"""MED-326: TOTP MFA for org admins - helpers, enrollment and the login MFA step."""

import hashlib
from datetime import datetime, timedelta, timezone as dt_timezone

import pyotp
import pytest
from django.core.cache import cache
from django.utils import timezone
from freezegun import freeze_time
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from authentication import mfa
from authentication.login_security import LoginSecurityService
from authentication.models import MfaDevice, RecoveryCode
from core.admin_utils import assign_org_admin
from core.models import CustomUser, Organization
from core.services.auth_tokens import build_user_refresh_token


pytestmark = pytest.mark.django_db

PASSWORD = "CurrentPassword123!"
START_URL = "/auth/mfa/enroll/start/"
CONFIRM_URL = "/auth/mfa/enroll/confirm/"
VERIFY_URL = "/auth/mfa/verify/"

# 15 seconds into a 30-second TOTP step, so the neighbouring steps are exactly 30 s away.
NOW = datetime(2026, 1, 1, 0, 0, 15, tzinfo=dt_timezone.utc)
NOW_TS = int(NOW.timestamp())


@pytest.fixture(autouse=True)
def clear_login_counters():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def organization():
    return Organization.objects.create(name="MFA Org", email_domain="mfa-org.com")


def make_user(organization, email, **extra):
    return CustomUser.objects.create_user(
        username=email.split("@")[0], email=email, password=PASSWORD,
        organization=organization, is_verified=True, **extra,
    )


@pytest.fixture
def member(organization):
    return make_user(organization, "member@mfa-org.com")


@pytest.fixture
def org_admin(organization):
    user = make_user(organization, "admin@mfa-org.com")
    assign_org_admin(user, organization)
    return user


@pytest.fixture
def enrolled_admin(org_admin):
    """An org admin who has finished MFA setup. Returns (user, secret, recovery_codes)."""
    secret = mfa.generate_secret()
    device = MfaDevice(user=org_admin, confirmed_at=timezone.now())
    device.set_secret(secret)
    device.save()
    return org_admin, secret, mfa.generate_recovery_codes(org_admin)


