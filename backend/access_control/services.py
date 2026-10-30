import math
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, NamedTuple, Optional, Pattern, Tuple, TypedDict

from django.db.models import Q

from django.core.cache import cache
from django.db import connection, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import Permission, Project, ProjectMember, Role
from access_control.models import RolePermission, UserRole


class PermissionBundle(TypedDict):
    permissions: List[str]
    has_any_role: bool


def permission_cache_key(schema_name: str, user_id: int) -> str:
    return f"access_control:permission_bundle:{schema_name}:{user_id}"


def invalidate_user_permission_cache(schema_name: str, user_id: int) -> None:
    try:
        cache.delete(permission_cache_key(schema_name, user_id))
    except Exception:
        pass


def _set_permission_cache(
    key: str,
    bundle: PermissionBundle,
    timeout,
) -> None:
    try:
        cache.set(key, bundle, timeout)
    except Exception:
        pass


def get_user_permission_bundle(
    user_id: int,
    schema_name: str,
) -> PermissionBundle:
    key = permission_cache_key(schema_name, user_id)

    try:
        cached = cache.get(key)
    except Exception:
        cached = None

    if cached is not None:
        return cached

    now = timezone.now()

    user_roles = list(
        UserRole.objects.filter(user_id=user_id).values(
            "role_id",
            "valid_from",
            "valid_to",
        )
    )

    active_role_ids = []
    next_transition_seconds = None

    for user_role in user_roles:
        valid_from = user_role["valid_from"]
        valid_to = user_role["valid_to"]

        if valid_from <= now and (
            valid_to is None or valid_to >= now
        ):
            active_role_ids.append(user_role["role_id"])

            if valid_to is not None:
                seconds = max(
                    1,
                    math.ceil((valid_to - now).total_seconds()),
                )
                if (
                    next_transition_seconds is None
                    or seconds < next_transition_seconds
                ):
                    next_transition_seconds = seconds

        elif valid_from > now:
            seconds = max(
                1,
                math.ceil((valid_from - now).total_seconds()),
            )
            if (
                next_transition_seconds is None
                or seconds < next_transition_seconds
            ):
                next_transition_seconds = seconds

    permission_pairs = RolePermission.objects.filter(
        role_id__in=active_role_ids
    ).values_list(
        "permission__module",
        "permission__action",
    )

    bundle: PermissionBundle = {
        "permissions": sorted(
            f"{module}:{action}"
            for module, action in permission_pairs
        ),
        "has_any_role": bool(user_roles),
    }

    timeout = cache.default_timeout
    if next_transition_seconds is not None:
        timeout = (
            next_transition_seconds
            if timeout is None
            else min(timeout, next_transition_seconds)
        )

    if connection.in_atomic_block:
        transaction.on_commit(
            lambda: _set_permission_cache(
                key,
                bundle,
                timeout,
            )
        )
    else:
        _set_permission_cache(
            key,
            bundle,
            timeout,
        )

    return bundle


def _current_schema_name() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        search_path = cursor.fetchone()[0]

    return search_path.split(",")[0].strip().strip('"')


def _invalidate_user_after_commit(user_id: int) -> None:
    try:
        schema_name = _current_schema_name()
    except Exception:
        return

    transaction.on_commit(
        lambda: invalidate_user_permission_cache(
            schema_name,
            user_id,
        )
    )


def _invalidate_role_users_after_commit(role_id: int) -> None:
    try:
        schema_name = _current_schema_name()
        user_ids = list(
            UserRole.objects.filter(role_id=role_id)
            .values_list("user_id", flat=True)
            .distinct()
        )
    except Exception:
        return

    def invalidate():
        for user_id in user_ids:
            invalidate_user_permission_cache(
                schema_name,
                user_id,
            )

    transaction.on_commit(invalidate)


@receiver(post_save, sender=UserRole)
@receiver(post_delete, sender=UserRole)
def invalidate_permission_cache_on_user_role_change(
    sender,
    instance,
    **kwargs,
):
    _invalidate_user_after_commit(instance.user_id)


