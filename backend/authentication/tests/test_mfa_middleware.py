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
        username=email.split("@")[0], email=email, password=PASSWORD,
        organization=organization, is_verified=True, **extra,
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


@pytest.mark.parametrize("enrolled", [False, True], ids=["not_enrolled", "enrolled"])
def test_admin_with_pre_mfa_token_is_blocked(org_admin, enrolled):
    if enrolled:
        enroll(org_admin)

    response = client_with(pre_mfa_token(org_admin)).get(ADMIN_API)

    assert response.status_code == 403
    assert response.json()["errorCode"] == "MFA_REQUIRED"
    assert response.json()["mfa_enrollment_required"] is (not enrolled)


def test_admin_who_passed_mfa_at_login_keeps_admin_access(org_admin):
    secret = enroll(org_admin)
    mfa_token = APIClient().post(
        "/auth/login/", {"email": org_admin.email, "password": PASSWORD}, format="json"
    ).data["mfa_token"]
    token = APIClient().post(
        "/auth/mfa/verify/", {"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()}, format="json"
    ).data["token"]

    assert client_with(token).get(ADMIN_API).status_code == 200


def test_admin_without_mfa_finishes_setup_from_profile_and_regains_access(org_admin):
    """Account and MFA endpoints stay reachable, so an old session can enroll."""
    client = client_with(pre_mfa_token(org_admin))
    assert client.get("/auth/me/").status_code == 200

    secret = client.post("/auth/mfa/enroll/start/", format="json").data["secret"]
    new_token = client.post(
        "/auth/mfa/enroll/confirm/", {"code": pyotp.TOTP(secret).now()}, format="json"
    ).data["token"]

    assert client_with(new_token).get(ADMIN_API).status_code == 200


# --- every way an admin record can be stored --------------------------------
#
# Admin status lives in two places: A = an 'admin' OrganizationMembership
# (public schema only) and B = a level-2 UserRole (a tenant table, so it can end
# up in the org's own schema or in public, depending on the search_path when it
# was written). Any admin record must require MFA, whichever combination exists.

def give_membership_admin(user, organization):
    OrganizationMembership.objects.create(user=user, organization=organization, role="admin", is_active=True)


def give_level2_role(user, organization, schema, schema_org=None, **extra):
    def create():
        role, _ = Role.objects.get_or_create(
            organization=organization, name="Organization Admin", defaults={"level": 2}
        )
        UserRole.objects.create(user=user, role=role, **extra)

    if schema == "org":
        with tenant_schema_context(slug_to_schema_name((schema_org or organization).slug)):
            create()
    else:
        create()


def make_superuser(user):
    user.is_superuser = True
    user.save(update_fields=["is_superuser"])


def other_org():
    return Organization.objects.create(name="MFA Other Org", email_domain="mfa-other.com")


ADMIN_RECORD_CASES = {
    "A_and_B_in_org_schema": (lambda u, o: (give_membership_admin(u, o), give_level2_role(u, o, "org")), True),
    "A_and_B_in_public": (lambda u, o: (give_membership_admin(u, o), give_level2_role(u, o, "public")), True),
    "A_only": (lambda u, o: give_membership_admin(u, o), True),
    "B_in_org_schema_only": (lambda u, o: give_level2_role(u, o, "org"), True),
    "B_in_public_only": (lambda u, o: give_level2_role(u, o, "public"), True),
    # is_org_admin() ignores expiry, so the MFA gate must too.
    "expired_B_in_org_schema": (
        lambda u, o: give_level2_role(u, o, "org", valid_to=timezone.now() - timedelta(days=1)), True
    ),
    # A level-2 role in this org's schema whose Role names another org (historical data).
    "B_in_org_schema_for_another_org": (lambda u, o: give_level2_role(u, other_org(), "org", schema_org=o), True),
    # Admin of another org while this org is selected (get_org_admin_org_ids honours it).
    "A_in_another_org": (lambda u, o: give_membership_admin(u, other_org()), True),
    "no_admin_records": (lambda u, o: None, False),
    "superuser_without_admin_records": (lambda u, o: make_superuser(u), False),
}


@pytest.mark.parametrize("set_up, needs_mfa", list(ADMIN_RECORD_CASES.values()), ids=list(ADMIN_RECORD_CASES))
def test_every_admin_record_requires_mfa(organization, set_up, needs_mfa):
    user = make_user(organization, "case@mfa-gate.com")
    set_up(user, organization)

    response = client_with(pre_mfa_token(user)).get(ADMIN_API)

    # Login and enrollment (requires_mfa) and the middleware apply the same rule.
    assert mfa.requires_mfa(user) is needs_mfa
    if needs_mfa:
        assert response.status_code == 403
        assert response.json()["errorCode"] == "MFA_REQUIRED"
    else:
        assert response.json().get("errorCode") != "MFA_REQUIRED"
