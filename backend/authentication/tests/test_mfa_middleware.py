"""MED-326: AuthorizationMiddleware blocks org-admin privileges until MFA is passed."""

import pyotp
import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from authentication import mfa
from authentication.models import MfaDevice
from core.admin_utils import assign_org_admin
from core.models import CustomUser, Organization
from core.services.auth_tokens import build_user_refresh_token


pytestmark = pytest.mark.django_db

# An org-admin-only API (IsOrgAdmin), outside the MFA exempt list.
ADMIN_API = "/api/core/admin/organizations/"
PASSWORD = "CurrentPassword123!"


@pytest.fixture(autouse=True)
def clear_login_counters():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def organization():
    return Organization.objects.create(name="MFA Gate Org", email_domain="mfa-gate.com")


def make_user(organization, email, **extra):
    return CustomUser.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password=PASSWORD,
        organization=organization,
        is_verified=True,
        **extra,
    )


@pytest.fixture
def org_admin(organization):
    user = make_user(organization, "admin@mfa-gate.com")
    assign_org_admin(user, organization)
    return user


def enroll(user):
    secret = mfa.generate_secret()
    device = MfaDevice(user=user, confirmed_at=timezone.now())
    device.set_secret(secret)
    device.save()
    return secret


def client_with(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def pre_mfa_token(user):
    """A token issued without MFA, e.g. a session that started before MED-326 shipped."""
    return str(build_user_refresh_token(user).access_token)


# --- blocked -----------------------------------------------------------------

def test_admin_without_mfa_is_blocked_and_sent_to_setup(org_admin):
    response = client_with(pre_mfa_token(org_admin)).get(ADMIN_API)

    assert response.status_code == 403
    assert response.json()["errorCode"] == "MFA_REQUIRED"
    assert response.json()["mfa_enrollment_required"] is True


def test_enrolled_admin_with_pre_mfa_token_is_blocked_and_told_to_sign_in_again(org_admin):
    enroll(org_admin)

    response = client_with(pre_mfa_token(org_admin)).get(ADMIN_API)

    assert response.status_code == 403
    assert response.json()["errorCode"] == "MFA_REQUIRED"
    assert response.json()["mfa_enrollment_required"] is False


# --- allowed -----------------------------------------------------------------

def test_admin_who_passed_mfa_at_login_keeps_admin_access(org_admin):
    secret = enroll(org_admin)
    mfa_token = APIClient().post(
        "/auth/login/", {"email": org_admin.email, "password": PASSWORD}, format="json"
    ).data["mfa_token"]
    token = APIClient().post(
        "/auth/mfa/verify/", {"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()}, format="json"
    ).data["token"]

    response = client_with(token).get(ADMIN_API)

    assert response.status_code == 200


def test_admin_without_mfa_can_still_reach_account_and_mfa_endpoints(org_admin):
    client = client_with(pre_mfa_token(org_admin))

    assert client.get("/auth/me/").status_code == 200
    assert client.post("/auth/mfa/enroll/start/", format="json").status_code == 200


def test_admin_finishes_setup_from_profile_and_regains_access(org_admin):
    """The profile-page path: old token -> enroll -> new MFA-verified token."""
    client = client_with(pre_mfa_token(org_admin))
    secret = client.post("/auth/mfa/enroll/start/", format="json").data["secret"]
    new_token = client.post(
        "/auth/mfa/enroll/confirm/", {"code": pyotp.TOTP(secret).now()}, format="json"
    ).data["token"]

    response = client_with(new_token).get(ADMIN_API)

    assert response.status_code == 200


# --- not affected ------------------------------------------------------------

def test_member_requests_are_not_mfa_gated(organization):
    member = make_user(organization, "member@mfa-gate.com")

    response = client_with(pre_mfa_token(member)).get(ADMIN_API)

    assert response.status_code == 403
    assert response.json().get("errorCode") != "MFA_REQUIRED"


def test_superuser_is_not_mfa_gated(organization):
    superuser = make_user(organization, "root@mfa-gate.com", is_superuser=True)

    response = client_with(pre_mfa_token(superuser)).get(ADMIN_API)

    assert response.json().get("errorCode") != "MFA_REQUIRED"