@receiver(post_save, sender=RolePermission)
@receiver(post_delete, sender=RolePermission)
def invalidate_permission_cache_on_role_permission_change(
    sender,
    instance,
    **kwargs,
):
    _invalidate_role_users_after_commit(instance.role_id)


@receiver(post_save, sender=Role)
def invalidate_permission_cache_on_role_change(
    sender,
    instance,
    **kwargs,
):
    _invalidate_role_users_after_commit(instance.id)


MODULE_LABELS = {
    "ASSET": "Asset Management",
    "CAMPAIGN": "Campaign Execution",
    "BUDGET_REQUEST": "Budget Request",
    "BUDGET_POOL": "Budget Pool",
    "BUDGET_ESCALATION": "Budget Escalation",
    "QUEUE": "Queue",
    "SUPPORT_TEAM": "Support Team",
    "TICKET": "Ticket",
    "INVITATION": "Invitation",
    "REPORT": "Report",
}


class MatrixRole(TypedDict):
    id: str
    name: str
    description: str
    rank: int
    organizationId: Optional[str]
    isReadOnly: bool


class MatrixPermission(TypedDict):
    id: str
    name: str
    description: str
    module: str
    action: str


class MatrixWarning(TypedDict, total=False):
    code: str
    message: str
    roleId: str
    permissionId: str


class ProjectPermissionMatrixResponse(TypedDict):
    projectId: str
    projectName: str
    organizationId: Optional[str]
    roles: List[MatrixRole]
    permissions: List[MatrixPermission]
    matrix: Dict[str, Dict[str, bool]]
    warnings: List[MatrixWarning]


def permission_key(permission: Permission) -> str:
    return f"{permission.module.lower()}_{permission.action.lower()}"


def serialize_permission(permission: Permission) -> MatrixPermission:
    module_label = MODULE_LABELS.get(permission.module, permission.module)
    action_label = permission.action.capitalize()
    return {
        "id": permission_key(permission),
        "name": f"{action_label} {module_label}",
        "description": f"{action_label} access for {module_label} module",
        "module": module_label,
        "action": action_label,
    }


def serialize_role(role: Role) -> MatrixRole:
    return {
        "id": str(role.id),
        "name": role.name,
        "description": f"Role: {role.name}",
        "rank": role.level,
        "organizationId": str(role.organization_id) if role.organization_id else None,
        "isReadOnly": False,
    }


def get_project_permission_matrix(project_id: int) -> ProjectPermissionMatrixResponse:
    project = Project.objects.select_related("organization").get(
        id=project_id,
        is_deleted=False,
    )

    roles = list(
        Role.objects.filter(is_deleted=False)
        .filter(organization__isnull=True)
        .order_by("level", "name")
    )
    if project.organization_id:
        roles.extend(
            Role.objects.filter(
                organization_id=project.organization_id,
                is_deleted=False,
            ).order_by("level", "name")
        )

    permissions = list(Permission.objects.filter(is_deleted=False).order_by("module", "action"))
    permission_ids_by_pk = {permission.id: permission_key(permission) for permission in permissions}
    matrix: Dict[str, Dict[str, bool]] = {
        str(role.id): {permission_key(permission): False for permission in permissions}
        for role in roles
    }

    role_permissions = (
        RolePermission.objects.select_related("permission", "role")
        .filter(role__in=roles, permission__in=permissions, is_deleted=False)
    )
    for role_permission in role_permissions:
        role_id = str(role_permission.role_id)
        permission_id = permission_ids_by_pk.get(role_permission.permission_id)
        if role_id in matrix and permission_id:
            matrix[role_id][permission_id] = True

    warnings: List[MatrixWarning] = []
    if not project.organization_id:
        warnings.append({
            "code": "PROJECT_WITHOUT_ORGANIZATION",
            "message": "This project is not linked to an organization, so only system roles are included.",
        })

    role_names = {role.name.lower(): str(role.id) for role in roles}
    project_members = ProjectMember.objects.filter(
        project=project,
        is_active=True,
        is_deleted=False,
    )
    for member in project_members:
        if member.role and member.role.lower() not in role_names:
            warnings.append({
                "code": "PROJECT_MEMBER_ROLE_UNMAPPED",
                "message": f'Project member role "{member.role}" does not match an access-control role.',
            })

    granted_permission_role_ids = set(
        role_permissions.values_list("role_id", flat=True).distinct()
    )
    for role in roles:
        if role.id not in granted_permission_role_ids:
            warnings.append({
                "code": "ROLE_WITHOUT_PERMISSIONS",
                "message": f'Role "{role.name}" has no explicit permissions.',
                "roleId": str(role.id),
            })

    return {
        "projectId": str(project.id),
        "projectName": project.name,
        "organizationId": str(project.organization_id) if project.organization_id else None,
        "roles": [serialize_role(role) for role in roles],
        "permissions": [serialize_permission(permission) for permission in permissions],
        "matrix": matrix,
        "warnings": warnings,
    }
