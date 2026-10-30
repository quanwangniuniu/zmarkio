# campaigns/team_api/views.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.views import View
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
import json
from core.models import Team, TeamMember, TeamRole
from access_control.models import UserRole
from access_control.middleware.authorization import AuthorizationMiddleware
from access_control.services import get_user_current_org_id
from teams.services import (
    TeamServiceError,
    create_org_team,
    list_org_teams,
    soft_delete_team,
    team_has_members_or_children,
    user_belongs_to_organization,
)

# A valid login does not prove team membership, so these check the
# real TeamMember row. Members read; only Team Leaders add members, change
# roles or remove members.
team_member_required = AuthorizationMiddleware.team_permission_required(required_role="MEMBER")
team_leader_required = AuthorizationMiddleware.team_permission_required(required_role="LEADER")
# Level-2 org admins of the current organization (and superusers) bypass the
# membership check above and may also list/create/delete Teams.
org_admin_required = AuthorizationMiddleware.org_admin_required


def _get_team_or_404(request, team_id):
    """Non-deleted Team, restricted to the caller's organization when known
    (defence in depth on top of the per-organization tenant schema)."""
    filters = {'id': team_id, 'is_deleted': False}
    org_id = get_user_current_org_id(request.user)
    if org_id:
        filters['organization_id'] = org_id
    return get_object_or_404(Team, **filters)


def _team_payload(team, member_count=None):
    payload = {
        'id': team.id,
        'name': team.name,
        'desc': team.desc,
        'organization_id': team.organization_id,
        'parent_team_id': team.parent_id,
        'is_parent': team.is_parent,
        'created_at': team.created_at.isoformat(),
        'updated_at': team.updated_at.isoformat(),
    }
    if member_count is not None:
        payload['member_count'] = member_count
    return payload


    


@method_decorator(csrf_exempt, name='dispatch')
class TeamMemberAPIView(View):
    """Base view for team member operations"""
    
    def dispatch(self, request, *args, **kwargs):
        """Handle JSON parsing for all methods"""
        if request.content_type == 'application/json' and request.body:
            try:
                request.json = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({
                    'error': 'Invalid JSON in request body',
                    'code': 'INVALID_JSON'
                }, status=400)
        else:
            request.json = {}
        
        return super().dispatch(request, *args, **kwargs)

@method_decorator(csrf_exempt, name='dispatch')
class TeamMembersView(TeamMemberAPIView):
    """Handle team member operations: GET list, POST add member"""
    
    @method_decorator(team_member_required)
    def get(self, request, team_id):
        """GET /teams/:id/members - List team members"""
        team = _get_team_or_404(request, team_id)
        
        # Query TeamMember using team relationship
        memberships = TeamMember.objects.filter(team=team)
        
        members = []
        for membership in memberships:
            member_data = {
                'user_id': membership.user.id,
                'team_id': membership.team.id,
                'role_id': membership.role_id,
                'role_name': TeamRole.get_role_name(membership.role_id),
                'created_at': membership.created_at.isoformat(),
                'updated_at': membership.updated_at.isoformat()
            }
            members.append(member_data)
        
        return JsonResponse({
            'team_id': team.id,
            'team_name': team.name,
            'members': members,
            'member_count': len(members)
        })
    
    @method_decorator(team_leader_required)
    def post(self, request, team_id):
        """POST /teams/:id/members - Add user to team"""
        team = _get_team_or_404(request, team_id)
        
        # Validate required fields
        user_id = request.json.get('user_id')
        role_id = request.json.get('role_id', TeamRole.MEMBER)  # Default to MEMBER
        
        if not user_id:
            return JsonResponse({
                'error': 'user_id is required',
                'code': 'MISSING_USER_ID'
            }, status=400)
        
        # Validate role
        if not TeamRole.is_valid_role(role_id):
            return JsonResponse({
                'error': 'Invalid role_id',
                'code': 'INVALID_ROLE',
                'details': {
                    'provided_role_id': role_id,
                    'valid_roles': [TeamRole.LEADER, TeamRole.MEMBER]
                }
            }, status=400)
        
        # Check for duplicate membership
        from core.models import CustomUser
        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return JsonResponse({
                'error': 'User not found',
                'code': 'USER_NOT_FOUND'
            }, status=404)
        
        if not user_belongs_to_organization(user, team.organization_id):
            return JsonResponse({
                'error': 'User does not belong to this organization',
                'code': 'USER_NOT_IN_ORGANIZATION'
            }, status=400)
        
        existing_membership = TeamMember.objects.filter(
            user=user,
            team=team
        ).first()
        
        if existing_membership:
            return JsonResponse({
                'error': 'User is already a member of this team',
                'code': 'USER_ALREADY_MEMBER',
                'details': {
                    'user_id': user_id,
                    'team_id': team_id,
                    'existing_role': existing_membership.role_id
                }
            }, status=409)
        
        # Create new membership
        try:
            membership = TeamMember.objects.create(
                user=user,
                team=team,
                role_id=role_id
            )
            
            return JsonResponse({
                'message': 'User added to team successfully',
                'membership': {
                    'user_id': membership.user.id,
                    'team_id': membership.team.id,
                    'role_id': membership.role_id,
                    'role_name': TeamRole.get_role_name(membership.role_id),
                    'created_at': membership.created_at.isoformat()
                }
            }, status=201)
            
        except Exception as e:
            return JsonResponse({
                'error': 'Failed to add user to team',
                'code': 'ADD_MEMBER_FAILED',
                'details': str(e)
            }, status=500)

