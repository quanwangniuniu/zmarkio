"""MED-326: AuthorizationMiddleware blocks org-admin privileges until MFA is passed."""

from datetime import timedelta

import pyotp
import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from access_control.models import UserRole
from authentication import mfa
from authentication.models import MfaDevice
from core.admin_utils import assign_org_admin
from core.models import CustomUser, Organization, OrganizationMembership, Role
from core.services.auth_tokens import build_user_refresh_token
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context


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


# --- every way admin records can be stored ----------------------------------
#
# Admin status lives in two places: A = an 'admin' OrganizationMembership
# (public schema only) and B = a level-2 UserRole (a tenant table, so it can end
# up in the org's own schema or in public, depending on the search_path when it
# was written). Any admin record must require MFA, whichever combination exists.

def give_membership_admin(user, organization):
    OrganizationMembership.objects.create(user=user, organization=organization, role="admin", is_active=True)


def give_level2_role(user, organization):
    role, _ = Role.objects.get_or_create(
        organization=organization, name="Organization Admin", defaults={"level": 2}
    )
    UserRole.objects.create(user=user, role=role)


ADMIN_RECORD_CASES = {
    # case id: (has A, where B is, needs MFA)
    "A_and_B_in_org_schema": (True, "org", True),
    "A_and_B_in_public": (True, "public", True),
    "A_only": (True, None, True),
    "B_in_org_schema_only": (False, "org", True),
    "B_in_public_only": (False, "public", True),
    "no_admin_records": (False, None, False),
}


@pytest.mark.parametrize(
    "has_membership, role_schema, needs_mfa",
    list(ADMIN_RECORD_CASES.values()),
    ids=list(ADMIN_RECORD_CASES),
)
def test_every_admin_record_combination_requires_mfa(
    organization, has_membership, role_schema, needs_mfa
):
    user = make_user(organization, "case@mfa-gate.com")
    if has_membership:
        give_membership_admin(user, organization)
    if role_schema == "org":
        with tenant_schema_context(slug_to_schema_name(organization.slug)):
            give_level2_role(user, organization)
    elif role_schema == "public":
        give_level2_role(user, organization)

    response = client_with(pre_mfa_token(user)).get(ADMIN_API)

    assert mfa.requires_mfa(user) is needs_mfa
    if needs_mfa:
        assert response.status_code == 403
        assert response.json()["errorCode"] == "MFA_REQUIRED"
    else:
        assert response.json().get("errorCode") != "MFA_REQUIRED"


def test_expired_level2_role_still_requires_mfa(organization):
    """is_org_admin() ignores expiry, so the MFA gate must too."""
    user = make_user(organization, "expired@mfa-gate.com")
    with tenant_schema_context(slug_to_schema_name(organization.slug)):
        role, _ = Role.objects.get_or_create(
            organization=organization, name="Organization Admin", defaults={"level": 2}
        )
        UserRole.objects.create(user=user, role=role, valid_to=timezone.now() - timedelta(days=1))

    response = client_with(pre_mfa_token(user)).get(ADMIN_API)

    assert response.status_code == 403
    assert response.json()["errorCode"] == "MFA_REQUIRED"


def test_admin_of_another_org_requires_mfa(organization):
    """Admin of org A while org B is selected: admin access to A still needs MFA."""
    other_org = Organization.objects.create(name="MFA Other Org", email_domain="mfa-other.com")
    user = make_user(organization, "cross@mfa-gate.com")
    give_membership_admin(user, other_org)

    response = client_with(pre_mfa_token(user)).get(ADMIN_API)

    assert mfa.requires_mfa(user) is True
    assert response.status_code == 403
    assert response.json()["errorCode"] == "MFA_REQUIRED"


# --- not affected ------------------------------------------------------------

def test_superuser_is_not_mfa_gated(organization):
    superuser = make_user(organization, "root@mfa-gate.com", is_superuser=True)

    response = client_with(pre_mfa_token(superuser)).get(ADMIN_API)

    assert response.json().get("errorCode") != "MFA_REQUIRED"