# ---------------------------------------------------------------------------
# Authorization policy registry
# ---------------------------------------------------------------------------
# Path resolution below is pure (no database, no request object): it maps
# (path, method) to (module, action) plus an enforcement state.
#
# - ASSET and /api/campaigns/** use the legacy resolver, unchanged (active).
# - Every other route is described by POLICY_RULES (planned: resolved, not
#   enforced by the middleware).
# - EXEMPT_ROUTES classify routes that no Permission module covers; see there.
#
# Action model for POLICY_RULES: read VIEW; create/update/workflow EDIT;
# approval decision APPROVE; deletion DELETE. The legacy resolver keeps its
# historical map (PATCH -> APPROVE) as the one exception.

#: Legacy resolver only: path segment after "api" -> module.
MODULE_PATH_MAP: Dict[str, str] = {
    "assets": "ASSET",
    "campaigns": "CAMPAIGN",
}

#: Legacy resolver only: HTTP method -> default action.
METHOD_ACTION_MAP: Dict[str, str] = {
    "GET": "VIEW",
    "POST": "EDIT",
    "PUT": "EDIT",
    "PATCH": "APPROVE",
    "DELETE": "DELETE",
}

#: Legacy resolver only: a 4th path segment in this map forces the action.
#: No current ASSET/CAMPAIGN route has that shape; kept for compatibility.
ACTION_KEYWORD_OVERRIDES: Dict[str, str] = {
    "approve": "APPROVE",
    "export": "EXPORT",
}

#: Role level that grants the org-admin bypass in process_view.
ORG_ADMIN_ROLE_LEVEL = 2


def _legacy_resolve(path: str, method: str) -> Tuple[Optional[str], Optional[str]]:
    """Legacy ASSET/CAMPAIGN resolver: the inline logic process_view used
    before the registry, unchanged. Returns (None, None) for an unknown module and
    (module, None) when the method has no action. Pure.
    """
    parts = path.strip("/").split("/")
    module_key: Optional[str] = None
    action_key: Optional[str] = None

    if len(parts) >= 2 and parts[0] == "api":
        module_key = MODULE_PATH_MAP.get(parts[1])
        if module_key is not None:
            if len(parts) >= 4 and parts[3] in ACTION_KEYWORD_OVERRIDES:
                action_key = ACTION_KEYWORD_OVERRIDES[parts[3]]
            else:
                action_key = METHOD_ACTION_MAP.get(method)

    return module_key, action_key


# ---------------------------------------------------------------------------
# Explicit rule table for the remaining modules
# ---------------------------------------------------------------------------

_TEMPLATE_VARIABLE = re.compile(r"<(?:\w+:)?\w+>")

#: Optional trailing slash. A `.json` format suffix is not matched (see the
#: policy doc, future hardening).
_TEMPLATE_TAIL = r"/?"


