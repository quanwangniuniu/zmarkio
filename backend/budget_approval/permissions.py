from rest_framework import permissions
from django.conf import settings
import logging
from .models import BudgetRequestStatus
from .approver_access import (
    user_is_org_admin_for_budget_request,
    user_may_process_budget_approval,
)
from core.admin_utils import is_org_admin
from utils.rbac_utils import has_rbac_permission, require_user_context, user_has_team

logger = logging.getLogger(__name__)

class BudgetRequestPermission(permissions.BasePermission):
    """Permissions to view and manage budget requests and their approval history"""
    
    def has_permission(self, request, view):
        """Check if the user has permission to access the entire view"""

        if request is None or view is None:
            return False
    
        # Super admin bypass all permission checks
        if request.user.is_superuser:
            return True
    
        # Check required request headers
        # must have x-user-role
        # if user has team, must have x-team-id
        if not require_user_context(request, user_has_team(request.user)):
            return False
        
        # Get team_id if user has team
        team_id = request.headers.get('x-team-id') if user_has_team(request.user) else None
        
        # Get organization from user
        organization = getattr(request.user, 'organization', None)

        if organization is None:
            return request.user.is_authenticated

        # Check RBAC permissions based on action type or HTTP method
        action = getattr(view, 'action', None)

        if action == 'create' or (action is None and request.method == 'POST'):
            return has_rbac_permission(request.user, 'BUDGET_REQUEST', 'EDIT', organization, team_id)
        elif action == 'list' or (action is None and request.method == 'GET'):
            return has_rbac_permission(request.user, 'BUDGET_REQUEST', 'VIEW', organization, team_id)

        return True
    
    def has_object_permission(self, request, view, obj):
        """Check if the user has permission to access a specific object"""

        if request is None or view is None or obj is None:
            return False
        
        # Super admin bypass all object permission checks
        if request.user.is_superuser:
            return True
        
        # Get team_id if user has team
        team_id = request.headers.get('x-team-id') if user_has_team(request.user) else None
        
        # Get organization from the object, falling back to request.user.organization
        # when FK traversal fails due to multi-tenant schema context.
        organization = None
        if hasattr(obj, 'organization'):
            organization = obj.organization
        elif hasattr(obj, 'project'):
            try:
                organization = obj.project.organization
            except Exception:
                pass
        elif hasattr(obj, 'budget_pool'):
            try:
                organization = obj.budget_pool.project.organization
            except Exception:
                logger.warning(
                    "BudgetRequestPermission: cannot resolve organization for budget_request_id=%s "
                    "(user_id=%s); falling back to user.organization",
                    obj.pk, request.user.id, exc_info=True,
                )

        if organization is None:
            organization = getattr(request.user, 'organization', None)

        # Check RBAC permissions based on action type or HTTP method
        action = getattr(view, 'action', None)
        is_get_request = request.method == 'GET'
        is_retrieve_action = action == 'retrieve'
        
        # For GET request or retrieve action
        if is_retrieve_action or is_get_request:
            # Object owner always has permission to view
            if obj.requested_by == request.user:
                return True
            
            # Current approver always has permission to view
            if obj.current_approver == request.user:
                return True
            
            # Users with VIEW permission can view requests from their organization
            return has_rbac_permission(request.user, 'BUDGET_REQUEST', 'VIEW', organization, team_id)
            
        # For update or partial update
        elif action in ['update', 'partial_update'] or (action is None and request.method in ['PUT', 'PATCH']):
            # Owner can update their own requests
            if obj.requested_by == request.user:
                return True
            # Users with EDIT permission can update requests from their organization
            return has_rbac_permission(request.user, 'BUDGET_REQUEST', 'EDIT', organization, team_id)
        
        return False


class ApprovalPermission(permissions.BasePermission):
    """Permissions to approve or reject budget requests.

    This class (plus BudgetRequestService / make_approval) is the authorization
    gate. Frontend Approve/Reject visibility is not a security boundary.
    """
    
    def has_permission(self, request, view):
        """Check if the user has permission to access the decision API"""

        if request is None or view is None:
            return False
        
        # Super admin bypass all permission checks
        if request.user.is_superuser:
            return True
            
        # Check required request headers
        # must have x-user-role
        # if user has team, must have x-team-id
        if not require_user_context(request, user_has_team(request.user)):
            return False

        # MED-240: org-admin may reach the decision endpoint without APPROVE RBAC.
        # Object-level check still enforces same-org + override rules.
        if is_org_admin(request.user) or getattr(request, 'is_org_admin', False):
            return True
        
        # Get team_id if user has team
        team_id = request.headers.get('x-team-id') if user_has_team(request.user) else None
        
        # Get organization from user
        organization = getattr(request.user, 'organization', None)
        
        # Check if the user has approval permission
        return has_rbac_permission(request.user, 'BUDGET_REQUEST', 'APPROVE', organization, team_id)
    
    def has_object_permission(self, request, view, obj):
        """Check if the user has permission to approve a specific object"""
        # Handle None request (for direct permission class testing)
        if request is None:
            return False
        
        # Super admin bypass all object permission checks
        if request.user.is_superuser:
            return True

        # MED-240: chain approver OR same-org org-admin override
        if not user_may_process_budget_approval(request.user, obj):
            return False

        # Org-admin override does not require BUDGET_REQUEST.APPROVE RBAC
        if user_is_org_admin_for_budget_request(request.user, obj):
            return True
        
        # Get team_id if user has team
        team_id = request.headers.get('x-team-id') if user_has_team(request.user) else None
        
        # Get organization from the object, falling back to request.user.organization
        # when FK traversal fails due to multi-tenant schema context.
        organization = None
        if hasattr(obj, 'budget_pool'):
            try:
                organization = obj.budget_pool.project.organization
            except Exception:
                logger.warning(
                    "ApprovalPermission: cannot resolve organization for budget_request_id=%s "
                    "(user_id=%s); falling back to user.organization",
                    obj.pk, request.user.id, exc_info=True,
                )

        if organization is None:
            organization = getattr(request.user, 'organization', None)

        # Chain approver still needs APPROVE RBAC
        return has_rbac_permission(request.user, 'BUDGET_REQUEST', 'APPROVE', organization, team_id)


