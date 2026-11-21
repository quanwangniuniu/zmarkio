"""
Tests for the explicit authorization policy registry that covers all
nine Permission modules.

Three layers:

1. Policy-table tests (pure, no DB): an independent hand-written literal table
   of (method, path) -> (module, action), and the negative / edge cases
   (unsupported methods, lookalike paths, PATCH is not assumed APPROVE) plus
   the documented hardening gaps (HEAD, `.json`).
2. Middleware allow/deny tests for every module (tenant schema, RequestFactory).
3. Real-API tests live in test_policy_api_integration.py.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from access_control import services
from access_control.middleware.authorization import AuthorizationMiddleware
from access_control.models import AdminOverrideAudit, RolePermission, UserRole
from access_control.tests.test_urls import dummy_view
from core.models import Organization, Permission, Role
from core.services.tenant import slug_to_schema_name

User = get_user_model()

_UUID = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------------------
# 1. Policy-table tests (pure)
# ---------------------------------------------------------------------------


def _literal_rows():
    rows = []
    budget = [
        ("GET", "/requests/", "BUDGET_REQUEST", "VIEW"),
        ("POST", "/requests/", "BUDGET_REQUEST", "EDIT"),
        ("GET", "/requests/7/", "BUDGET_REQUEST", "VIEW"),
        ("PATCH", "/requests/7/", "BUDGET_REQUEST", "EDIT"),
        ("DELETE", "/requests/7/", "BUDGET_REQUEST", "DELETE"),
        ("POST", "/requests/7/start-review/", "BUDGET_REQUEST", "EDIT"),
        ("PATCH", "/requests/7/decision/", "BUDGET_REQUEST", "APPROVE"),
        ("PUT", "/requests/7/decision/", "BUDGET_REQUEST", "APPROVE"),
        ("GET", "/pools/", "BUDGET_POOL", "VIEW"),
        ("POST", "/pools/", "BUDGET_POOL", "EDIT"),
        ("GET", "/pools/3/", "BUDGET_POOL", "VIEW"),
        ("PATCH", "/pools/3/", "BUDGET_POOL", "EDIT"),
        ("DELETE", "/pools/3/", "BUDGET_POOL", "DELETE"),
        ("POST", "/escalate/", "BUDGET_ESCALATION", "EDIT"),
    ]
    for prefix in ("/api/budgets", "/budgets"):  # both URLconf mounts
        rows += [(m, prefix + p, mod, act) for m, p, mod, act in budget]
    csm = [
        ("GET", "/queues/", "QUEUE", "VIEW"),
        ("POST", "/queues/", "QUEUE", "EDIT"),
        ("GET", "/queues/q-slug/", "QUEUE", "VIEW"),
        ("PUT", "/queues/q-slug/", "QUEUE", "EDIT"),
        ("PATCH", "/queues/q-slug/", "QUEUE", "EDIT"),
        ("DELETE", "/queues/q-slug/", "QUEUE", "DELETE"),
        ("GET", "/queues/q-slug/tickets/", "QUEUE", "VIEW"),
        ("GET", "/queues/q-slug/ticket_counts/", "QUEUE", "VIEW"),
        ("GET", "/projects/4/queues/", "QUEUE", "VIEW"),
        ("POST", "/projects/4/queues/", "QUEUE", "EDIT"),
        ("GET", "/queues/5/teams/", "SUPPORT_TEAM", "VIEW"),
        ("POST", "/queues/5/teams/", "SUPPORT_TEAM", "EDIT"),
        ("DELETE", "/queues/5/teams/9/", "SUPPORT_TEAM", "DELETE"),
        ("GET", "/queues/5/agents/", "SUPPORT_TEAM", "VIEW"),
        ("POST", "/queues/5/agents/", "SUPPORT_TEAM", "EDIT"),
        ("DELETE", "/queues/5/agents/9/", "SUPPORT_TEAM", "DELETE"),
        ("GET", "/tickets/", "TICKET", "VIEW"),
        ("POST", "/tickets/", "TICKET", "EDIT"),
        ("GET", "/tickets/8/", "TICKET", "VIEW"),
        ("PATCH", "/tickets/8/", "TICKET", "EDIT"),
        ("POST", "/tickets/8/claim/", "TICKET", "EDIT"),
        ("POST", "/tickets/8/close/", "TICKET", "EDIT"),
        # Ticket configuration belongs to TICKET.
        ("GET", "/ticket-forms/", "TICKET", "VIEW"),
        ("POST", "/ticket-forms/", "TICKET", "EDIT"),
        ("GET", "/ticket-forms/f-slug/", "TICKET", "VIEW"),
        ("PUT", "/ticket-forms/f-slug/", "TICKET", "EDIT"),
        ("PATCH", "/ticket-forms/f-slug/", "TICKET", "EDIT"),
        ("DELETE", "/ticket-forms/f-slug/", "TICKET", "DELETE"),
        ("PUT", "/ticket-forms/f-slug/fields/", "TICKET", "EDIT"),
        ("POST", "/ticket-forms/f-slug/set-default/", "TICKET", "EDIT"),
        ("GET", "/ticket-forms/f-slug/assignments/", "TICKET", "VIEW"),
        ("PUT", "/ticket-forms/f-slug/assignments/", "TICKET", "EDIT"),
        ("GET", "/ticket-statuses/", "TICKET", "VIEW"),
        ("POST", "/ticket-statuses/", "TICKET", "EDIT"),
        ("GET", "/ticket-statuses/3/", "TICKET", "VIEW"),
        ("PATCH", "/ticket-statuses/3/", "TICKET", "EDIT"),
        ("DELETE", "/ticket-statuses/3/", "TICKET", "DELETE"),
        ("GET", "/ticket-status-machine/", "TICKET", "VIEW"),
        ("PUT", "/ticket-status-machine/", "TICKET", "EDIT"),
        ("PATCH", "/ticket-status-machine/auto-resolve/", "TICKET", "EDIT"),
    ]
    rows += [(m, "/api/csm" + p, mod, act) for m, p, mod, act in csm]
    core = [
        ("POST", "/organizations/2/invitations/", "INVITATION", "EDIT"),
        ("GET", "/organizations/2/invitations/list/", "INVITATION", "VIEW"),
        ("DELETE", "/organizations/2/invitations/6/", "INVITATION", "DELETE"),
        ("POST", "/invitations/6/resend/", "INVITATION", "EDIT"),
        ("GET", "/projects/3/invitations/", "INVITATION", "VIEW"),
        ("GET", "/projects/3/invitations/pending-approval/", "INVITATION", "VIEW"),
        ("POST", "/projects/3/invitations/6/approve/", "INVITATION", "APPROVE"),
        ("DELETE", "/projects/3/invitations/6/reject/", "INVITATION", "APPROVE"),
    ]
    rows += [(m, "/api/core" + p, mod, act) for m, p, mod, act in core]
    campaign_siblings = [
        ("GET", "/campaign-templates/", "CAMPAIGN", "VIEW"),
        ("POST", "/campaign-templates/", "CAMPAIGN", "EDIT"),
        ("GET", "/campaign-templates/" + _UUID + "/", "CAMPAIGN", "VIEW"),
        ("PUT", "/campaign-templates/" + _UUID + "/", "CAMPAIGN", "EDIT"),
        ("PATCH", "/campaign-templates/" + _UUID + "/", "CAMPAIGN", "EDIT"),
        ("DELETE", "/campaign-templates/" + _UUID + "/", "CAMPAIGN", "DELETE"),
        ("POST", "/campaign-templates/" + _UUID + "/create-campaign/", "CAMPAIGN", "EDIT"),
        ("GET", "/campaign-task-links/", "CAMPAIGN", "VIEW"),
        ("POST", "/campaign-task-links/", "CAMPAIGN", "EDIT"),
        ("DELETE", "/campaign-task-links/" + _UUID + "/", "CAMPAIGN", "DELETE"),
        ("GET", "/campaign-decision-links/", "CAMPAIGN", "VIEW"),
        ("POST", "/campaign-decision-links/", "CAMPAIGN", "EDIT"),
        ("GET", "/campaign-calendar-links/", "CAMPAIGN", "VIEW"),
        ("POST", "/campaign-calendar-links/", "CAMPAIGN", "EDIT"),
    ]
    rows += [(m, "/api" + p, mod, act) for m, p, mod, act in campaign_siblings]
    # Legacy ASSET / CAMPAIGN behavior, unchanged.
    rows += [
        ("GET", "/api/assets/", "ASSET", "VIEW"),
        ("POST", "/api/assets/", "ASSET", "EDIT"),
        ("PUT", "/api/assets/4/submit/", "ASSET", "EDIT"),
        ("PATCH", "/api/assets/4/review/", "ASSET", "APPROVE"),
        ("DELETE", "/api/assets/4/", "ASSET", "DELETE"),
        ("GET", "/api/campaigns/", "CAMPAIGN", "VIEW"),
        ("POST", "/api/campaigns/", "CAMPAIGN", "EDIT"),
        ("PATCH", "/api/campaigns/abc/", "CAMPAIGN", "APPROVE"),
        ("DELETE", "/api/campaigns/abc/", "CAMPAIGN", "DELETE"),
    ]
    return rows


LITERAL_ROWS = _literal_rows()


@pytest.mark.parametrize(
    "method, path, module, action",
    LITERAL_ROWS,
    ids=[f"{m} {p}" for m, p, _, _ in LITERAL_ROWS],
)
def test_policy_table_literal_rows(method, path, module, action):
    assert services.resolve_module_and_action(path, method) == (module, action)


@pytest.mark.parametrize(
    "method, path, expected",
    [
        # PATCH is NOT assumed to be APPROVE outside the legacy modules.
        ("PATCH", "/api/csm/queues/1/", ("QUEUE", "EDIT")),
        ("PATCH", "/api/csm/tickets/1/", ("TICKET", "EDIT")),
        ("PATCH", "/api/budgets/pools/1/", ("BUDGET_POOL", "EDIT")),
        ("PATCH", "/api/budgets/requests/1/", ("BUDGET_REQUEST", "EDIT")),
        ("PATCH", "/api/csm/ticket-statuses/1/", ("TICKET", "EDIT")),
        ("PATCH", "/api/campaign-templates/1/", ("CAMPAIGN", "EDIT")),
        # ... while the legacy /api/campaigns/** mapping keeps PATCH -> APPROVE.
        ("PATCH", "/api/campaigns/abc/", ("CAMPAIGN", "APPROVE")),
        ("PUT", "/api/csm/ticket-statuses/1/", ("TICKET", None)),
        ("PUT", "/api/campaign-task-links/" + _UUID + "/", ("CAMPAIGN", None)),
        # Path matches a rule but the method is not routed: module, no action.
        ("OPTIONS", "/api/csm/queues/", ("QUEUE", None)),
        ("PUT", "/api/budgets/requests/7/", ("BUDGET_REQUEST", None)),
        ("DELETE", "/api/csm/tickets/5/", ("TICKET", None)),
        ("PATCH", "/api/csm/queues/5/agents/", ("SUPPORT_TEAM", None)),
        # Documented hardening gap (matrix section 6): HEAD is not treated as GET,
        # for the rule table and for the legacy resolver alike.
        ("HEAD", "/api/csm/queues/", ("QUEUE", None)),
        ("HEAD", "/budgets/pools/", ("BUDGET_POOL", None)),
        ("HEAD", "/api/assets/list/", ("ASSET", None)),
        # Documented hardening gap (matrix section 6): a `.json` format suffix is
        # not matched, so these resolve to nothing (legacy behavior unchanged).
        ("GET", "/api/csm/queues.json", (None, None)),
        ("GET", "/api/campaigns.json", (None, None)),
        # Lookalike / unknown paths are not gated.
        ("GET", "/api/csm/unknown/", (None, None)),
        ("GET", "/api/csm/queues/1/agents/2/extra/", (None, None)),
        ("GET", "/api/csm/queues/1/unknown/", (None, None)),
        ("GET", "/api/budgets/unknown/", (None, None)),
        ("GET", "/budgets/", (None, None)),
        ("GET", "/api/core/invitations/", (None, None)),
        ("GET", "/api/core/organizations/1/invitations/9/extra/", (None, None)),
        ("GET", "/api/core/organizations/", (None, None)),
        # Prefix lookalikes must not match.
        ("GET", "/api/budgetsx/requests/", (None, None)),
        ("GET", "/xbudgets/requests/", (None, None)),
        ("GET", "/api/csmx/queues/", (None, None)),
    ],
)
def test_policy_table_edge_cases(method, path, expected):
    assert services.resolve_module_and_action(path, method) == expected


class PolicyRegistryIntegrityTest(SimpleTestCase):
    def test_all_nine_permission_modules_are_covered(self):
        expected = {module for module, _label in Permission.MODULE_CHOICES}
        self.assertEqual(len(expected), 9)
        covered = set(services.MODULE_PATH_MAP.values()) | {rule.module for rule in services.POLICY_RULES}
        self.assertEqual(covered, expected)


# ---------------------------------------------------------------------------
# 2. Middleware allow / deny for every module
# ---------------------------------------------------------------------------

#: (module, method, path, action) -- at least one real route per module.
MODULE_CASES = [
    ("ASSET", "GET", "/api/assets/", "VIEW"),
    ("CAMPAIGN", "GET", "/api/campaigns/", "VIEW"),
    ("BUDGET_REQUEST", "GET", "/api/budgets/requests/", "VIEW"),
    ("BUDGET_REQUEST", "PATCH", "/budgets/requests/1/decision/", "APPROVE"),
    ("BUDGET_POOL", "GET", "/budgets/pools/", "VIEW"),
    ("BUDGET_ESCALATION", "POST", "/api/budgets/escalate/", "EDIT"),
    ("QUEUE", "GET", "/api/csm/queues/", "VIEW"),
    ("SUPPORT_TEAM", "POST", "/api/csm/queues/1/agents/", "EDIT"),
    ("TICKET", "POST", "/api/csm/tickets/1/claim/", "EDIT"),
    ("INVITATION", "POST", "/api/core/organizations/1/invitations/", "EDIT"),
    ("INVITATION", "POST", "/api/core/projects/1/invitations/2/approve/", "APPROVE"),
]


def _case_id(case):
    module, method, path, action = case
    return f"{module}:{method} {path}"


class _MiddlewareBase(TestCase):
    """Shared tenant schema, roles and helpers (no tests of its own)."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="PolicyRegistryOrg")
        schema = slug_to_schema_name(cls.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {schema}, public")

        cls.factory = RequestFactory()
        cls.middleware = AuthorizationMiddleware()
        now = timezone.now()
        counter = {"n": 0}

        def make_user(label):
            counter["n"] += 1
            return User.objects.create_user(
                username=f"pr_{counter['n']}_{label}"[:150],
                email=f"pr{counter['n']}@policyregistry.test",
                password=None,
            )

        def role_with(name, module, action):
            role = Role.objects.create(organization=cls.org, name=name, level=1)
            permission, _ = Permission.objects.get_or_create(module=module, action=action)
            RolePermission.objects.create(role=role, permission=permission)
            return role

        cls.holders = {}          # (module, action) -> user holding exactly it
        cls.other_action = {}     # module -> user holding (module, EXPORT) only
        cls.other_module = {}     # (module, action) -> user holding the action on a different module
        cls.expired = {}          # (module, action) -> user whose matching role expired
        for module, _method, _path, action in MODULE_CASES:
            key = (module, action)
            if key in cls.holders:
                continue
            holder = make_user("holder")
            UserRole.objects.create(
                user=holder, role=role_with(f"holder-{module}-{action}", module, action), valid_from=now
            )
            cls.holders[key] = holder

            expired = make_user("expired")
            UserRole.objects.create(
                user=expired,
                role=role_with(f"expired-{module}-{action}", module, action),
                valid_from=now - timedelta(days=2),
                valid_to=now - timedelta(days=1),
            )
            cls.expired[key] = expired

            wrong_module = "QUEUE" if module == "ASSET" else "ASSET"
            swapped = make_user("othermodule")
            UserRole.objects.create(
                user=swapped,
                role=role_with(f"othermodule-{module}-{action}", wrong_module, action),
                valid_from=now,
            )
            cls.other_module[key] = swapped

            if module not in cls.other_action:
                other = make_user("otheraction")
                UserRole.objects.create(
                    user=other, role=role_with(f"otheraction-{module}", module, "EXPORT"), valid_from=now
                )
                cls.other_action[module] = other

        cls.zero_role_user = make_user("zerorole")
        admin_role = Role.objects.create(organization=cls.org, name="OrgAdminLevel2", level=2)
        cls.org_admin = make_user("orgadmin")
        UserRole.objects.create(user=cls.org_admin, role=admin_role, valid_from=now)
        cls.superuser = User.objects.create_user(
            username="pr_superuser", email="prsuper@policyregistry.test", password=None, is_superuser=True
        )
        # A user with a real role that grants nothing relevant.
        cls.denied_user = make_user("denied")
        UserRole.objects.create(
            user=cls.denied_user, role=role_with("denied-asset-export", "ASSET", "EXPORT"), valid_from=now
        )

        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")

    def setUp(self):
        schema = slug_to_schema_name(self.org.slug)
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {schema}, public")

    def tearDown(self):
        super().tearDown()
        with connection.cursor() as cursor:
            cursor.execute("SET search_path TO public")

    def _run(self, user, method, path, **extra):
        request = self.factory.generic(method, path, **extra)
        request.user = user
        return self.middleware.process_view(request, dummy_view, (), {})

    def _assert_denied(self, response):
        self.assertIsInstance(response, JsonResponse)
        self.assertEqual(response.status_code, 403)
        self.assertJSONEqual(response.content, {"detail": "Permission denied"})


#: Rule-table modules whose enforcement is *planned* in this PR.
PLANNED_MODULES = ("BUDGET_REQUEST", "BUDGET_POOL", "BUDGET_ESCALATION", "QUEUE", "SUPPORT_TEAM", "TICKET", "INVITATION")


class MiddlewareAllNineModulesTest(_MiddlewareBase):
    """Allow/deny semantics of every module once its policy is *activated*.

    The rule-table modules are planned (not enforced) by default, so this class
    switches them to ``active`` for the duration of each test. This proves the
    policy is correct and ready for the later rollout without enabling it now.
    The default (shipped) behavior is covered by PlannedRoutesNotEnforcedTest.
    """

    def setUp(self):
        super().setUp()
        patcher = patch.dict(
            services.RULE_TABLE_ENFORCEMENT,
            {module: services.ENFORCEMENT_ACTIVE for module in services.RULE_TABLE_ENFORCEMENT},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- allow -------------------------------------------------------------

    def test_user_with_matching_permission_is_allowed(self):
        for case in MODULE_CASES:
            module, method, path, action = case
            with self.subTest(case=_case_id(case)):
                self.assertIsNone(self._run(self.holders[(module, action)], method, path))

    def test_zero_role_user_is_allowed_for_every_module(self):
        for case in MODULE_CASES:
            _module, method, path, _action = case
            with self.subTest(case=_case_id(case)):
                self.assertIsNone(self._run(self.zero_role_user, method, path))

    def test_org_admin_bypass_applies_to_every_module(self):
        for case in MODULE_CASES:
            _module, method, path, _action = case
            with self.subTest(case=_case_id(case)):
                request = self.factory.generic(method, path)
                request.user = self.org_admin
                self.assertIsNone(self.middleware.process_view(request, dummy_view, (), {}))
                self.assertTrue(request.is_org_admin)

    def test_superuser_bypass_applies_to_every_module(self):
        for case in MODULE_CASES:
            _module, method, path, _action = case
            with self.subTest(case=_case_id(case)):
                self.assertIsNone(self._run(self.superuser, method, path))

    def test_anonymous_requests_are_skipped_for_every_module(self):
        for case in MODULE_CASES:
            _module, method, path, _action = case
            with self.subTest(case=_case_id(case)):
                self.assertIsNone(self._run(AnonymousUser(), method, path))

    # -- deny --------------------------------------------------------------

    def test_user_with_unrelated_role_is_denied(self):
        for case in MODULE_CASES:
            _module, method, path, _action = case
            with self.subTest(case=_case_id(case)):
                self._assert_denied(self._run(self.denied_user, method, path))

    def test_user_with_other_action_on_same_module_is_denied(self):
        for case in MODULE_CASES:
            module, method, path, _action = case
            with self.subTest(case=_case_id(case)):
                self._assert_denied(self._run(self.other_action[module], method, path))

    def test_user_with_same_action_on_other_module_is_denied(self):
        for case in MODULE_CASES:
            module, method, path, action = case
            with self.subTest(case=_case_id(case)):
                self._assert_denied(self._run(self.other_module[(module, action)], method, path))

    def test_expired_matching_role_is_denied(self):
        for case in MODULE_CASES:
            module, method, path, action = case
            with self.subTest(case=_case_id(case)):
                self._assert_denied(self._run(self.expired[(module, action)], method, path))

    def test_both_budget_mounts_are_gated_the_same_way(self):
        for prefix in ("/api/budgets", "/budgets"):
            with self.subTest(prefix=prefix):
                self.assertIsNone(
                    self._run(self.holders[("BUDGET_POOL", "VIEW")], "GET", prefix + "/pools/")
                )
                self._assert_denied(self._run(self.denied_user, "GET", prefix + "/pools/"))

    def test_patch_on_non_decision_routes_requires_edit_not_approve(self):
        holder = self.holders[("QUEUE", "VIEW")]  # placeholder user without QUEUE EDIT
        self._assert_denied(self._run(holder, "PATCH", "/api/csm/queues/1/"))

    # -- unmapped routes ------------------------------------------------------
    def test_unmapped_and_options_requests_are_allowed(self):
        self.assertIsNone(self._run(self.denied_user, "GET", "/api/csm/unknown/"))
        self.assertIsNone(self._run(self.denied_user, "OPTIONS", "/api/csm/queues/"))

    # -- internal escalation webhook ----------------------------------------
    # The webhook is anonymous and authenticated by its view's X-Internal-Token
    # check. process_view skips anonymous requests, so the webhook is not
    # affected. A header is never trusted by the middleware: an authenticated
    # user is gated whether or not X-Internal-Token is present.

    def test_anonymous_escalation_webhook_is_not_gated_with_or_without_header(self):
        self.assertIsNone(self._run(AnonymousUser(), "POST", "/api/budgets/escalate/"))
        self.assertIsNone(
            self._run(AnonymousUser(), "POST", "/budgets/escalate/", HTTP_X_INTERNAL_TOKEN="any-value")
        )

    def test_authenticated_user_is_gated_on_escalation_even_with_internal_token_header(self):
        for path in ("/api/budgets/escalate/", "/budgets/escalate/"):
            with self.subTest(path=path):
                self._assert_denied(self._run(self.denied_user, "POST", path))
                self._assert_denied(
                    self._run(self.denied_user, "POST", path, HTTP_X_INTERNAL_TOKEN="any-value")
                )

    def test_authenticated_user_with_escalation_permission_is_allowed(self):
        holder = self.holders[("BUDGET_ESCALATION", "EDIT")]
        self.assertIsNone(self._run(holder, "POST", "/api/budgets/escalate/"))

    # -- fail-open is preserved for the new modules ------------------------

    def test_permission_query_failure_fails_open_for_new_modules(self):
        # Permissions are resolved from a cached bundle first. Make that
        # fast path fail too so the PostgreSQL fallback (fail-open) is what runs.
        with patch(
            "access_control.middleware.authorization.get_user_permission_bundle",
            side_effect=RuntimeError("cache unavailable"),
        ), patch(
            "access_control.middleware.authorization.RolePermission",
            **{"objects.filter.side_effect": RuntimeError("boom")},
        ):
            self.assertIsNone(self._run(self.denied_user, "GET", "/api/csm/queues/"))


class PlannedRoutesNotEnforcedTest(_MiddlewareBase):
    """Shipped (default) behavior: planned routes keep their behavior from before the registry.

    No middleware 403 and no new AdminOverrideAudit rows for the seven planned
    modules or for the CAMPAIGN template/link routes, while the legacy ASSET and
    /api/campaigns/** routes stay enforced.
    """

    PLANNED_CASES = [case for case in MODULE_CASES if case[0] in PLANNED_MODULES] + [
        ("CAMPAIGN", "GET", "/api/campaign-templates/", "VIEW"),
        ("CAMPAIGN", "POST", "/api/campaign-task-links/", "EDIT"),
        ("CAMPAIGN", "GET", "/api/campaign-decision-links/", "VIEW"),
        ("CAMPAIGN", "POST", "/api/campaign-calendar-links/", "EDIT"),
    ]
    ACTIVE_CASES = [
        ("ASSET", "GET", "/api/assets/", "VIEW"),
        ("ASSET", "PATCH", "/api/assets/4/review/", "APPROVE"),
        ("CAMPAIGN", "GET", "/api/campaigns/", "VIEW"),
        ("CAMPAIGN", "DELETE", "/api/campaigns/abc/", "DELETE"),
    ]

    def test_default_enforcement_states(self):
        self.assertEqual(services.LEGACY_ENFORCEMENT, {"ASSET": "active", "CAMPAIGN": "active"})
        self.assertEqual(set(services.RULE_TABLE_ENFORCEMENT.values()), {"planned"})

    def test_planned_routes_are_resolved_but_not_enforced(self):
        for module, method, path, action in self.PLANNED_CASES:
            with self.subTest(case=f"{module}:{method} {path}"):
                decision = services.resolve_policy(path, method)
                self.assertEqual((decision.module, decision.action), (module, action))
                self.assertEqual(decision.enforcement, "planned")
                self.assertFalse(decision.enforced)

    def test_planned_routes_never_return_403(self):
        users = (self.denied_user, self.other_action["QUEUE"], self.zero_role_user)
        for module, method, path, _action in self.PLANNED_CASES:
            for user in users:
                with self.subTest(case=f"{module}:{method} {path}", user=user.username):
                    self.assertIsNone(self._run(user, method, path))

    def test_planned_routes_write_no_override_audit_rows(self):
        before = AdminOverrideAudit.objects.count()
        for _module, method, path, _action in self.PLANNED_CASES:
            self._run(self.org_admin, method, path)
            self._run(self.superuser, method, path)
        self.assertEqual(AdminOverrideAudit.objects.count(), before)

    def test_escalation_header_is_irrelevant_while_planned(self):
        for path in ("/api/budgets/escalate/", "/budgets/escalate/"):
            self.assertIsNone(self._run(self.denied_user, "POST", path))
            self.assertIsNone(self._run(self.denied_user, "POST", path, HTTP_X_INTERNAL_TOKEN="any-value"))

    def test_legacy_asset_and_campaign_routes_stay_enforced(self):
        for module, method, path, _action in self.ACTIVE_CASES:
            with self.subTest(case=f"{module}:{method} {path}"):
                self.assertTrue(services.resolve_policy(path, method).enforced)
                self._assert_denied(self._run(self.denied_user, method, path))

    def test_legacy_routes_still_write_override_audit_rows(self):
        before = AdminOverrideAudit.objects.count()
        self._run(self.superuser, "GET", "/api/assets/")
        self.assertGreater(AdminOverrideAudit.objects.count(), before)
