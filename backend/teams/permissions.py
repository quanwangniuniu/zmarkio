from rest_framework.permissions import BasePermission

from access_control.services import user_is_current_org_admin


class IsCurrentOrgAdmin(BasePermission):
    """Superusers and level-2 admins of the current organization."""

    def has_permission(self, request, view):
        return request.user.is_superuser or user_is_current_org_admin(request.user)