def _compile_template(template: str) -> Pattern:
    """Compile a path template ("queues/<pk>/agents/") into an anchored regex.
    "<...>" matches one path segment; a trailing "/**" matches everything
    beneath (exemptions only).
    """
    wildcard = template.endswith("/**")
    body = template[:-3] if wildcard else template.strip("/")
    out: List[str] = []
    position = 0
    for match in _TEMPLATE_VARIABLE.finditer(body):
        out.append(re.escape(body[position:match.start()]))
        out.append("[^/]+")
        position = match.end()
    out.append(re.escape(body[position:]))
    regex = "".join(out)
    if wildcard:
        return re.compile("^" + regex + r"(?:[/.].*)?$")
    return re.compile("^" + regex + _TEMPLATE_TAIL + "$")


@dataclass(frozen=True)
class PolicyRule:
    """One gated route: requests whose method is in `methods` and whose path
    matches `template` require `module` + `action`."""

    module: str
    action: str
    methods: FrozenSet[str]
    template: str
    pattern: Pattern


@dataclass(frozen=True)
class ExemptRoute:
    """A route that is deliberately NOT gated by AuthorizationMiddleware."""

    template: str
    pattern: Pattern
    reason: str
    methods: Optional[FrozenSet[str]] = None  # None = every method


ENFORCEMENT_ACTIVE = "active"
ENFORCEMENT_PLANNED = "planned"

#: Legacy resolver (/api/assets/**, /api/campaigns/**): enforced today.
LEGACY_ENFORCEMENT: Dict[str, str] = {
    "ASSET": ENFORCEMENT_ACTIVE,
    "CAMPAIGN": ENFORCEMENT_ACTIVE,
}

#: Enforcement state of POLICY_RULES routes, by module. All planned: resolved
#: and documented but not enforced until existing roles hold the required
#: RolePermission rows. CAMPAIGN here means templates and links only, not the
#: active /api/campaigns/**. A module missing here is treated as planned.
RULE_TABLE_ENFORCEMENT: Dict[str, str] = {
    "CAMPAIGN": ENFORCEMENT_PLANNED,
    "BUDGET_REQUEST": ENFORCEMENT_PLANNED,
    "BUDGET_POOL": ENFORCEMENT_PLANNED,
    "BUDGET_ESCALATION": ENFORCEMENT_PLANNED,
    "QUEUE": ENFORCEMENT_PLANNED,
    "SUPPORT_TEAM": ENFORCEMENT_PLANNED,
    "TICKET": ENFORCEMENT_PLANNED,
    "INVITATION": ENFORCEMENT_PLANNED,
}


class PolicyDecision(NamedTuple):
    """What a request requires, and whether it is enforced yet."""

    module: Optional[str]
    action: Optional[str]
    rule: Optional[PolicyRule]
    enforcement: Optional[str] = None

    @property
    def enforced(self) -> bool:
        """True only for a resolved (module, action) whose state is active."""
        return (
            self.module is not None
            and self.action is not None
            and self.enforcement == ENFORCEMENT_ACTIVE
        )


BUDGET_PREFIXES = ("api/budgets/", "budgets/")  # both mounts serve the same views
CSM_PREFIX = "api/csm/"
CORE_PREFIX = "api/core/"
API_PREFIX = "api/"

# (module, action, methods, path template relative to a prefix)
_RuleSpec = Tuple[str, str, Tuple[str, ...], str]

