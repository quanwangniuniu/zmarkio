import math
from typing import Dict, List, Optional, Tuple, TypedDict

from django.db.models import Q

from django.core.cache import cache
from django.db import connection, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import OrganizationMembership, Permission, Project, ProjectMember, Role
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
# Authorization policy table
# ---------------------------------------------------------------------------
# Path resolution below is pure (no database, no request object): it maps
# (path, method) to (module, action) for the modules AuthorizationMiddleware
# enforces: ASSET and /api/campaigns/**.

#: Path segment after "api" -> module.
MODULE_PATH_MAP: Dict[str, str] = {
    "assets": "ASSET",
    "campaigns": "CAMPAIGN",
}

#: HTTP method -> default action.
METHOD_ACTION_MAP: Dict[str, str] = {
    "GET": "VIEW",
    "POST": "EDIT",
    "PUT": "EDIT",
    "PATCH": "APPROVE",
    "DELETE": "DELETE",
}

#: A 4th path segment in this map forces the action.
#: No current ASSET/CAMPAIGN route has that shape; kept for compatibility.
ACTION_KEYWORD_OVERRIDES: Dict[str, str] = {
    "approve": "APPROVE",
    "export": "EXPORT",
}

#: Role level that grants the org-admin bypass in process_view.
ORG_ADMIN_ROLE_LEVEL = 2


def resolve_module_and_action(path: str, method: str) -> Tuple[Optional[str], Optional[str]]:
    """The (module, action) a request requires: the logic process_view used
    inline before, unchanged. Returns (None, None) for an unknown module and
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


def get_user_current_org_id(user):
    """Organization the tenant middleware resolves for `user`
    (current_organization, falling back to the legacy organization FK)."""
    return getattr(user, "current_organization_id", None) or getattr(user, "organization_id", None)


def user_is_current_org_admin(user) -> bool:
    """Org-wide Team management. True only for an active member of the
    user's CURRENT organization who holds a valid, non-deleted level-2 role
    of that organization that is not scoped to a Team. Runs under the
    request's tenant schema. A generic OrganizationMembership 'admin' label
    is deliberately not enough. The Asset/Campaign middleware bypass is
    unchanged.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    org_id = get_user_current_org_id(user)
    if not org_id:
        return False
    if not OrganizationMembership.objects.filter(
        user=user, organization_id=org_id, is_active=True
    ).exists():
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
