"""
Representative real-API allow/deny tests for the modules added to the
declarative authorization policy (BUDGET_REQUEST, BUDGET_POOL,
BUDGET_ESCALATION, QUEUE, SUPPORT_TEAM, TICKET, INVITATION), plus explicitly
exempt routes and the internal escalation webhook.

Every request is authenticated with a real JWT (Authorization: Bearer ...) and
selects the tenant with X-Organization-Slug, so TenantSchemaMiddleware sets
request.user and the organization schema exactly as in production before
AuthorizationMiddleware runs (force_authenticate() would NOT do that; see
asset/tests/test_views/test_authentication.py::AssetAuthorizationRealPermissionTest).

Three kinds of assertion are used, and each test says which one it makes:

* DENY: AuthorizationMiddleware's own 403 with the exact body
  {"detail": "Permission denied"} (DRF's permission failures use different
  messages, so the two cannot be confused).
* ALLOW (full fixture): the request is expected to succeed, so the test
  asserts the success status (200) of the real view.
* REACHES VIEW (reachability only): used where building the view's own
  fixtures is out of scope here (e.g. a CSM admin CustomerUser). It
  proves only that the middleware did not block the request and that the
  request did not end in 401 or a server error. It does NOT prove the
  request succeeded.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from access_control import services
from access_control.models import RolePermission, UserRole
from core.models import Organization, Permission, Role
from core.services.tenant import slug_to_schema_name

User = get_user_model()

MIDDLEWARE_DENIAL = {"detail": "Permission denied"}
TEST_INTERNAL_TOKEN = "med335-test-internal-token"  # test-only value, never a real secret


class PolicyApiBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="PolicyApiOrg")
        schema = slug_to_schema_name(cls.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {schema}, public")

        now = timezone.now()
        cls._counter = 0
        cls.holders = {}

        def make_user(label):
            cls._counter += 1
            return User.objects.create_user(
                username=f"pa_{cls._counter}_{label}",
                email=f"pa{cls._counter}@policyapi.test",
                password=None,
            )

        def grant(user, name, module, action):
            role = Role.objects.create(organization=cls.org, name=name, level=1)
            permission, _ = Permission.objects.get_or_create(module=module, action=action)
            RolePermission.objects.create(role=role, permission=permission)
            UserRole.objects.create(user=user, role=role, valid_from=now)

        for module, action in [
            ("BUDGET_REQUEST", "VIEW"),
            ("BUDGET_REQUEST", "APPROVE"),
            ("BUDGET_POOL", "VIEW"),
            ("BUDGET_ESCALATION", "EDIT"),
            ("QUEUE", "VIEW"),
            ("SUPPORT_TEAM", "VIEW"),
            ("TICKET", "VIEW"),
            ("INVITATION", "VIEW"),
            ("INVITATION", "EDIT"),
        ]:
            user = make_user(f"{module}_{action}".lower())
            grant(user, f"holder-{module}-{action}", module, action)
            cls.holders[(module, action)] = user

        # Has a real, valid role, but it grants nothing used by the cases below.
        cls.denied_user = make_user("denied")
        grant(cls.denied_user, "denied-asset-export", "ASSET", "EXPORT")

        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")

    def setUp(self):
        self.client = APIClient()
        self.client.raise_request_exception = False  # a view's own 500 must not mask the auth assertion
        schema = slug_to_schema_name(self.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {schema}, public")

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")

    # -- helpers ---------------------------------------------------------
    def _request(self, method, path, user=None, **extra):
        headers = {"HTTP_X_ORGANIZATION_SLUG": self.org.slug}
        if user is not None:
            token = RefreshToken.for_user(user).access_token
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        headers.update(extra)
        kwargs = {"format": "json"} if method in ("post", "put", "patch") else {}
        return getattr(self.client, method)(path, **headers, **kwargs)

    @staticmethod
    def _is_middleware_denial(response):
        if response.status_code != 403:
            return False
        try:
            return response.json() == MIDDLEWARE_DENIAL
        except ValueError:
            return False

    def assertDeniedByMiddleware(self, response):
        """DENY: AuthorizationMiddleware's own 403."""
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), MIDDLEWARE_DENIAL)

    def assertReachesView(self, response):
        """REACHES VIEW (reachability only).

        Passes when the middleware did not block the request and the request
        did not end in 401 or a server error. The view may still answer
        400/403/404 for its own reasons; this does NOT assert success.
        """
        self.assertFalse(
            self._is_middleware_denial(response),
            "request was denied by AuthorizationMiddleware",
        )
        self.assertNotEqual(response.status_code, 401, "request ended unauthenticated (401)")
        self.assertLess(response.status_code, 500, f"server error {response.status_code}")

    def assertListOk(self, response):
        """ALLOW (full fixture): 200 with a list or a paginated payload."""
        self.assertEqual(response.status_code, 200, getattr(response, "content", b"")[:300])
        body = response.json()
        self.assertTrue(isinstance(body, list) or "results" in body, body)


