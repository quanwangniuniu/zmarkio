import json

from django.db import connection
from django.test import TestCase, RequestFactory, override_settings
from access_control.tests.mfa_request_factory import MfaVerifiedRequestFactory
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.models import Organization, Role, Permission
from core.services.tenant import slug_to_schema_name
from access_control.models import AdminOverrideAudit, RolePermission, UserRole
from access_control.middleware.authorization import AuthorizationMiddleware
from access_control.tests.test_urls import dummy_view


@override_settings(ROOT_URLCONF='access_control.tests.test_urls')
class AdminOverrideAuditTest(TestCase):
    """Covers audit emission for superuser/org-admin permission bypasses."""

    @classmethod
    def setUpTestData(cls):
        # provision_tenant_schema() (called by Organization.save()) resets
        # search_path to public after creating the schema, so re-set it here
        # before writing to tenant-only tables (Role, RolePermission, UserRole).
        cls.org = Organization.objects.create(name="OverrideAuditOrg")
        _schema = slug_to_schema_name(cls.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {_schema}, public')

        cls.perm_view_asset, _ = Permission.objects.get_or_create(module="ASSET", action="VIEW")
        cls.role_asset_viewer = Role.objects.create(organization=cls.org, name="AssetViewer", level=1)
        RolePermission.objects.create(role=cls.role_asset_viewer, permission=cls.perm_view_asset)

        cls.role_org_admin = Role.objects.create(organization=cls.org, name="OrgAdmin", level=2)

        User = get_user_model()
        cls.superuser = User.objects.create_user(
            username="root", email="root@example.com", password="pw",
            is_superuser=True, current_organization=cls.org,
        )
        cls.org_admin_user = User.objects.create_user(
            username="admin", email="admin@example.com", password="pw",
            current_organization=cls.org,
        )
        UserRole.objects.create(user=cls.org_admin_user, role=cls.role_org_admin, valid_from=timezone.now())

        # Has a role, but not one that grants CAMPAIGN/EDIT — exercises the
        # ordinary 403 path, which must never emit an override audit row.
        cls.plain_user = User.objects.create_user(
            username="plain", email="plain@example.com", password="pw",
            current_organization=cls.org,
        )
        UserRole.objects.create(user=cls.plain_user, role=cls.role_asset_viewer, valid_from=timezone.now())

        cls.factory = MfaVerifiedRequestFactory()
        cls.middleware = AuthorizationMiddleware()

        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

    def setUp(self):
        # SET search_path is connection-level, not transactional. Django's
        # TestCase rolls back each test via a savepoint, which does NOT reset
        # search_path, so re-apply it before every test method.
        _schema = slug_to_schema_name(self.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {_schema}, public')

    def tearDown(self):
        super().tearDown()
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

    def test_superuser_override_on_module_path_is_logged(self):
        req = self.factory.post('/api/campaigns/create/', REMOTE_ADDR='10.0.0.5')
        req.user = self.superuser
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertIsNone(resp)

        audit = AdminOverrideAudit.objects.get()
        self.assertEqual(audit.user, self.superuser)
        self.assertEqual(audit.override_type, 'SUPERUSER')
        self.assertEqual(audit.module, 'CAMPAIGN')
        self.assertEqual(audit.action, 'EDIT')
        self.assertEqual(audit.method, 'POST')
        self.assertEqual(audit.path, '/api/campaigns/create/')
        self.assertEqual(audit.ip_address, '10.0.0.5')
        self.assertEqual(audit.organization_id, self.org.id)
        self.assertEqual(audit.reason, '')

    def test_org_admin_override_on_module_path_is_logged(self):
        req = self.factory.put('/api/campaigns/1/approve/')
        req.user = self.org_admin_user
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertIsNone(resp)

        audit = AdminOverrideAudit.objects.get()
        self.assertEqual(audit.user, self.org_admin_user)
        self.assertEqual(audit.override_type, 'ORG_ADMIN')
        self.assertEqual(audit.module, 'CAMPAIGN')
        self.assertEqual(audit.action, 'APPROVE')

    def test_org_admin_without_mfa_is_blocked_and_not_logged(self):
        """MED-326: a level-2 role grants no bypass until MFA is passed this session."""
        req = RequestFactory().put('/api/campaigns/1/approve/')
        req.user = self.org_admin_user
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(json.loads(resp.content)['errorCode'], 'MFA_REQUIRED')
        self.assertEqual(AdminOverrideAudit.objects.count(), 0)

    def test_superuser_bypass_on_non_module_path_is_not_logged(self):
        # /api/auth/... never resolves to a recognized module, so there is no
        # permission gate to bypass — nothing should be audited.
        req = self.factory.post('/api/auth/login/')
        req.user = self.superuser
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertIsNone(resp)
        self.assertEqual(AdminOverrideAudit.objects.count(), 0)

    def test_org_admin_bypass_on_non_module_path_is_not_logged(self):
        req = self.factory.get('/api/health/')
        req.user = self.org_admin_user
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertIsNone(resp)
        self.assertEqual(AdminOverrideAudit.objects.count(), 0)

    def test_reason_header_is_captured(self):
        req = self.factory.post(
            '/api/campaigns/create/',
            HTTP_X_OVERRIDE_REASON='Emergency hotfix rollout',
        )
        req.user = self.superuser
        self.middleware.process_view(req, dummy_view, (), {})
        audit = AdminOverrideAudit.objects.get()
        self.assertEqual(audit.reason, 'Emergency hotfix rollout')

    def test_missing_reason_header_defaults_to_empty_string(self):
        req = self.factory.post('/api/campaigns/create/')
        req.user = self.superuser
        self.middleware.process_view(req, dummy_view, (), {})
        audit = AdminOverrideAudit.objects.get()
        self.assertEqual(audit.reason, '')

    def test_normal_user_permission_denied_does_not_log_override(self):
        req = self.factory.post('/api/campaigns/create/')
        req.user = self.plain_user
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(AdminOverrideAudit.objects.count(), 0)

    def test_normal_user_permission_allowed_does_not_log_override(self):
        req = self.factory.get('/api/assets/list/')
        req.user = self.plain_user
        resp = self.middleware.process_view(req, dummy_view, (), {})
        self.assertIsNone(resp)
        self.assertEqual(AdminOverrideAudit.objects.count(), 0)
