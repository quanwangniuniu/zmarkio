"""DRF permission classes for admin roles."""

from rest_framework.permissions import BasePermission
from core.admin_utils import is_org_admin


class IsOrgAdmin(BasePermission):
    """Allow access to organization admins (level 2 role)."""
    message = "Organization admin access required."

    def has_permission(self, request, view):
        return is_org_admin(request.user)


class IsCsmAccessAllowed(BasePermission):
    """Allow access to CSM admins (user_type='admin' in CustomerUser)."""
    message = "CSM admin access required."

    def has_permission(self, request, view):
        from core.admin_utils import is_csm_admin
        return is_csm_admin(request.user)


class IsCsmSupervisor(BasePermission):
    """Allow access to CSM supervisors and admins (plus Django staff)."""
    message = "Supervisor access required."

    def has_permission(self, request, view):
        from core.admin_utils import is_csm_supervisor
        return is_csm_supervisor(request.user)