class ActivatedPolicyMixin:
    """Switch the planned rule-table modules to ``active`` for each test.

    The seven modules are planned (not enforced) by default. These tests prove
    the policy is correct once activated; PlannedRoutesApiTest covers the
    shipped default.
    """

    def setUp(self):
        super().setUp()
        patcher = patch.dict(
            services.RULE_TABLE_ENFORCEMENT,
            {module: services.ENFORCEMENT_ACTIVE for module in services.RULE_TABLE_ENFORCEMENT},
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class BudgetPolicyApiTest(ActivatedPolicyMixin, PolicyApiBase):
    # BudgetRequestPermission/BudgetPoolPermission require an x-user-role header
    # (a user with no organization falls back to authenticated-only access).
    VIEW_HEADERS = {"HTTP_X_USER_ROLE": "team_member"}

    def test_request_list_allowed_for_holder_on_both_mounts(self):
        for prefix in ("/api/budgets", "/budgets"):
            with self.subTest(prefix=prefix):
                response = self._request(
                    "get", prefix + "/requests/", self.holders[("BUDGET_REQUEST", "VIEW")], **self.VIEW_HEADERS
                )
                self.assertListOk(response)

    def test_request_list_denied_without_permission_on_both_mounts(self):
        for prefix in ("/api/budgets", "/budgets"):
            with self.subTest(prefix=prefix):
                self.assertDeniedByMiddleware(
                    self._request("get", prefix + "/requests/", self.denied_user, **self.VIEW_HEADERS)
                )

    def test_pool_list_allowed_for_holder(self):
        response = self._request(
            "get", "/api/budgets/pools/", self.holders[("BUDGET_POOL", "VIEW")], **self.VIEW_HEADERS
        )
        self.assertListOk(response)

    def test_pool_list_denied_without_permission(self):
        self.assertDeniedByMiddleware(
            self._request("get", "/api/budgets/pools/", self.denied_user, **self.VIEW_HEADERS)
        )

    def test_decision_reaches_view_for_approve_holder(self):
        # Reachability only: request id 1 does not exist, so the view answers
        # for its own reasons (not-found / validation).
        response = self._request(
            "patch", "/api/budgets/requests/1/decision/",
            self.holders[("BUDGET_REQUEST", "APPROVE")], data={}, **self.VIEW_HEADERS,
        )
        self.assertReachesView(response)

    def test_decision_denied_for_view_only_holder(self):
        # VIEW on the same module is not enough to decide a request.
        self.assertDeniedByMiddleware(
            self._request(
                "patch", "/api/budgets/requests/1/decision/",
                self.holders[("BUDGET_REQUEST", "VIEW")], data={}, **self.VIEW_HEADERS,
            )
        )

    def test_ad_channels_exempt_route_succeeds_without_module_permission(self):
        # Explicitly exempt (no Permission module); the user's role grants nothing relevant.
        response = self._request("get", "/api/budgets/ad-channels/", self.denied_user)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)


