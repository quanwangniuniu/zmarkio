from django.http import JsonResponse
from django.db import connection
from core.models import Organization
from core.services.tenant import slug_to_schema_name
from access_control.models import AdminOverrideAudit
from access_control.services import get_user_permission_bundle
from typing import Callable
from functools import wraps
from core.models import TeamMember, TeamRole

class AuthorizationMiddleware:
    """
    A simple middleware example for enforcing permissions based on URL path and HTTP method.
    Assumes the URL prefix contains the module (e.g., 'assets' maps to ASSET),
    and HTTP methods map to actions (GET→VIEW, POST→EDIT, etc.).
    """
    METHOD_ACTION_MAP = {
        'GET': 'VIEW',
        'POST': 'EDIT',
        'PUT': 'EDIT',
        'PATCH': 'APPROVE',
        'DELETE': 'DELETE',
    }

    # Explicit URL-segment → module mapping, replacing the old raw.rstrip('s')
    # guess (which mangled anything not a simple plural, e.g. 'canvas' ->
    # 'CANVA', 'sms' -> 'SM'). Only covers segments that map directly to a
    # Permission.MODULE_CHOICES value at the top-level /api/<segment>/ path —
    # e.g. budgets/queues/tickets/invitations live one level deeper
    # (/api/budgets/requests/, /api/csm/tickets/, /api/core/invitations/) and
    # were never reachable through this top-level check even under the old
    # logic, so they're intentionally not added here.
    MODULE_PATH_MAP = {
        'assets': 'ASSET',
        'campaigns': 'CAMPAIGN',
    }

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        # Skip permission checks here; handle them in process_view instead
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # If there is no authenticated user, skip or return 401 as needed
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return None

        # Parse the module/action from the path first, e.g. /api/assets/... → ASSET.
        # This must happen before the superuser/org-admin bypass checks below so we
        # know whether there's an actual permission gate to bypass: audit logging
        # only fires when a real gate would have applied here — e.g. not
        # for /api/auth/login/, which never reaches a module permission check.
        parts = request.path.strip('/').split('/')
        module_key = None
        action_key = None
        if len(parts) >= 2 and parts[0] == 'api':
            raw = parts[1]
            candidate_module = self.MODULE_PATH_MAP.get(raw)
            if candidate_module is not None:
                module_key = candidate_module
                # Special-case URL segments for approve/export actions
                if len(parts) >= 4 and parts[3] == 'approve':
                    action_key = 'APPROVE'
                elif len(parts) >= 4 and parts[3] == 'export':
                    action_key = 'EXPORT'
                else:
                    action_key = self.METHOD_ACTION_MAP.get(request.method, None)
        has_permission_gate = module_key is not None and action_key is not None

        # Superusers bypass module-level authorization. The @require_* decorators
        # in this module already short-circuit for superusers ("Org admin
        # (superuser) can always proceed"); this middleware was missing the same
        # bypass, so superusers were denied any module whose RBAC permissions
        # had not been seeded.
        if getattr(user, 'is_superuser', False):
            if has_permission_gate:
                self._log_override(request, user, 'SUPERUSER', module_key, action_key)
            return None

        request.is_org_admin = False

        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW search_path")
                search_path = cursor.fetchone()[0]
            schema_name = search_path.split(",")[0].strip().strip('"')

            bundle = get_user_permission_bundle(user.id, schema_name)

            if bundle["is_org_admin"]:
                request.is_org_admin = True
                if has_permission_gate:
                    self._log_override(
                        request,
                        user,
                        'ORG_ADMIN',
                        module_key,
                        action_key,
                    )
                return None

            if not has_permission_gate:
                return None

            required_permission = f"{module_key}:{action_key}"
            if required_permission in bundle["permissions"]:
                return None

            if not bundle["has_any_role"]:
                return None

        except Exception:
            # Preserve the existing fail-open behavior for organizations where
            # RBAC tables are not available or permission resolution fails.
            return None

        return JsonResponse({'detail': 'Permission denied'}, status=403)

    def _resolve_org_id_from_search_path(self):
        """
        Resolve the Organization whose tenant schema matches the DB
        connection's *current* search_path, rather than trusting
        user.current_organization_id.

        TenantSchemaMiddleware runs before this middleware and has already
        issued `SET search_path TO org_xxx, public` for this request. Reading
        it back here — instead of the user's saved current_organization_id —
        guarantees the audit row's organization always matches the tenant
        schema the row is actually written into. user.current_organization_id
        can diverge from that: it's a value cached on the user object, and a
        request can be served against a different org's schema than the
        user's own "current" org (e.g. an org-scoped token/header switched
        the schema for just this request without updating the user row).
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute('SHOW search_path')
                search_path = cursor.fetchone()[0]
            schema_name = search_path.split(',')[0].strip().strip('"')
            if schema_name in ('public', '$user'):
                return None
            # slug_to_schema_name() isn't reliably reversible in general (it
            # collapses every non-alphanumeric character to '_'), so match by
            # recomputing the forward transform per org rather than parsing
            # the schema name back into a slug. This only runs on override
            # events (superuser/org-admin bypasses), which are rare, so a
            # full scan of organizations is not a hot-path concern.
            for org_id, slug in Organization.objects.filter(is_deleted=False).values_list('id', 'slug'):
                if slug_to_schema_name(slug) == schema_name:
                    return org_id
            return None
        except Exception:
            return None

    def _log_override(self, request, user, override_type, module_key, action_key):
        """
        Record an audit row when a superuser/org-admin bypasses the module
        permission check. Best-effort: a logging failure must never block
        the request the bypass already allowed.
        """
        try:
            org_id = self._resolve_org_id_from_search_path()
            AdminOverrideAudit.objects.create(
                user=user,
                organization_id=org_id,
                override_type=override_type,
                module=module_key,
                action=action_key,
                method=request.method,
                path=request.path,
                ip_address=request.META.get('REMOTE_ADDR'),
                reason=request.META.get('HTTP_X_OVERRIDE_REASON', ''),
            )
        except Exception:
            pass

    # Authorization decorator for team endpoints
    

    def team_permission_required(required_role="LEADER"):
        """
        Decorator to enforce team permission checks on view functions.
        Only users with the required role or org admins can proceed.
        """
        def decorator(view_func: Callable) -> Callable:
            @wraps(view_func)
            def _wrapped_view(request, team_id=None, *args, **kwargs):
                user = request.user
                if not user.is_authenticated:
                    return JsonResponse({'error': 'Authentication required'}, status=401)
                # Org admin (superuser) can always proceed
                if hasattr(user, 'is_superuser') and user.is_superuser:
                    return view_func(request, team_id=team_id, *args, **kwargs)
                # Check team membership and role
                if not team_id:
                    return JsonResponse({'error': 'team_id required'}, status=400)

                # CRITICAL: Team membership data may live in public schema,
                # so temporarily switch back if needed
                with connection.cursor() as cursor:
                    cursor.execute('SHOW search_path')
                    original_path = cursor.fetchone()[0]

                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO public')

                try:
                    membership = TeamMember.objects.filter(user=user, team_id=team_id).first()
                    if not membership:
                        return JsonResponse({'error': 'Permission denied: not a team member'}, status=403)
                    # Only allow if user has required role
                    if required_role == "LEADER" and membership.role_id != TeamRole.LEADER:
                        return JsonResponse({'error': 'Permission denied: must be team leader'}, status=403)
                finally:
                    # CRITICAL: Use f-string instead of %s parameter to avoid quoting the path
                    with connection.cursor() as cursor:
                        cursor.execute(f'SET search_path TO {original_path}')

                return view_func(request, team_id=team_id, *args, **kwargs)
            return _wrapped_view
        return decorator