_BUDGET_RULE_SPECS: Tuple[_RuleSpec, ...] = (
    # start-review is a workflow step (EDIT), not an approval. Deleting also
    # needs EDIT in the view's own permission class.
    ("BUDGET_REQUEST", "VIEW", ("GET",), "requests/"),
    ("BUDGET_REQUEST", "EDIT", ("POST",), "requests/"),
    ("BUDGET_REQUEST", "VIEW", ("GET",), "requests/<int:pk>/"),
    ("BUDGET_REQUEST", "EDIT", ("PATCH",), "requests/<int:pk>/"),
    ("BUDGET_REQUEST", "DELETE", ("DELETE",), "requests/<int:pk>/"),
    ("BUDGET_REQUEST", "EDIT", ("POST",), "requests/<int:pk>/start-review/"),
    # UpdateAPIView also serves PUT, so both methods are the approval decision.
    ("BUDGET_REQUEST", "APPROVE", ("PUT", "PATCH"), "requests/<int:pk>/decision/"),
    ("BUDGET_POOL", "VIEW", ("GET",), "pools/"),
    ("BUDGET_POOL", "EDIT", ("POST",), "pools/"),
    ("BUDGET_POOL", "VIEW", ("GET",), "pools/<int:pk>/"),
    ("BUDGET_POOL", "EDIT", ("PATCH",), "pools/<int:pk>/"),
    ("BUDGET_POOL", "DELETE", ("DELETE",), "pools/<int:pk>/"),
    # escalate/ is an anonymous webhook authenticated by the view
    # (X-Internal-Token); the middleware never gates anonymous requests and
    # never reads that header. The rule applies only to an authenticated caller.
    ("BUDGET_ESCALATION", "EDIT", ("POST",), "escalate/"),
)

_CSM_RULE_SPECS: Tuple[_RuleSpec, ...] = (
    # QUEUE: queue CRUD, queue-scoped ticket listings, project queue list.
    ("QUEUE", "VIEW", ("GET",), "queues/"),
    ("QUEUE", "EDIT", ("POST",), "queues/"),
    ("QUEUE", "VIEW", ("GET",), "queues/<pk>/"),
    ("QUEUE", "EDIT", ("PUT", "PATCH"), "queues/<pk>/"),
    ("QUEUE", "DELETE", ("DELETE",), "queues/<pk>/"),
    ("QUEUE", "VIEW", ("GET",), "queues/<pk>/tickets/"),
    ("QUEUE", "VIEW", ("GET",), "queues/<pk>/ticket_counts/"),
    ("QUEUE", "VIEW", ("GET",), "projects/<int:project_id>/queues/"),
    ("QUEUE", "EDIT", ("POST",), "projects/<int:project_id>/queues/"),
    # SUPPORT_TEAM: queue team/agent assignment (who staffs a queue).
    ("SUPPORT_TEAM", "VIEW", ("GET",), "queues/<int:queue_id>/teams/"),
    ("SUPPORT_TEAM", "EDIT", ("POST",), "queues/<int:queue_id>/teams/"),
    ("SUPPORT_TEAM", "DELETE", ("DELETE",), "queues/<int:queue_id>/teams/<int:pk>/"),
    ("SUPPORT_TEAM", "VIEW", ("GET",), "queues/<int:queue_id>/agents/"),
    ("SUPPORT_TEAM", "EDIT", ("POST",), "queues/<int:queue_id>/agents/"),
    ("SUPPORT_TEAM", "DELETE", ("DELETE",), "queues/<int:queue_id>/agents/<int:pk>/"),
    # TICKET: claim and close are workflow steps (EDIT), not approvals.
    ("TICKET", "VIEW", ("GET",), "tickets/"),
    ("TICKET", "EDIT", ("POST",), "tickets/"),
    ("TICKET", "VIEW", ("GET",), "tickets/<pk>/"),
    ("TICKET", "EDIT", ("PATCH",), "tickets/<pk>/"),
    ("TICKET", "EDIT", ("POST",), "tickets/<pk>/claim/"),
    ("TICKET", "EDIT", ("POST",), "tickets/<pk>/close/"),
    # TICKET: ticket forms, statuses and status machine.
    ("TICKET", "VIEW", ("GET",), "ticket-forms/"),
    ("TICKET", "EDIT", ("POST",), "ticket-forms/"),
    ("TICKET", "VIEW", ("GET",), "ticket-forms/<pk>/"),
    ("TICKET", "EDIT", ("PUT", "PATCH"), "ticket-forms/<pk>/"),
    ("TICKET", "DELETE", ("DELETE",), "ticket-forms/<pk>/"),
    ("TICKET", "EDIT", ("PUT",), "ticket-forms/<pk>/fields/"),
    ("TICKET", "EDIT", ("POST",), "ticket-forms/<pk>/set-default/"),
    ("TICKET", "VIEW", ("GET",), "ticket-forms/<pk>/assignments/"),
    ("TICKET", "EDIT", ("PUT",), "ticket-forms/<pk>/assignments/"),
    ("TICKET", "VIEW", ("GET",), "ticket-statuses/"),
    ("TICKET", "EDIT", ("POST",), "ticket-statuses/"),
    ("TICKET", "VIEW", ("GET",), "ticket-statuses/<pk>/"),
    ("TICKET", "EDIT", ("PATCH",), "ticket-statuses/<pk>/"),
    ("TICKET", "DELETE", ("DELETE",), "ticket-statuses/<pk>/"),
    ("TICKET", "VIEW", ("GET",), "ticket-status-machine/"),
    ("TICKET", "EDIT", ("PUT",), "ticket-status-machine/"),
    ("TICKET", "EDIT", ("PATCH",), "ticket-status-machine/auto-resolve/"),
)