def jwt_client(user):
    """A signed-in client (e.g. the profile page), with a token issued before MFA."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {build_user_refresh_token(user).access_token}")
    return client


def login(user, password=PASSWORD):
    return APIClient().post("/auth/login/", {"email": user.email, "password": password}, format="json")


def verify(payload):
    return APIClient().post(VERIFY_URL, payload, format="json")


def wrong_code(secret):
    return "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"


# --- who needs MFA and how codes are checked ---------------------------------

def test_only_org_admins_require_mfa(org_admin, member, organization):
    superuser = make_user(organization, "root@mfa-org.com", is_superuser=True)

    assert mfa.requires_mfa(org_admin) is True
    assert mfa.requires_mfa(member) is False
    assert mfa.requires_mfa(superuser) is False


@freeze_time(NOW)
@pytest.mark.parametrize("offset", [-30, 0, 30])
def test_totp_accepts_current_and_adjacent_30s_steps(offset):
    secret = mfa.generate_secret()

    assert mfa.verify_totp(secret, pyotp.TOTP(secret).at(NOW_TS + offset)) is True


@freeze_time(NOW)
def test_totp_rejects_codes_outside_the_window_from_another_secret_or_malformed():
    secret = mfa.generate_secret()
    rejected = [
        pyotp.TOTP(secret).at(NOW_TS - 60),
        pyotp.TOTP(secret).at(NOW_TS + 60),
        pyotp.TOTP(mfa.generate_secret()).at(NOW_TS),
        "", None, "12345", "abcdef",
    ]

    assert [mfa.verify_totp(secret, code) for code in rejected] == [False] * len(rejected)


def test_secret_is_stored_encrypted(org_admin):
    secret = mfa.generate_secret()
    device = MfaDevice(user=org_admin)
    device.set_secret(secret)
    device.save()

    device.refresh_from_db()
    assert secret not in device.encrypted_secret
    assert device.get_secret() == secret


def test_recovery_codes_are_stored_as_keyed_hashes(org_admin):
    codes = mfa.generate_recovery_codes(org_admin)

    stored = set(RecoveryCode.objects.filter(user=org_admin).values_list("code_hash", flat=True))
    assert len(set(codes)) == len(stored) == mfa.RECOVERY_CODE_COUNT
    for code in codes:
        assert mfa.hash_recovery_code(code) in stored
        # Keyed hash: neither the code nor a plain SHA-256 of it is stored.
        assert code not in stored
        assert hashlib.sha256(code.replace("-", "").encode()).hexdigest() not in stored


def test_recovery_code_ignores_case_dashes_and_spaces(org_admin):
    code = mfa.generate_recovery_codes(org_admin)[0]

    assert mfa.consume_recovery_code(org_admin, f" {code.replace('-', '').upper()} ") is True


def test_wrong_or_another_users_recovery_code_is_rejected(org_admin, member):
    code = mfa.generate_recovery_codes(org_admin)[0]

    assert mfa.consume_recovery_code(org_admin, "00000-00000") is False
    assert mfa.consume_recovery_code(org_admin, "") is False
    assert mfa.consume_recovery_code(member, code) is False


# --- enrollment (signed in, e.g. from the profile page) ---------------------

def test_start_returns_secret_and_stores_pending_device(org_admin):
    response = jwt_client(org_admin).post(START_URL, format="json")

    assert response.status_code == 200
    assert response.data["otpauth_uri"].startswith("otpauth://totp/")
    device = MfaDevice.objects.get(user=org_admin)
    assert device.is_confirmed is False
    assert device.get_secret() == response.data["secret"]


def test_confirm_enrolls_and_returns_recovery_codes_only_once(org_admin):
    client = jwt_client(org_admin)
    secret = client.post(START_URL, format="json").data["secret"]

    response = client.post(CONFIRM_URL, {"code": pyotp.TOTP(secret).now()}, format="json")

    assert response.status_code == 200
    assert len(response.data["recovery_codes"]) == mfa.RECOVERY_CODE_COUNT
    assert AccessToken(response.data["token"]).payload["mfa_verified"] is True
    assert MfaDevice.objects.get(user=org_admin).is_confirmed is True
    # Once enrolled, neither endpoint hands out new codes or a new secret.
    again = client.post(CONFIRM_URL, {"code": pyotp.TOTP(secret).now()}, format="json")
    restart = client.post(START_URL, format="json")
    assert (again.status_code, restart.status_code) == (409, 409)
    assert "recovery_codes" not in again.data and "secret" not in restart.data
    assert MfaDevice.objects.get(user=org_admin).get_secret() == secret


def test_missing_or_wrong_code_keeps_device_pending_and_user_can_retry(org_admin):
    client = jwt_client(org_admin)
    secret = client.post(START_URL, format="json").data["secret"]

    missing = client.post(CONFIRM_URL, {}, format="json")
    wrong = client.post(CONFIRM_URL, {"code": wrong_code(secret)}, format="json")

    assert missing.status_code == 400
    assert wrong.status_code == 400
    assert wrong.data["errorCode"] == "MFA_INVALID_CODE"
    assert MfaDevice.objects.get(user=org_admin).is_confirmed is False
    assert not RecoveryCode.objects.filter(user=org_admin).exists()
    assert client.post(CONFIRM_URL, {"code": pyotp.TOTP(secret).now()}, format="json").status_code == 200


def test_confirm_without_starting_is_rejected(org_admin):
    response = jwt_client(org_admin).post(CONFIRM_URL, {"code": "123456"}, format="json")

    assert response.status_code == 400
    assert response.data["errorCode"] == "MFA_ENROLLMENT_NOT_STARTED"


@pytest.mark.parametrize("url", [START_URL, CONFIRM_URL])
def test_only_org_admins_can_enroll(member, url):
    assert jwt_client(member).post(url, {"code": "123456"}, format="json").status_code == 403
    assert APIClient().post(url, {"code": "123456"}, format="json").status_code == 401
    assert not MfaDevice.objects.filter(user=member).exists()


# --- login: password step ----------------------------------------------------

def test_member_login_is_unchanged(member):
    response = login(member)

    assert response.status_code == 200
    assert {"token", "refresh", "user"} <= set(response.data)
    assert "mfa_required" not in response.data


@pytest.mark.parametrize("enrolled", [True, False])
def test_admin_login_returns_mfa_challenge_instead_of_tokens(org_admin, enrolled):
    if enrolled:
        device = MfaDevice(user=org_admin, confirmed_at=timezone.now())
        device.set_secret(mfa.generate_secret())
        device.save()

    response = login(org_admin)

    assert response.status_code == 200
    assert response.data["mfa_required"] is True
    assert response.data["mfa_enrollment_required"] is (not enrolled)
    assert mfa.read_mfa_token(response.data["mfa_token"]) == org_admin
    assert "token" not in response.data and "refresh" not in response.data


def test_wrong_password_gets_no_mfa_token(enrolled_admin):
    user, _, _ = enrolled_admin

    response = login(user, password="WrongPassword123!")

    assert response.status_code == 401
    assert "mfa_token" not in response.data


# --- login: MFA step ---------------------------------------------------------

def test_correct_code_completes_login_with_mfa_marked_token(enrolled_admin):
    user, secret, _ = enrolled_admin

    response = verify({"mfa_token": login(user).data["mfa_token"], "code": pyotp.TOTP(secret).now()})

    assert response.status_code == 200
    assert AccessToken(response.data["token"]).payload["mfa_verified"] is True


def test_recovery_code_completes_login_only_once(enrolled_admin):
    user, _, recovery_codes = enrolled_admin

    first = verify({"mfa_token": login(user).data["mfa_token"], "recovery_code": recovery_codes[0]})
    second = verify({"mfa_token": login(user).data["mfa_token"], "recovery_code": recovery_codes[0]})

    assert first.status_code == 200
    assert AccessToken(first.data["token"]).payload["mfa_verified"] is True
    assert second.status_code == 400
    assert second.data["errorCode"] == "MFA_INVALID_CODE"


def test_wrong_codes_count_toward_login_backoff_across_new_logins(enrolled_admin):
    """Signing in again with the password must not give an attacker fresh guesses."""
    user, secret, _ = enrolled_admin

    for _ in range(LoginSecurityService.BACKOFF_THRESHOLD - 1):
        wrong = verify({"mfa_token": login(user).data["mfa_token"], "code": wrong_code(secret)})
        assert wrong.status_code == 400
        assert "token" not in wrong.data
    mfa_token = login(user).data["mfa_token"]
    blocked = verify({"mfa_token": mfa_token, "code": wrong_code(secret)})
    after = verify({"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})

    assert blocked.status_code == 429
    assert blocked.data["errorCode"] == LoginSecurityService.TOO_MANY_ATTEMPTS_CODE
    assert after.status_code == 429


@pytest.mark.parametrize("case", ["expired", "tampered", "password_changed"])
def test_invalid_mfa_token_is_rejected(enrolled_admin, case):
    user, secret, _ = enrolled_admin
    mfa_token = login(user).data["mfa_token"]
    later = timezone.now()
    if case == "expired":
        later += timedelta(seconds=mfa.MFA_TOKEN_MAX_AGE_SECONDS + 1)
    elif case == "tampered":
        mfa_token = mfa_token[:-2] + "xx"
    else:
        user.auth_token_version += 1
        user.save(update_fields=["auth_token_version"])

    with freeze_time(later):
        verified = verify({"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()})
        started = APIClient().post(START_URL, {"mfa_token": mfa_token}, format="json")

    assert verified.status_code == 401
    assert verified.data["errorCode"] == "MFA_TOKEN_INVALID"
    assert started.status_code == 401


@pytest.mark.parametrize(
    "codes", [{}, {"code": "123456", "recovery_code": "abcde-12345"}], ids=["neither", "both"]
)
def test_verify_needs_exactly_one_of_code_or_recovery_code(enrolled_admin, codes):
    user, _, _ = enrolled_admin

    response = verify({"mfa_token": login(user).data["mfa_token"], **codes})

    assert response.status_code == 400
    assert "token" not in response.data


def test_unenrolled_admin_cannot_skip_enrollment_via_verify(org_admin):
    response = verify({"mfa_token": login(org_admin).data["mfa_token"], "code": "123456"})

    assert response.status_code == 400
    assert response.data["errorCode"] == "MFA_ENROLLMENT_REQUIRED"


def test_unenrolled_admin_enrolls_from_the_login_step_and_is_signed_in(org_admin):
    mfa_token = login(org_admin).data["mfa_token"]

    secret = APIClient().post(START_URL, {"mfa_token": mfa_token}, format="json").data["secret"]
    confirmed = APIClient().post(
        CONFIRM_URL, {"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()}, format="json"
    )

    assert confirmed.status_code == 200
    assert len(confirmed.data["recovery_codes"]) == mfa.RECOVERY_CODE_COUNT
    assert AccessToken(confirmed.data["token"]).payload["mfa_verified"] is True
    assert login(org_admin).data["mfa_enrollment_required"] is False


def test_password_alone_cannot_replace_an_enrolled_admins_authenticator(enrolled_admin):
    """Someone with only the password must not be able to register their own phone."""
    user, secret, _ = enrolled_admin

    response = APIClient().post(START_URL, {"mfa_token": login(user).data["mfa_token"]}, format="json")

    assert response.status_code == 409
    assert "secret" not in response.data
    assert MfaDevice.objects.get(user=user).get_secret() == secret
