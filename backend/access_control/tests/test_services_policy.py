"""
Focused tests for the declarative authorization policy registry
added to access_control.services, plus the middleware behaviors that
registry is responsible for (zero-role grace, and resolution feeding the
org-admin/superuser bypass checks).

This file is intentionally separate from test_middleware.py /
test_authorization_logic.py (which already cover the ASSET/CAMPAIGN
role-permission matrix end-to-end through process_view) rather than merging
into either — it targets the new services.py functions directly (pure-unit
level for resolve_module_and_action) plus the one middleware behavior
(zero-role grace) that had no dedicated test anywhere in this app before the
policy registry was added.
"""
import pytest
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, RequestFactory, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from access_control import services as access_control_services
from access_control.middleware.authorization import AuthorizationMiddleware
from access_control.tests.test_urls import dummy_view


# ---------------------------------------------------------------------------
# 1. resolve_module_and_action — pure function, no DB required.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path, method, expected",
    [
        # ASSET: plain GET/POST/DELETE map through METHOD_ACTION_MAP.
        ("/api/assets/list/", "GET", ("ASSET", "VIEW")),
        ("/api/assets/1/delete/", "DELETE", ("ASSET", "DELETE")),
        ("/api/assets/1/edit/", "POST", ("ASSET", "EDIT")),
        ("/api/assets/1/edit/", "PUT", ("ASSET", "EDIT")),
        # ASSET: 4th path segment "export" forces EXPORT regardless of method.
        ("/api/assets/1/export/", "GET", ("ASSET", "EXPORT")),
        ("/api/assets/1/export/", "POST", ("ASSET", "EXPORT")),
        # CAMPAIGN: plain create/edit map through METHOD_ACTION_MAP.
        ("/api/campaigns/create/", "POST", ("CAMPAIGN", "EDIT")),
        ("/api/campaigns/1/edit/", "PUT", ("CAMPAIGN", "EDIT")),
        # CAMPAIGN: 4th path segment "approve" forces APPROVE regardless of method.
        ("/api/campaigns/1/approve/", "PUT", ("CAMPAIGN", "APPROVE")),
        ("/api/campaigns/1/approve/", "GET", ("CAMPAIGN", "APPROVE")),
        # PATCH with no keyword override falls back to METHOD_ACTION_MAP["PATCH"].
        ("/api/assets/1/", "PATCH", ("ASSET", "APPROVE")),
        # Unknown module segment -> no module, no action.
        ("/api/unknown/1/", "GET", (None, None)),
        # Not under /api/ at all -> no module, no action.
        ("/health/", "GET", (None, None)),
        ("/", "GET", (None, None)),
        # Module resolves but method has no entry in METHOD_ACTION_MAP ->
        # module_key set, action_key None.
        ("/api/assets/list/", "OPTIONS", ("ASSET", None)),
        # Short path (module segment only, no 3rd/4th segment) still resolves
        # the module and falls back to METHOD_ACTION_MAP.
        ("/api/assets/", "GET", ("ASSET", "VIEW")),
        # A 4th-segment value that merely contains "approve"/"export" as a
        # substring (not an exact segment match) must NOT trigger the
        # keyword override.
        ("/api/campaigns/1/disapprove/", "GET", ("CAMPAIGN", "VIEW")),
    ],
)
def test_resolve_module_and_action(path, method, expected):
    assert access_control_services.resolve_module_and_action(path, method) == expected


# ---------------------------------------------------------------------------
# 2. Middleware behavior: zero-role grace period.
#
# No existing test file in this app exercised this path directly: a user
# with ZERO UserRole rows at all (not merely a non-matching or expired role)
# hitting a real module/action gate must be allowed through, per the
# "grace period" comment in process_view. This is explicitly preserved,
# unchanged, by the registry refactor.
# ---------------------------------------------------------------------------

