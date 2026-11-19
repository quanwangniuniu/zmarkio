"""
Convert meeting action items into tasks with immutable lineage (meeting + action item).

Keeps conversion logic out of views for testing and reuse.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction

from rest_framework import serializers

from core.models import ProjectMember
from meetings.models import Meeting, MeetingActionItem, MeetingTaskOrigin
from meetings.services import validate_meeting_for_origin_link
from org_customization.services import is_task_type_enabled
from task.models import Task

User = get_user_model()


def _ensure_not_already_converted(action_item: MeetingActionItem) -> None:
    try:
        _ = action_item.derived_task
    except ObjectDoesNotExist:
        return
    raise serializers.ValidationError(
        {"detail": "This action item has already been converted to a task."},
    )


def _resolve_owner(*, project, request_user, owner_id: int | None):
    if owner_id is None:
        return request_user
    try:
        owner = User.objects.get(pk=owner_id)
    except User.DoesNotExist:
        raise serializers.ValidationError({"owner_id": "User not found."})
    has_membership = ProjectMember.objects.filter(
        user=owner,
        project=project,
        is_active=True,
    ).exists()
    if not has_membership:
        raise serializers.ValidationError(
            {"owner_id": "Owner must be an active member of the project."},
        )
    return owner


def _resolve_approver(*, project, current_approver_id: int | None):
    if current_approver_id is None:
        return None
    try:
        approver = User.objects.get(pk=current_approver_id)
    except User.DoesNotExist:
        raise serializers.ValidationError({"current_approver_id": "User not found."})
    has_membership = ProjectMember.objects.filter(
        user=approver,
        project=project,
        is_active=True,
    ).exists()
    if not has_membership:
        raise serializers.ValidationError(
            {"current_approver_id": "Approver must be a member of the project."},
        )
    return approver


def _validate_task_type(task_type: str, project) -> str:
    valid = {c[0] for c in Task._meta.get_field("type").choices}
    if task_type not in valid:
        raise serializers.ValidationError(
            {"type": f"Invalid task type. Must be one of: {sorted(valid)}"},
        )
    if not is_task_type_enabled(project, task_type):
        raise serializers.ValidationError(
            {"type": "This task type is disabled for this project."},
        )
    return task_type


def _validate_priority(priority: str | None) -> str:
    if priority is None:
        return Task.Priority.MEDIUM
    valid = {c[0] for c in Task.Priority.choices}
    if priority not in valid:
        raise serializers.ValidationError(
            {"priority": f"Invalid priority. Must be one of: {sorted(valid)}"},
        )
    return priority


def convert_meeting_action_item_to_task(
    *,
    user,
    meeting: Meeting,
    action_item: MeetingActionItem,
    owner_id: int | None = None,
    due_date=None,
    priority: str | None = None,
    task_type: str = "execution",
    current_approver_id: int | None = None,
    create_as_draft: bool = False,
) -> Task:
    """
    Create a task from a single action item, link meeting origin + action-item lineage, optionally submit.
    """
    if action_item.meeting_id != meeting.id:
        raise serializers.ValidationError(
            {"action_item_id": "Action item does not belong to this meeting."},
        )

    validate_meeting_for_origin_link(
        meeting_id=meeting.id,
        project=meeting.project,
        user=user,
    )
    _ensure_not_already_converted(action_item)

    task_type = _validate_task_type(task_type, meeting.project)
    priority = _validate_priority(priority)
    owner = _resolve_owner(project=meeting.project, request_user=user, owner_id=owner_id)
    current_approver = _resolve_approver(
        project=meeting.project,
        current_approver_id=current_approver_id,
    )

    summary = (action_item.title or "").strip()[:255] or "Meeting action"
    description = (action_item.description or "").strip()

    with transaction.atomic():
        task = Task.objects.create(
            summary=summary,
            description=description or "",
            project=meeting.project,
            created_by=user,
            owner=owner,
            due_date=due_date,
            priority=priority,
            type=task_type,
            current_approver=current_approver,
            origin_action_item=action_item,
        )
        MeetingTaskOrigin.objects.create(meeting=meeting, task=task)
        if not create_as_draft:
            task.submit()
            task.save()

    return task


def bulk_convert_meeting_action_items(
    *,
    user,
    meeting: Meeting,
    items: list[dict[str, Any]],
) -> list[Task]:
    """
    All-or-nothing: validates every row, then creates all tasks in one transaction.
    """
    validate_meeting_for_origin_link(
        meeting_id=meeting.id,
        project=meeting.project,
        user=user,
    )

    action_item_ids = [int(x["action_item_id"]) for x in items]
    if len(set(action_item_ids)) != len(action_item_ids):
        raise serializers.ValidationError(
            {"items": "Duplicate action_item_id entries are not allowed."},
        )

    action_items = list(
        MeetingActionItem.objects.filter(
            meeting_id=meeting.id,
            id__in=action_item_ids,
        )
    )
    by_id = {a.id: a for a in action_items}
    missing = [i for i in action_item_ids if i not in by_id]
    if missing:
        raise serializers.ValidationError(
            {"items": f"Unknown action item id(s) for this meeting: {missing}."},
        )

    for ai in action_items:
        _ensure_not_already_converted(ai)

    tasks: list[Task] = []
    with transaction.atomic():
        for row in items:
            aid = int(row["action_item_id"])
            ai = by_id[aid]
            task_type = _validate_task_type(row["type"], meeting.project)
            priority = _validate_priority(row.get("priority"))
            owner = _resolve_owner(
                project=meeting.project,
                request_user=user,
                owner_id=row.get("owner_id"),
            )
            current_approver = _resolve_approver(
                project=meeting.project,
                current_approver_id=row.get("current_approver_id"),
            )
            summary = (ai.title or "").strip()[:255] or "Meeting action"
            description = (ai.description or "").strip()
            task = Task.objects.create(
                summary=summary,
                description=description or "",
                project=meeting.project,
                created_by=user,
                owner=owner,
                due_date=row.get("due_date"),
                priority=priority,
                type=task_type,
                current_approver=current_approver,
                origin_action_item=ai,
            )
            MeetingTaskOrigin.objects.create(meeting=meeting, task=task)
            tasks.append(task)

        for row, task in zip(items, tasks, strict=True):
            if not row.get("create_as_draft", False):
                task.submit()
                task.save()

    return tasks