_CORE_RULE_SPECS: Tuple[_RuleSpec, ...] = (
    # INVITATION: management routes. approve and reject are both APPROVE (the
    # two outcomes of one decision). Invitee self-service is in EXEMPT_ROUTES.
    ("INVITATION", "EDIT", ("POST",), "organizations/<str:org_id>/invitations/"),
    ("INVITATION", "VIEW", ("GET",), "organizations/<str:org_id>/invitations/list/"),
    ("INVITATION", "DELETE", ("DELETE",), "organizations/<str:org_id>/invitations/<int:invitation_id>/"),
    ("INVITATION", "EDIT", ("POST",), "invitations/<int:invitation_id>/resend/"),
    ("INVITATION", "VIEW", ("GET",), "projects/<str:project_id>/invitations/"),
    ("INVITATION", "VIEW", ("GET",), "projects/<str:project_id>/invitations/pending-approval/"),
    ("INVITATION", "APPROVE", ("POST",), "projects/<str:project_id>/invitations/<int:invitation_id>/approve/"),
    ("INVITATION", "APPROVE", ("DELETE",), "projects/<str:project_id>/invitations/<int:invitation_id>/reject/"),
)


_CAMPAIGN_RULE_SPECS: Tuple[_RuleSpec, ...] = (
    # CAMPAIGN: templates and link resources (outside the legacy /api/campaigns/).
    ("CAMPAIGN", "VIEW", ("GET",), "campaign-templates/"),
    ("CAMPAIGN", "EDIT", ("POST",), "campaign-templates/"),
    ("CAMPAIGN", "VIEW", ("GET",), "campaign-templates/<id>/"),
    ("CAMPAIGN", "EDIT", ("PUT", "PATCH"), "campaign-templates/<id>/"),
    ("CAMPAIGN", "DELETE", ("DELETE",), "campaign-templates/<id>/"),
    ("CAMPAIGN", "EDIT", ("POST",), "campaign-templates/<id>/create-campaign/"),
    ("CAMPAIGN", "VIEW", ("GET",), "campaign-task-links/"),
    ("CAMPAIGN", "EDIT", ("POST",), "campaign-task-links/"),
    ("CAMPAIGN", "DELETE", ("DELETE",), "campaign-task-links/<id>/"),
    ("CAMPAIGN", "VIEW", ("GET",), "campaign-decision-links/"),
    ("CAMPAIGN", "EDIT", ("POST",), "campaign-decision-links/"),
    ("CAMPAIGN", "VIEW", ("GET",), "campaign-calendar-links/"),
    ("CAMPAIGN", "EDIT", ("POST",), "campaign-calendar-links/"),
)