@override_settings(ROOT_URLCONF='access_control.tests.test_urls')
class ZeroRoleGraceBehaviorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Organization
        from core.services.tenant import slug_to_schema_name

        cls.org = Organization.objects.create(name="ZeroRoleGraceOrg")
        _schema = slug_to_schema_name(cls.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {_schema}, public')

        User = get_user_model()
        cls.user = User.objects.create_user(username="freshuser", email="freshuser@example.com", password="pw")

        cls.factory = RequestFactory()
        cls.middleware = AuthorizationMiddleware()

        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

    def setUp(self):
        from core.services.tenant import slug_to_schema_name
        _schema = slug_to_schema_name(self.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {_schema}, public')

    def tearDown(self):
        super().tearDown()
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

    def test_zero_role_user_allowed_on_asset_view(self):
        req = self.factory.get('/api/assets/list/')
        req.user = self.user
        self.assertIsNone(self.middleware.process_view(req, dummy_view, (), {}))

    def test_zero_role_user_allowed_on_campaign_approve(self):
        req = self.factory.put('/api/campaigns/1/approve/')
        req.user = self.user
        self.assertIsNone(self.middleware.process_view(req, dummy_view, (), {}))

    def test_zero_role_user_allowed_on_asset_delete(self):
        req = self.factory.delete('/api/assets/1/delete/')
        req.user = self.user
        self.assertIsNone(self.middleware.process_view(req, dummy_view, (), {}))

    def test_anonymous_user_still_skipped_before_any_role_check(self):
        from django.contrib.auth.models import AnonymousUser

        req = self.factory.get('/api/assets/list/')
        req.user = AnonymousUser()
        self.assertIsNone(self.middleware.process_view(req, dummy_view, (), {}))


# ---------------------------------------------------------------------------
# 3. Additional decision-branch coverage: an expired
# org-admin role (must not bypass), fail-open on a permission-query
# exception (proved with a user whose real role would otherwise be denied,
# so a None result here cannot be mistaken for the zero-role grace path),
# and the two "no gate" branches (an unmapped URL, and a method with no
# resolved action on a mapped module). None of these branches had any test
# anywhere in this app before the policy registry was added.
# ---------------------------------------------------------------------------

@override_settings(ROOT_URLCONF='access_control.tests.test_urls')
class AdditionalDecisionBranchCoverageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Organization, Role, Permission
        from core.services.tenant import slug_to_schema_name
        from access_control.models import RolePermission, UserRole

        cls.org = Organization.objects.create(name="AdditionalBranchOrg")
        _schema = slug_to_schema_name(cls.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {_schema}, public')

        cls.perm_view_asset, _ = Permission.objects.get_or_create(module="ASSET", action="VIEW")
        cls.perm_view_campaign, _ = Permission.objects.get_or_create(module="CAMPAIGN", action="VIEW")

        # Level-2 (org-admin) role, but the only UserRole grant of it below
        # is already expired -- must not grant the org-admin bypass.
        cls.role_org_admin = Role.objects.create(organization=cls.org, name="ExpiredOrgAdmin", level=2)

        # A real, currently-valid role that grants CAMPAIGN/VIEW, not
        # ASSET/VIEW. A user holding only this role has "a role" (so the
        # zero-role grace period does not apply) but no matching permission
        # for an ASSET/VIEW request -- normally denied with 403.
        cls.role_no_asset_view = Role.objects.create(organization=cls.org, name="CampaignViewerOnly", level=1)
        RolePermission.objects.create(role=cls.role_no_asset_view, permission=cls.perm_view_campaign)

        User = get_user_model()

        cls.user_expired_admin = User.objects.create_user(
            username="expiredadmin", email="expiredadmin@example.com", password="pw"
        )
        now = timezone.now()
        UserRole.objects.create(
            user=cls.user_expired_admin,
            role=cls.role_org_admin,
            valid_from=now - timedelta(days=10),
            valid_to=now - timedelta(days=1),
        )

        cls.user_mismatched_role = User.objects.create_user(
            username="mismatched", email="mismatched@example.com", password="pw"
        )
        UserRole.objects.create(user=cls.user_mismatched_role, role=cls.role_no_asset_view, valid_from=now)

        cls.user_plain = User.objects.create_user(
            username="plainuser2", email="plainuser2@example.com", password="pw"
        )

        cls.factory = RequestFactory()
        cls.middleware = AuthorizationMiddleware()

        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

    def setUp(self):
        from core.services.tenant import slug_to_schema_name
        _schema = slug_to_schema_name(self.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {_schema}, public')

    def tearDown(self):
        super().tearDown()
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

    def test_expired_org_admin_role_does_not_bypass_and_denies_without_permission(self):
        # An expired level-2 role must not grant the org-admin bypass. This
        # user's only role is that expired one, so "any role" is still True
        # (expired rows still count for the zero-role grace check) and the
        # request must be denied with 403, not silently allowed through
        # either bypass path.
        req = self.factory.get('/api/assets/list/')
        req.user = self.user_expired_admin
        response = self.middleware.process_view(req, dummy_view, (), {})
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(getattr(req, 'is_org_admin', False))

    def test_fail_open_when_permission_query_raises(self):
        # This user holds a real, currently-valid role that does NOT grant
        # ASSET/VIEW, so without the exception below this request would be
        # denied (403). Mocking the exception and still getting None proves
        # this is the fail-open branch, not an accidental pass through the
        # zero-role grace period.
        req = self.factory.get('/api/assets/list/')
        req.user = self.user_mismatched_role

        # Permissions are resolved from a cached bundle first. Make that
        # fast path fail too so the PostgreSQL fallback (fail-open) is what runs.
        with patch(
            'access_control.middleware.authorization.get_user_permission_bundle',
            side_effect=RuntimeError("cache unavailable"),
        ), patch(
            'access_control.middleware.authorization.RolePermission',
            **{'objects.filter.side_effect': RuntimeError("boom")},
        ):
            response = self.middleware.process_view(req, dummy_view, (), {})

        self.assertIsNone(response)

    def test_unmapped_url_allowed_for_authenticated_user(self):
        # A normal authenticated user hitting a URL that does not resolve to
        # any module (not under /api/<mapped-segment>/...) must be allowed
        # through: there is no gate to enforce, regardless of roles.
        req = self.factory.get('/health/')
        req.user = self.user_plain
        self.assertIsNone(self.middleware.process_view(req, dummy_view, (), {}))

    def test_options_request_on_mapped_module_allowed(self):
        # OPTIONS has no entry in METHOD_ACTION_MAP, so even though the
        # module resolves (ASSET), no action resolves and there is no gate
        # to enforce -- regardless of the user's roles.
        req = self.factory.options('/api/assets/list/')
        req.user = self.user_plain
        self.assertIsNone(self.middleware.process_view(req, dummy_view, (), {}))
