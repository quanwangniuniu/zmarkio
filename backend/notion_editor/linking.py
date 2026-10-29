"""Assign a draft to a project the caller can use in the current tenant."""

from django.db.models import Q

from core.admin_utils import get_org_admin_org_ids
from core.models import Project, ProjectMember
from notion_editor.models import Draft, DraftProjectLink
from rest_framework.exceptions import NotFound


def accessible_projects(user):
    """Projects the user may attach a draft to.

    Same membership rule as ``ProjectViewSet.get_queryset``: an active
    project member, or an admin of the project's organization. Does not
    apply the list endpoint's ``active_only`` query param.
    """
    member_ids = ProjectMember.objects.filter(
        user=user,
        is_active=True,
    ).values_list('project_id', flat=True)
    org_ids = get_org_admin_org_ids(user)
    if org_ids:
        return Project.objects.filter(
            Q(id__in=member_ids) | Q(organization_id__in=org_ids)
        ).distinct()
    return Project.objects.filter(id__in=member_ids)


def link_draft_to_project(draft: Draft, user, project_id: int) -> DraftProjectLink:
    """Create or move the draft's project link. Saves so RAG signals run."""
    project = accessible_projects(user).filter(pk=project_id).first()
    if project is None:
        raise NotFound('Project not found.')

    try:
        link = draft.project_link
    except DraftProjectLink.DoesNotExist:
        return DraftProjectLink.objects.create(draft=draft, project=project)

    if link.project_id != project.id:
        link.project = project
        link.save()
    return link


def unlink_draft_from_project(draft: Draft) -> None:
    """Remove the link. ``delete()`` fires the signal that clears the index."""
    try:
        draft.project_link.delete()
    except DraftProjectLink.DoesNotExist:
        return
