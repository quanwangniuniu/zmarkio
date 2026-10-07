from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

from core.admin_utils import get_org_admin_org_ids
from csm.models import CustomerUser
from public_api.principal import ApiPrincipal


class HasApiScope(BasePermission):
    """
    The credential must hold `<view.api_resource>:read` for safe methods and
    `:write` otherwise. A write scope implies read.
    """

    message = 'This credential does not have the scope this request needs.'

    def has_permission(self, request, view):
        principal = request.user
        if not isinstance(principal, ApiPrincipal):
            return False
        write = f'{view.api_resource}:write'
        if request.method in SAFE_METHODS:
            return write in principal.scopes or f'{view.api_resource}:read' in principal.scopes
        return write in principal.scopes


def can_manage_integrations(user, organization_id):
    """Org admins and CSM admins of the organisation manage its credentials and webhooks."""
    if user.is_superuser:
        return True
    if organization_id in get_org_admin_org_ids(user):
        return True
    return CustomerUser.objects.filter(
        user=user,
        user_type='admin',
        is_active=True,
        organisation__organization_id=organization_id,
    ).exists()


def require_integrations_admin(user, organization_id):
    if not can_manage_integrations(user, organization_id):
        raise PermissionDenied('Only an organisation admin or CSM admin can manage API access and webhooks.')
