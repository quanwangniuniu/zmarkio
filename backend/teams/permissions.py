from functools import wraps

from django.http import JsonResponse
from rest_framework.permissions import BasePermission

from access_control.services import user_is_current_org_admin


def _is_org_admin(user):
    return user.is_superuser or user_is_current_org_admin(user)


class IsCurrentOrgAdmin(BasePermission):
    """Superusers and level-2 admins of the current organization (DRF views)."""

    def has_permission(self, request, view):
        return _is_org_admin(request.user)


def org_admin_required(view_func):
    """The same rule for plain Django views (TeamDetailView.delete)."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        if _is_org_admin(user):
            return view_func(request, *args, **kwargs)
        return JsonResponse({'error': 'Permission denied: organization admin required'}, status=403)
    return _wrapped_view