@override_settings(INTERNAL_WEBHOOK_ENABLED=True, INTERNAL_WEBHOOK_TOKEN=TEST_INTERNAL_TOKEN)
class BudgetEscalationWebhookApiTest(ActivatedPolicyMixin, PolicyApiBase):
    """The anonymous internal webhook keeps authenticating with X-Internal-Token
    only. The middleware never trusts the header: it skips anonymous requests,
    and gates authenticated users like any other route."""

    path = "/api/budgets/escalate/"

    def test_anonymous_webhook_with_valid_token_reaches_the_view(self):
        response = self._request("post", self.path, None, HTTP_X_INTERNAL_TOKEN=TEST_INTERNAL_TOKEN, data={})
        # Token accepted by EscalationPermission; the view then rejects the
        # empty body for its own reason.
        self.assertEqual(response.status_code, 400)
        self.assertIn("budget_request_id", response.json().get("error", ""))

    def test_anonymous_webhook_without_token_is_rejected_by_the_view(self):
        response = self._request("post", self.path, None, data={})
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(self._is_middleware_denial(response))

    def test_anonymous_webhook_with_wrong_token_is_rejected_by_the_view(self):
        response = self._request("post", self.path, None, HTTP_X_INTERNAL_TOKEN="wrong", data={})
        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(self._is_middleware_denial(response))

    def test_authenticated_user_without_permission_is_denied_even_with_valid_token(self):
        # The header is not trusted by the middleware.
        self.assertDeniedByMiddleware(
            self._request(
                "post", self.path, self.denied_user, HTTP_X_INTERNAL_TOKEN=TEST_INTERNAL_TOKEN, data={}
            )
        )

    def test_authenticated_user_without_permission_is_denied_without_token(self):
        self.assertDeniedByMiddleware(self._request("post", self.path, self.denied_user, data={}))

    def test_authenticated_user_with_permission_and_valid_token_reaches_the_view(self):
        response = self._request(
            "post", self.path, self.holders[("BUDGET_ESCALATION", "EDIT")],
            HTTP_X_INTERNAL_TOKEN=TEST_INTERNAL_TOKEN, data={},
        )
        self.assertEqual(response.status_code, 400)  # passes both checks; empty body rejected by the view


class CsmPolicyApiTest(ActivatedPolicyMixin, PolicyApiBase):
    CSM_ADMIN_REQUIRED = "CSM admin access required."

    def test_ticket_list_allowed_for_holder(self):
        # TicketViewSet only requires IsAuthenticated; a non-staff user sees an empty list.
        self.assertListOk(self._request("get", "/api/csm/tickets/", self.holders[("TICKET", "VIEW")]))

    def test_ticket_list_denied_without_permission(self):
        self.assertDeniedByMiddleware(self._request("get", "/api/csm/tickets/", self.denied_user))

    def test_queue_list_reaches_view_for_holder(self):
        # Reachability only: QueueViewSet also needs a CSM admin CustomerUser
        # (IsCsmAccessAllowed), a fixture not built here. Because the holder is
        # not a CSM admin, the view's own 403 is expected, and it must be the
        # view's message, not the middleware's.
        response = self._request("get", "/api/csm/queues/", self.holders[("QUEUE", "VIEW")])
        self.assertReachesView(response)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json().get("detail"), self.CSM_ADMIN_REQUIRED)

    def test_queue_list_denied_without_permission(self):
        self.assertDeniedByMiddleware(self._request("get", "/api/csm/queues/", self.denied_user))

    def test_queue_agents_reaches_view_for_support_team_holder(self):
        # Reachability only (same CSM admin fixture note as above).
        response = self._request("get", "/api/csm/queues/1/agents/", self.holders[("SUPPORT_TEAM", "VIEW")])
        self.assertReachesView(response)
        self.assertEqual(response.json().get("detail"), self.CSM_ADMIN_REQUIRED)

    def test_queue_agents_and_teams_denied_without_support_team_permission(self):
        for path in ("/api/csm/queues/1/agents/", "/api/csm/queues/1/teams/"):
            with self.subTest(path=path):
                self.assertDeniedByMiddleware(self._request("get", path, self.denied_user))
                # QUEUE VIEW does not grant SUPPORT_TEAM VIEW.
                self.assertDeniedByMiddleware(self._request("get", path, self.holders[("QUEUE", "VIEW")]))

    def test_queue_teams_reaches_view_for_support_team_holder(self):
        response = self._request("get", "/api/csm/queues/1/teams/", self.holders[("SUPPORT_TEAM", "VIEW")])
        self.assertReachesView(response)
        self.assertEqual(response.json().get("detail"), self.CSM_ADMIN_REQUIRED)

    def test_exempt_csm_route_is_not_gated_by_middleware(self):
        # Reachability only: explicitly exempt resource with its own permission class.
        self.assertReachesView(self._request("get", "/api/csm/templates/", self.denied_user))