class BudgetPoolPermission(permissions.BasePermission):
    """Permissions to view and manage budget pools"""
    
    def has_permission(self, request, view):
        """Check if the user has permission to access budget pool operations"""

        if request is None or view is None:
            return False

        # Super admin bypass all permission checks
        if request.user.is_superuser:
            return True
            
        # Check required request headers
        if not require_user_context(request, user_has_team(request.user)):
            return False
        
        # Get team_id if user has team
        team_id = request.headers.get('x-team-id') if user_has_team(request.user) else None
        
        # Get organization from user
        organization = getattr(request.user, 'organization', None)

        # If no organization is configured, fall back to authenticated-only access
        if organization is None:
            return request.user.is_authenticated

        # Check RBAC permissions based on action type
        # Handle views that don't have action attribute (like UpdateAPIView)
        if hasattr(view, 'action'):
            if view.action == 'create':
                return has_rbac_permission(request.user, 'BUDGET_POOL', 'EDIT', organization, team_id)
            elif view.action == 'list':
                return has_rbac_permission(request.user, 'BUDGET_POOL', 'VIEW', organization, team_id)
        else:
            # For views without action attribute, check based on HTTP method
            if request.method == 'POST':
                return has_rbac_permission(request.user, 'BUDGET_POOL', 'EDIT', organization, team_id)
            elif request.method == 'GET':
                return has_rbac_permission(request.user, 'BUDGET_POOL', 'VIEW', organization, team_id)
            elif request.method in ['PUT', 'PATCH']:
                return has_rbac_permission(request.user, 'BUDGET_POOL', 'EDIT', organization, team_id)

        return True
    
    def has_object_permission(self, request, view, obj):
        """Check if the user has permission to access a specific budget pool"""

        if request is None or view is None or obj is None:
            return False

        # Super admin bypass all object permission checks
        if request.user.is_superuser:
            return True

        # Get team_id if user has team
        team_id = request.headers.get('x-team-id') if user_has_team(request.user) else None

        # Get organization from the object, falling back to request.user.organization
        # when FK traversal fails due to multi-tenant schema context.
        organization = None
        if hasattr(obj, 'project'):
            try:
                organization = obj.project.organization
            except Exception:
                pass

        if organization is None:
            organization = getattr(request.user, 'organization', None)

        # If no organization configured, fall back to authenticated-only access
        if organization is None:
            return request.user.is_authenticated

        # Check RBAC permissions based on action type with organization check
        if view.action == 'retrieve':
            return has_rbac_permission(request.user, 'BUDGET_POOL', 'VIEW', organization, team_id)
        elif view.action in ['update', 'partial_update', 'destroy']:
            return has_rbac_permission(request.user, 'BUDGET_POOL', 'EDIT', organization, team_id)

        return has_rbac_permission(request.user, 'BUDGET_POOL', 'EDIT', organization, team_id)


class EscalationPermission(permissions.BasePermission):
    """Internal system webhook permissions for escalation"""
    
    def has_permission(self, request, view):
        """Check if the request is from an internal system"""
        
        if request is None:
            return False
        
        logger = logging.getLogger(__name__)
        
        # Check if internal webhook is enabled
        if not getattr(settings, 'INTERNAL_WEBHOOK_ENABLED', True):
            logger.warning("Internal webhook access denied: feature is disabled")
            return False
        
        # Check for internal token header
        internal_token = request.headers.get('X-Internal-Token')
        if not internal_token:
            logger.warning("Internal webhook access denied: missing X-Internal-Token header")
            return False
        
        # Validate token against settings
        expected_token = getattr(settings, 'INTERNAL_WEBHOOK_TOKEN', None)
        if not expected_token:
            logger.error("Internal webhook configuration error: INTERNAL_WEBHOOK_TOKEN not set")
            return False
        
        if internal_token != expected_token:
            logger.warning("Internal webhook access denied: invalid token")
            return False
        
        # Token is valid
        logger.info("Internal webhook access granted")
        return True
    
    def has_object_permission(self, request, view, obj):
        """Internal webhooks do not require object-level permissions"""

        if request is None or view is None or obj is None:
            return False

        return False
    
