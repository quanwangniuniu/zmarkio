"""
Team services using core models through compatibility layer
This maintains the existing API while using the new core models
"""

from django.db import IntegrityError, transaction
from django.db.models import Count

from core.models import OrganizationMembership, Team as _Team

from .compatibility import (
    add_member,
    remove_member,
    assign_leader,
    change_member_role,
    user_can_manage_team,
    user_can_modify_team,
    is_same_organization,
    is_valid_team_hierarchy,
    create_team,
    update_team,
    delete_team,
    get_child_teams,
    get_parent_team,
    is_team_member,
    user_is_org_admin,
    is_team_leader,
    get_team_leader,
    get_team_members,
    TeamRole
)

# Re-export all functions for backward compatibility
__all__ = [
    'add_member',
    'remove_member', 
    'assign_leader',
    'change_member_role',
    'user_can_manage_team',
    'user_can_modify_team',
    'is_same_organization',
    'is_valid_team_hierarchy',
    'create_team',
    'update_team',
    'delete_team',
    'get_child_teams',
    'get_parent_team',
    'is_team_member',
    'user_is_org_admin',
    'is_team_leader',
    'get_team_leader',
    'get_team_members',
    'TeamRole',
    'TeamServiceError',
    'user_belongs_to_organization',
    'list_org_teams',
    'create_org_team',
    'soft_delete_team',
]


# ---------------------------------------------------------------------------
# Organization-wide Team management (Level-2 org admins)
# ---------------------------------------------------------------------------

TEAM_NAME_MAX_LENGTH = _Team._meta.get_field("name").max_length


class TeamServiceError(Exception):
    """Raised with an HTTP-ish status and machine-readable code."""

    def __init__(self, message, code, status):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def user_belongs_to_organization(user, organization_id):
    """Same-organization check for adding Team members: an active
    OrganizationMembership is required (the user's organization fields can
    outlive a removal)."""
    return OrganizationMembership.objects.filter(
        user=user, organization_id=organization_id, is_active=True
    ).exists()


def list_org_teams(organization_id, search=""):
    """Non-deleted Teams of one organization, optionally filtered by name."""
    qs = _Team.objects.filter(organization_id=organization_id, is_deleted=False)
    search = (search or "").strip()
    if search:
        qs = qs.filter(name__icontains=search)
    return qs.annotate(member_count=Count("members")).order_by("name")


def create_org_team(organization_id, name, desc=None):
    """Create a Team in `organization_id`; `name` and `desc` are validated by
    TeamCreateSerializer. Soft-deleted Teams keep their name, so a duplicate
    (deleted or not) is a 409."""
    if _Team.objects.filter(organization_id=organization_id, name=name).exists():
        raise TeamServiceError("A team with this name already exists", "TEAM_NAME_TAKEN", 409)
    try:
        with transaction.atomic():
            return _Team.objects.create(organization_id=organization_id, name=name, desc=desc or None)
    except IntegrityError:
        raise TeamServiceError("A team with this name already exists", "TEAM_NAME_TAKEN", 409)


def team_has_members_or_children(team):
    """Whether `team` still has members or non-deleted child Teams. Such a
    Team cannot be deleted."""
    from core.models import TeamMember
    return (
        TeamMember.objects.filter(team=team).exists()
        or _Team.objects.filter(parent=team, is_deleted=False).exists()
    )


def soft_delete_team(team):
    """Soft-delete `team`. Callers must first check team_has_members_or_children,
    which also guarantees it has no live child Teams to detach."""
    team.is_deleted = True
    team.save(update_fields=["is_deleted", "updated_at"])
