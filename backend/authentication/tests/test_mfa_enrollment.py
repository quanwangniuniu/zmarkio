import pyotp
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from authentication import mfa
from authentication.models import MfaDevice, RecoveryCode
from core.admin_utils import assign_org_admin
from core.models import CustomUser, Organization
from core.services.auth_tokens import build_user_refresh_token


pytestmark = pytest.mark.django_db

START_URL = "/auth/mfa/enroll/start/"
CONFIRM_URL = "/auth/mfa/enroll/confirm/"


@pytest.fixture
def organization():
    return Organization.objects.create(name="MFA Enroll Org", email_domain="mfa-enroll.com")


def make_user(organization, email):
    return CustomUser.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="CurrentPassword123!",
        organization=organization,
        is_verified=True,
    )


@pytest.fixture
def org_admin(organization):
    user = make_user(organization, "admin@mfa-enroll.com")
    assign_org_admin(user, organization)
    return user


@pytest.fixture
def member(organization):
    return make_user(organization, "member@mfa-enroll.com")


def client_for(user):
    """A client that sends a real JWT, the same way the frontend does."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {build_user_refresh_token(user).access_token}")
    return client


def start(client):
    return client.post(START_URL, format="json")


def confirm(client, code):
    return client.post(CONFIRM_URL, {"code": code}, format="json")


# --- start -------------------------------------------------------------------

def test_start_returns_secret_and_qr_uri_and_stores_pending_device(org_admin):
    response = start(client_for(org_admin))

    assert response.status_code == 200
    secret = response.data["secret"]
    assert response.data["otpauth_uri"] == mfa.provisioning_uri(org_admin, secret)
    device = MfaDevice.objects.get(user=org_admin)
    assert device.is_confirmed is False
    assert device.encrypted_secret != secret
    assert device.get_secret() == secret


def test_starting_again_before_confirming_replaces_the_secret(org_admin):
    client = client_for(org_admin)
    first_secret = start(client).data["secret"]

    second_secret = start(client).data["secret"]

    assert second_secret != first_secret
    assert MfaDevice.objects.filter(user=org_admin).count() == 1
    assert MfaDevice.objects.get(user=org_admin).get_secret() == second_secret


# --- confirm -----------------------------------------------------------------

def test_confirm_with_correct_code_enrolls_and_returns_recovery_codes_once(org_admin):
    client = client_for(org_admin)
    secret = start(client).data["secret"]

    response = confirm(client, pyotp.TOTP(secret).now())

    assert response.status_code == 200
    codes = response.data["recovery_codes"]
    assert len(codes) == mfa.RECOVERY_CODE_COUNT
    assert MfaDevice.objects.get(user=org_admin).is_confirmed is True
    assert RecoveryCode.objects.filter(user=org_admin).count() == mfa.RECOVERY_CODE_COUNT
    assert mfa.consume_recovery_code(org_admin, codes[0]) is True


def test_confirm_replaces_the_session_token_with_an_mfa_verified_one(org_admin):
    """An admin who was already signed in gets a token the MFA gate will accept."""
    client = client_for(org_admin)
    secret = start(client).data["secret"]

    response = confirm(client, pyotp.TOTP(secret).now())

    assert AccessToken(response.data["token"]).payload["mfa_verified"] is True
    assert "refresh" in response.data


def test_confirm_with_wrong_code_keeps_device_pending(org_admin):
    client = client_for(org_admin)
    secret = start(client).data["secret"]
    wrong_code = "000000" if pyotp.TOTP(secret).now() != "000000" else "111111"

    response = confirm(client, wrong_code)

    assert response.status_code == 400
    assert response.data["errorCode"] == "MFA_INVALID_CODE"
    assert MfaDevice.objects.get(user=org_admin).is_confirmed is False
    assert not RecoveryCode.objects.filter(user=org_admin).exists()


def test_user_can_retry_after_a_wrong_code(org_admin):
    client = client_for(org_admin)
    secret = start(client).data["secret"]
    confirm(client, "abcdef")

    response = confirm(client, pyotp.TOTP(secret).now())

    assert response.status_code == 200


def test_confirm_without_starting_is_rejected(org_admin):
    response = confirm(client_for(org_admin), "123456")

    assert response.status_code == 400
    assert response.data["errorCode"] == "MFA_ENROLLMENT_NOT_STARTED"


def test_confirm_requires_a_code(org_admin):
    client = client_for(org_admin)
    start(client)

    response = client.post(CONFIRM_URL, {}, format="json")

    assert response.status_code == 400
    assert "code" in response.data


# --- after enrollment --------------------------------------------------------

def test_enrolled_admin_cannot_start_again_or_read_the_secret(org_admin):
    client = client_for(org_admin)
    secret = start(client).data["secret"]
    confirm(client, pyotp.TOTP(secret).now())

    response = start(client)

    assert response.status_code == 409
    assert response.data["errorCode"] == "MFA_ALREADY_ENROLLED"
    assert "secret" not in response.data
    assert MfaDevice.objects.get(user=org_admin).get_secret() == secret


def test_enrolled_admin_cannot_confirm_again_to_get_new_recovery_codes(org_admin):
    client = client_for(org_admin)
    secret = start(client).data["secret"]
    first_codes = confirm(client, pyotp.TOTP(secret).now()).data["recovery_codes"]

    response = confirm(client, pyotp.TOTP(secret).now())

    assert response.status_code == 409
    assert response.data["errorCode"] == "MFA_ALREADY_ENROLLED"
    assert "recovery_codes" not in response.data
    assert mfa.consume_recovery_code(org_admin, first_codes[0]) is True


# --- who can enroll ----------------------------------------------------------

@pytest.mark.parametrize("url", [START_URL, CONFIRM_URL])
def test_regular_member_cannot_enroll(member, url):
    response = client_for(member).post(url, {"code": "123456"}, format="json")

    assert response.status_code == 403
    assert not MfaDevice.objects.filter(user=member).exists()


@pytest.mark.parametrize("url", [START_URL, CONFIRM_URL])
def test_anonymous_request_is_rejected(url):
    response = APIClient().post(url, {"code": "123456"}, format="json")

    assert response.status_code == 401