class InvitationPolicyApiTest(ActivatedPolicyMixin, PolicyApiBase):
    def test_organization_invitation_list_reaches_view_for_view_holder(self):
        # Reachability only: the view additionally requires organization
        # membership and org-admin status, a fixture not built here.
        path = f"/api/core/organizations/{self.org.id}/invitations/list/"
        self.assertReachesView(self._request("get", path, self.holders[("INVITATION", "VIEW")]))

    def test_organization_invitation_list_denied_without_permission(self):
        path = f"/api/core/organizations/{self.org.id}/invitations/list/"
        self.assertDeniedByMiddleware(self._request("get", path, self.denied_user))

    def test_organization_invitation_create_requires_edit(self):
        path = f"/api/core/organizations/{self.org.id}/invitations/"
        # Reachability only for the EDIT holder (the view applies its own admin check).
        self.assertReachesView(self._request("post", path, self.holders[("INVITATION", "EDIT")], data={}))
        self.assertDeniedByMiddleware(self._request("post", path, self.holders[("INVITATION", "VIEW")], data={}))

    def test_pending_invitations_self_service_route_succeeds_without_invitation_permission(self):
        # Exempt: an invitee has no INVITATION permission. Full fixture: 200 with an empty list.
        response = self._request("get", "/api/core/invitations/pending/", self.denied_user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_accept_routes_are_not_gated_by_middleware(self):
        # Reachability only: the accept views validate their own payload
        # (an empty body is expected to be rejected by the view).
        for path in ("/api/core/invitations/accept/", "/api/core/invitations/accept-organization/"):
            with self.subTest(path=path):
                self.assertReachesView(self._request("post", path, self.denied_user, data={}))


class PlannedRoutesApiTest(PolicyApiBase):
    """Shipped default: planned modules behave exactly as they did before the policy registry.

    A user whose only role grants nothing relevant is NOT denied by the
    middleware on planned routes (the view still applies its own checks), while
    the legacy ASSET routes remain enforced.
    """

    def test_planned_routes_are_not_denied_by_middleware(self):
        for method, path in [
            ("get", "/api/budgets/requests/"),
            ("get", "/budgets/pools/"),
            ("get", "/api/csm/tickets/"),
            ("get", "/api/csm/queues/"),
            ("get", "/api/csm/queues/1/agents/"),
            ("get", f"/api/core/organizations/{self.org.pk}/invitations/"),
            ("get", "/api/campaign-templates/"),
        ]:
            with self.subTest(path=path):
                response = self._request(method, path, user=self.denied_user)
                self.assertFalse(self._is_middleware_denial(response))

    def test_escalation_webhook_is_unchanged(self):
        with override_settings(INTERNAL_WEBHOOK_ENABLED=True, INTERNAL_WEBHOOK_TOKEN=TEST_INTERNAL_TOKEN):
            response = self._request("post", "/api/budgets/escalate/", HTTP_X_INTERNAL_TOKEN=TEST_INTERNAL_TOKEN)
        self.assertFalse(self._is_middleware_denial(response))
        self.assertNotEqual(response.status_code, 401)

    def test_legacy_asset_route_is_still_enforced(self):
        self.assertDeniedByMiddleware(self._request("get", "/api/assets/", user=self.denied_user))