def _build_rules() -> Tuple[PolicyRule, ...]:
    rules: List[PolicyRule] = []

    def add(prefix: str, spec: _RuleSpec) -> None:
        module, action, methods, tail = spec
        template = prefix + tail
        rules.append(
            PolicyRule(
                module=module,
                action=action,
                methods=frozenset(methods),
                template=template,
                pattern=_compile_template(template),
            )
        )

    for prefix in BUDGET_PREFIXES:
        for spec in _BUDGET_RULE_SPECS:
            add(prefix, spec)
    for spec in _CSM_RULE_SPECS:
        add(CSM_PREFIX, spec)
    for spec in _CORE_RULE_SPECS:
        add(CORE_PREFIX, spec)
    for spec in _CAMPAIGN_RULE_SPECS:
        add(API_PREFIX, spec)
    return tuple(rules)


#: Ordered, explicit rule table for the non-legacy modules. First match wins.
POLICY_RULES: Tuple[PolicyRule, ...] = _build_rules()

# (template, reason, methods or None)
_EXEMPT_SPECS: Tuple[Tuple[str, str, Optional[Tuple[str, ...]]], ...] = tuple(
    [
        *(
            (prefix + "ad-channels/**", "Reference data; no Permission module.", None)
            for prefix in BUDGET_PREFIXES
        ),
        (CORE_PREFIX + "invitations/accept-organization/", "Invitee self-service.", None),
        (CORE_PREFIX + "invitations/accept/", "Invitee self-service.", None),
        (CORE_PREFIX + "invitations/pending/", "Invitee self-service.", None),
        (CORE_PREFIX + "projects/<str:project_id>/roles/", "Role lookup; not an Invitation resource.", None),
        (CSM_PREFIX, "DRF API root.", ("GET",)),
        (CSM_PREFIX + ".<format>", "DRF API root (format suffix).", ("GET",)),
        (CSM_PREFIX + "customer-users/**", "Customer user admin; no Permission module.", None),
        (CSM_PREFIX + "projects/<int:project_id>/customer-users/", "Customer user admin; no Permission module.", None),
        (CSM_PREFIX + "notifications/**", "Per-user data; no Permission module.", None),
        (CSM_PREFIX + "conversations/**", "CSM-specific permissions; no Permission module.", None),
        (CSM_PREFIX + "templates/**", "Quick replies; no Permission module.", None),
        (CSM_PREFIX + "template-tags/**", "Quick replies; no Permission module.", None),
        (CSM_PREFIX + "support-projects/**", "CSM admin; no Permission module.", None),
        (CSM_PREFIX + "work-types/**", "CSM admin; no Permission module.", None),
        (CSM_PREFIX + "guidance/**", "CSM content; no Permission module.", None),
        (CSM_PREFIX + "support-channels/**", "CSM admin; no Permission module.", None),
        (CSM_PREFIX + "sla-policy/**", "CSM admin; no Permission module.", None),
        (CSM_PREFIX + "business-hours-calendars/**", "CSM admin; no Permission module.", None),
        (CSM_PREFIX + "quality/**", "Supervisor-only; no Permission module.", None),
        (CSM_PREFIX + "routing-rules/**", "CSM routing; project-scoped view permissions; no Permission module.", None),
        (CSM_PREFIX + "routing-sandbox/**", "CSM routing; project-scoped view permissions; no Permission module.", None),
    ]
)

#: Classification of routes that no Permission module covers. This is
#: future-policy documentation, not a runtime bypass: the middleware consults
#: only resolve_policy(), planned routes are not enforced today, and unmapped
#: routes are never gated. The completeness test requires every in-scope route
#: to be mapped by a rule or classified here.
EXEMPT_ROUTES: Tuple[ExemptRoute, ...] = tuple(
    ExemptRoute(
        template=template,
        pattern=_compile_template(template),
        reason=reason,
        methods=frozenset(methods) if methods else None,
    )
    for template, reason, methods in _EXEMPT_SPECS
)


