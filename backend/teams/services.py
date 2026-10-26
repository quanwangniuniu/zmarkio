"""
Team services using core models through compatibility layer
This maintains the existing API while using the new core models
"""

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
from django.db import IntegrityError, transaction
from django.db.models import Count

from core.models import OrganizationMembership, Team as _Team

TEAM_NAME_MAX_LENGTH = _Team._meta.get_field("name").max_length


class TeamServiceError(Exception):
    """Raised with an HTTP-ish status and machine-readable code."""

    def __init__(self, message, code, status):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def user_belongs_to_organization(user, organization_id):
    """Same-organization check for adding Team members. Mirrors how the
    tenant middleware resolves a user's organization, plus active
    OrganizationMembership rows."""
    current = getattr(user, "current_organization_id", None)
    if current == organization_id or (current is None and user.organization_id == organization_id):
        return True
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
    """Create a Team in `organization_id`. Soft-deleted Teams keep their name,
    so a duplicate (deleted or not) is a 409."""
    name = (name or "").strip() if isinstance(name, str) else ""
    if not name:
        raise TeamServiceError("name is required", "MISSING_NAME", 400)
    if len(name) > TEAM_NAME_MAX_LENGTH:
        raise TeamServiceError(
            f"name must be at most {TEAM_NAME_MAX_LENGTH} characters", "NAME_TOO_LONG", 400
        )
    if desc is not None and not isinstance(desc, str):
        raise TeamServiceError("desc must be a string", "INVALID_DESC", 400)
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
    """Soft-delete `team`. Callers must first check team_has_members_or_children.
    Other references are left untouched (hidden with the Team)."""
    with transaction.atomic():
        _Team.objects.filter(parent=team).update(parent=None)
        team.is_deleted = True
        team.save(update_fields=["is_deleted", "updated_at"])
