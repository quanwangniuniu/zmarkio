from datetime import timedelta

import pyotp
import pytest
from django.core.cache import cache
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from authentication import mfa
from authentication.login_security import LoginSecurityService
from authentication.models import MfaDevice
from core.admin_utils import assign_org_admin
from core.models import CustomUser, Organization


pytestmark = pytest.mark.django_db

LOGIN_URL = "/auth/login/"
VERIFY_URL = "/auth/mfa/verify/"
PASSWORD = "CurrentPassword123!"


@pytest.fixture(autouse=True)
def clear_login_counters():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def organization():
    return Organization.objects.create(name="MFA Login Org", email_domain="mfa-login.com")


def make_user(organization, email):
    return CustomUser.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password=PASSWORD,
        organization=organization,
        is_verified=True,
    )


@pytest.fixture
def member(organization):
    return make_user(organization, "member@mfa-login.com")


@pytest.fixture
def org_admin(organization):
    user = make_user(organization, "admin@mfa-login.com")
    assign_org_admin(user, organization)
    return user


@pytest.fixture
def enrolled_admin(org_admin):
    """An org admin who has finished MFA setup. Returns (user, secret, recovery_codes)."""
    secret = mfa.generate_secret()
    device = MfaDevice(user=org_admin, confirmed_at=timezone.now())
    device.set_secret(secret)
    device.save()
    recovery_codes = mfa.generate_recovery_codes(org_admin)
    return org_admin, secret, recovery_codes


def login(user, password=PASSWORD):
    return APIClient().post(LOGIN_URL, {"email": user.email, "password": password}, format="json")


def verify(payload):
    return APIClient().post(VERIFY_URL, payload, format="json")


# --- /auth/login/ ------------------------------------------------------------

def test_member_login_is_unchanged(member):
    response = login(member)

    assert response.status_code == 200
    assert response.data["message"] == "Login successful"
    assert {"token", "refresh", "user"} <= set(response.data)
    assert "mfa_required" not in response.data
    assert "mfa_token" not in response.data
    assert "mfa_verified" not in AccessToken(response.data["token"]).payload


def test_enrolled_admin_gets_mfa_challenge_instead_of_tokens(enrolled_admin):
    user, _, _ = enrolled_admin

    response = login(user)

    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert response.data["mfa_enrollment_required"] is False
    assert mfa.read_mfa_token(response.data["mfa_token"]) == user
    assert "token" not in response.data
    assert "refresh" not in response.data


def test_unenrolled_admin_is_sent_to_enrollment(org_admin):
    response = login(org_admin)

    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert response.data["mfa_enrollment_required"] is True
    assert "token" not in response.data


def test_admin_with_wrong_password_gets_no_mfa_token(enrolled_admin):
    user, _, _ = enrolled_admin

    response = login(user, password="WrongPassword123!")

    assert response.status_code == 401
    assert response.data["errorCode"] == "INVALID_PASSWORD"
    assert "mfa_token" not in response.data


# --- /auth/mfa/verify/ -------------------------------------------------------

def test_correct_code_completes_login_with_mfa_marked_token(enrolled_admin):
    user, secret, _ = enrolled_admin
    mfa_token = login(user).data["mfa_token"]

    response = verify({"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})

    assert response.status_code == 200
    assert response.data["message"] == "Login successful"
    assert AccessToken(response.data["token"]).payload["mfa_verified"] is True
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['token']}")
    assert client.get("/auth/me/").status_code == 200


def test_recovery_code_completes_login_once(enrolled_admin):
    user, _, recovery_codes = enrolled_admin

    first = verify({"mfa_token": login(user).data["mfa_token"], "recovery_code": recovery_codes[0]})
    second = verify({"mfa_token": login(user).data["mfa_token"], "recovery_code": recovery_codes[0]})

    assert first.status_code == 200
    assert AccessToken(first.data["token"]).payload["mfa_verified"] is True
    assert second.status_code == 400
    assert second.data["errorCode"] == "MFA_INVALID_CODE"


def test_wrong_code_is_rejected_without_tokens(enrolled_admin):
    user, secret, _ = enrolled_admin
    wrong_code = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"

    response = verify({"mfa_token": login(user).data["mfa_token"], "code": wrong_code})

    assert response.status_code == 400
    assert response.data["errorCode"] == "MFA_INVALID_CODE"
    assert "token" not in response.data


def test_repeated_wrong_codes_trigger_login_backoff(enrolled_admin):
    user, secret, _ = enrolled_admin
    mfa_token = login(user).data["mfa_token"]

    for _ in range(LoginSecurityService.BACKOFF_THRESHOLD - 1):
        assert verify({"mfa_token": mfa_token, "code": "abcdef"}).status_code == 400
    response = verify({"mfa_token": mfa_token, "code": "abcdef"})
    after_backoff = verify({"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})

    assert response.status_code == 429
    assert response.data["errorCode"] == LoginSecurityService.TOO_MANY_ATTEMPTS_CODE
    assert after_backoff.status_code == 429
    assert "token" not in after_backoff.data


def test_password_step_does_not_reset_wrong_code_counter(enrolled_admin):
    """Logging in again with the password must not give an attacker fresh guesses."""
    user, _, _ = enrolled_admin

    for _ in range(LoginSecurityService.BACKOFF_THRESHOLD - 1):
        verify({"mfa_token": login(user).data["mfa_token"], "code": "abcdef"})
    response = verify({"mfa_token": login(user).data["mfa_token"], "code": "abcdef"})

    assert response.status_code == 429


def test_expired_mfa_token_is_rejected(enrolled_admin):
    user, secret, _ = enrolled_admin
    mfa_token = login(user).data["mfa_token"]

    with freeze_time(timezone.now() + timedelta(seconds=mfa.MFA_TOKEN_MAX_AGE_SECONDS + 1)):
        response = verify({"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})

    assert response.status_code == 401
    assert response.data["errorCode"] == "MFA_TOKEN_INVALID"


def test_tampered_mfa_token_is_rejected(enrolled_admin):
    user, secret, _ = enrolled_admin
    mfa_token = login(user).data["mfa_token"]

    response = verify({"mfa_token": mfa_token[:-2] + "xx", "code": pyotp.TOTP(secret).now()})

    assert response.status_code == 401
    assert response.data["errorCode"] == "MFA_TOKEN_INVALID"


def test_password_change_invalidates_outstanding_mfa_token(enrolled_admin):
    user, secret, _ = enrolled_admin
    mfa_token = login(user).data["mfa_token"]
    user.auth_token_version += 1
    user.save(update_fields=["auth_token_version"])

    response = verify({"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})

    assert response.status_code == 401


def test_unenrolled_admin_cannot_skip_enrollment_via_verify(org_admin):
    mfa_token = login(org_admin).data["mfa_token"]

    response = verify({"mfa_token": mfa_token, "code": "123456"})

    assert response.status_code == 400
    assert response.data["errorCode"] == "MFA_ENROLLMENT_REQUIRED"


@pytest.mark.parametrize(
    "extra",
    [{}, {"code": "123456", "recovery_code": "abcde-12345"}],
)
def test_verify_needs_exactly_one_of_code_or_recovery_code(enrolled_admin, extra):
    user, _, _ = enrolled_admin

    response = verify({"mfa_token": login(user).data["mfa_token"], **extra})

    assert response.status_code == 400