@method_decorator(csrf_exempt, name='dispatch')
class TeamMemberDetailView(TeamMemberAPIView):
    """Handle individual team member operations: PATCH update role, DELETE remove member"""
    
    @method_decorator(team_leader_required)
    def patch(self, request, team_id, user_id):
        """PATCH /teams/:id/members/:user_id - Update member role"""
        team = _get_team_or_404(request, team_id)
        
        # Validate role_id in request
        role_id = request.json.get('role_id')
        if not role_id:
            return JsonResponse({
                'error': 'role_id is required',
                'code': 'MISSING_ROLE_ID'
            }, status=400)
        
        if not TeamRole.is_valid_role(role_id):
            return JsonResponse({
                'error': 'Invalid role_id',
                'code': 'INVALID_ROLE',
                'details': {
                    'provided_role_id': role_id,
                    'valid_roles': [TeamRole.LEADER, TeamRole.MEMBER]
                }
            }, status=400)
        
        # Find and update membership
        from core.models import CustomUser
        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return JsonResponse({
                'error': 'User not found',
                'code': 'USER_NOT_FOUND'
            }, status=404)
        
        membership = TeamMember.objects.filter(
            user=user,
            team=team
        ).first()
        
        if not membership:
            return JsonResponse({
                'error': 'User is not a member of this team',
                'code': 'USER_NOT_MEMBER'
            }, status=404)
        
        # Update role
        membership.role_id = role_id
        membership.save()
        
        return JsonResponse({
            'message': 'Member role updated successfully',
            'membership': {
                'user_id': membership.user.id,
                'team_id': membership.team.id,
                'role_id': membership.role_id,
                'role_name': TeamRole.get_role_name(membership.role_id),
                'updated_at': membership.updated_at.isoformat()
            }
        })
    
    @method_decorator(team_leader_required)
    def delete(self, request, team_id, user_id):
        """DELETE /teams/:id/members/:user_id - Remove member from team"""
        team = _get_team_or_404(request, team_id)
        
        from core.models import CustomUser
        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return JsonResponse({
                'error': 'User not found',
                'code': 'USER_NOT_FOUND'
            }, status=404)
        
        membership = TeamMember.objects.filter(
            user=user,
            team=team
        ).first()
        
        if not membership:
            return JsonResponse({
                'error': 'User is not a member of this team',
                'code': 'USER_NOT_MEMBER'
            }, status=404)
        
        # Remove membership
        membership.delete()
        
        return JsonResponse({
            'message': 'User removed from team successfully'
        }, status=200)