def resolve_policy(path: str, method: str) -> PolicyDecision:
    """Resolve what a request requires and whether that is enforced yet.

    Legacy resolver first (active), then POLICY_RULES (state from
    RULE_TABLE_ENFORCEMENT). A path match without a method match returns
    (module, None); no match returns (None, None). Resolution never depends
    on the enforcement state. Pure.
    """
    module_key, action_key = _legacy_resolve(path, method)
    if module_key is not None:
        return PolicyDecision(
            module_key, action_key, None,
            LEGACY_ENFORCEMENT.get(module_key, ENFORCEMENT_PLANNED),
        )

    relative = path.lstrip("/")
    path_only_module: Optional[str] = None
    for rule in POLICY_RULES:
        if rule.pattern.match(relative):
            if method in rule.methods:
                return PolicyDecision(
                    rule.module, rule.action, rule,
                    RULE_TABLE_ENFORCEMENT.get(rule.module, ENFORCEMENT_PLANNED),
                )
            if path_only_module is None:
                path_only_module = rule.module
    return PolicyDecision(path_only_module, None, None)


def resolve_module_and_action(path: str, method: str) -> Tuple[Optional[str], Optional[str]]:
    """(module, action) of resolve_policy(); ignores the enforcement state."""
    decision = resolve_policy(path, method)
    return decision.module, decision.action


def is_explicitly_exempt(path: str, method: str) -> bool:
    """Whether (path, method) is classified in EXEMPT_ROUTES (used by tests)."""
    relative = path.lstrip("/")
    for route in EXEMPT_ROUTES:
        if route.pattern.match(relative) and (route.methods is None or method in route.methods):
            return True
    return False


def get_user_current_org_id(user):
    """Organization the tenant middleware resolves for `user`
    (current_organization, falling back to the legacy organization FK)."""
    return getattr(user, "current_organization_id", None) or getattr(user, "organization_id", None)


def user_is_current_org_admin(user) -> bool:
    """Org-wide Team management. True only for a valid, non-deleted
    level-2 role of the user's CURRENT organization that is not scoped to a
    Team. Runs under the request's tenant schema. A generic
    OrganizationMembership 'admin' label is deliberately not enough. The
    Asset/Campaign middleware bypass is unchanged.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    org_id = get_user_current_org_id(user)
    if not org_id:
        return False
    now = timezone.now()
    return UserRole.objects.filter(
        user=user,
        is_deleted=False,
        team__isnull=True,
        role__is_deleted=False,
        role__organization_id=org_id,
        role__level=ORG_ADMIN_ROLE_LEVEL,
        valid_from__lte=now,
    ).filter(Q(valid_to__gte=now) | Q(valid_to__isnull=True)).exists()


def user_can_manage_all_teams(user) -> bool:
    """The single signal exposed to the frontend (`can_manage_all_teams`
    on /auth/me/). True when the backend will accept the org-wide Team
    endpoints for this user: a superuser or a strict current-organization
    Level-2 admin (user_is_current_org_admin), and an organization context
    exists (the list/create endpoints need one).

    Unlike the request-time decorator this may run outside the tenant schema
    (login, token refresh), so it selects the organization's schema itself and
    restores the previous search_path, the same approach as
    core.admin_utils.is_org_admin.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    org = getattr(user, "current_organization", None) or getattr(user, "organization", None)
    if org is None:
        return False
    if user.is_superuser:
        return True

    from django.db import connection, transaction
    from core.services.tenant import slug_to_schema_name

    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        original_path = cursor.fetchone()[0]
        cursor.execute(f"SET search_path TO {slug_to_schema_name(org.slug)}, public")
    try:
        with transaction.atomic():
            return user_is_current_org_admin(user)
    except Exception:
        return False
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {original_path}")