@method_decorator(csrf_exempt, name='dispatch')
class TeamListCreateView(TeamMemberAPIView):
    """Organization-wide Team management (org admins / superusers only)"""

    @method_decorator(org_admin_required)
    def get(self, request):
        """GET /teams/?search= - List (and search) all Teams of the organization"""
        org_id = get_user_current_org_id(request.user)
        if not org_id:
            return JsonResponse({'error': 'Organization context required',
                                 'code': 'NO_ORGANIZATION'}, status=400)
        teams = list_org_teams(org_id, request.GET.get('search', ''))
        items = [_team_payload(t, t.member_count) for t in teams]
        return JsonResponse({'teams': items, 'team_count': len(items)})

    @method_decorator(org_admin_required)
    def post(self, request):
        """POST /teams/ - Create a Team in the caller's organization"""
        org_id = get_user_current_org_id(request.user)
        if not org_id:
            return JsonResponse({'error': 'Organization context required',
                                 'code': 'NO_ORGANIZATION'}, status=400)
        data = request.json if isinstance(request.json, dict) else {}
        try:
            team = create_org_team(org_id, data.get('name'), data.get('desc'))
        except TeamServiceError as exc:
            return JsonResponse({'error': exc.message, 'code': exc.code}, status=exc.status)
        return JsonResponse({'message': 'Team created successfully',
                             'team': _team_payload(team, 0)}, status=201)


# Additional helper view for team details with members
@method_decorator(csrf_exempt, name='dispatch')
class TeamDetailView(View):
    """GET /teams/:id - Get team details with members; DELETE (org admin) - soft delete"""

    @method_decorator(org_admin_required)
    def delete(self, request, team_id):
        """DELETE /teams/:id - Soft-delete a Team that has no members or child Teams"""
        team = _get_team_or_404(request, team_id)
        if team_has_members_or_children(team):
            return JsonResponse({
                'error': 'Cannot delete team with active members or child teams',
                'code': 'TEAM_NOT_EMPTY',
            }, status=409)
        soft_delete_team(team)
        return HttpResponse(status=204)
    
    @method_decorator(team_member_required)
    def get(self, request, team_id):
        """Get detailed team information with members"""
        team = _get_team_or_404(request, team_id)
        
        # Get team members using team relationship
        memberships = TeamMember.objects.filter(team=team)
        members = []
        for membership in memberships:
            # Get user roles for this member with time validity
            user_roles = UserRole.objects.filter(
                user=membership.user,
                team_id=team.id,
                is_deleted=False,
            ).select_related('role')

            user_role_items = [
                {
                    'id': ur.role.id,
                    'name': ur.role.name,
                    'level': ur.role.level,
                }
                for ur in user_roles
            ]

            member_data = {
                'user_id': membership.user.id,
                'username': membership.user.username,
                'email': membership.user.email,
                'role_id': membership.role_id,
                'role_name': TeamRole.get_role_name(membership.role_id),
                'user_roles': user_role_items,
                'created_at': membership.created_at.isoformat()
            }
            members.append(member_data)
        
        # Get child teams using parent relationship
        child_teams = Team.objects.filter(parent=team, is_deleted=False)
        child_team_list = []
        for child in child_teams:
            child_data = {
                'id': child.id,
                'name': child.name,
                'organization_id': child.organization.id,
                'desc': child.desc,
                'parent_team_id': child.parent.id if child.parent else None,
                'is_parent': child.is_parent,
                'created_at': child.created_at.isoformat(),
                'updated_at': child.updated_at.isoformat()
            }
            child_team_list.append(child_data)
        
        return JsonResponse({
            'team': {
                'id': team.id,
                'name': team.name,
                'organization_id': team.organization.id,
                'organization_name': team.organization.name,
                'desc': team.desc,
                'parent_team_id': team.parent.id if team.parent else None,
                'is_parent': team.is_parent,
                'created_at': team.created_at.isoformat(),
                'updated_at': team.updated_at.isoformat()
            },
            'members': members,
            'member_count': len(members),
            'child_teams': child_team_list,
            'child_team_count': len(child_team_list)
        })