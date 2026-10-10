"""
Django signal handlers for Slack notifications.

This module:
 - Listens for model save events (Task, Decision, etc.)
 - Decides whether a notification should be sent (connection active, preference set)
 - Delegates message building to blocks.py
 - Sends via services.send_slack_message()
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from task.models import Task, ApprovalRecord, TaskComment
from decision.models import Decision
from core.models import Project
from .models import SlackWorkspaceConnection, NotificationPreference
from .services import send_slack_message, create_default_preferences
from .blocks import (
    build_task_submitted,
    build_task_under_review,
    build_task_cancelled,
    build_task_approval,
    build_comment_added,
    build_decision_committed,
    build_decision_approved,
)

import logging

logger = logging.getLogger(__name__)


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _get_connection(project):
    """Returns the active Slack connection for a project's organisation, or None."""
    if not project or not project.organization:
        return None

    # SlackWorkspaceConnection lives in the public schema.  The public schema
    # is always present as a fallback in the search_path (both in production
    # where TenantSchemaMiddleware sets "tenant, public" and in tests where the
    # default is "public"), so no search_path manipulation is needed.
    return SlackWorkspaceConnection.objects.filter(
        organization=project.organization,
        is_active=True
    ).first()


def _check_preference(connection, project, event_type, task_status=None):
    """
    Returns the best-matching NotificationPreference for the given event, or None.
    Specific-status match takes priority over a general (no-status) preference.
    """
    qs = NotificationPreference.objects.filter(
        connection=connection,
        project=project,
        event_type=event_type,
        is_active=True,
    )
    preferences = list(qs)
    # Prefer exact status match first
    for p in preferences:
        if task_status and p.task_status == task_status:
            return p
    # Fall back to generic preference
    for p in preferences:
        if not p.task_status:
            return p
    return None


def _resolve_channel(preference, connection):
    """Returns the appropriate channel id, or None if not configured."""
    return preference.slack_channel_id or connection.default_channel_id


# ─── Project ──────────────────────────────────────────────────────────────────

@receiver(post_save, sender=Project)
def notify_on_project_creation(sender, instance, created, **kwargs):
    """Ensure default notification preferences exist for each new project."""
    if not created or not instance.organization:
        return

    # SlackWorkspaceConnection is in public schema
    # Use raw SQL to query it to avoid schema context issues
    try:
        from django.db import connection as db_connection

        with db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT id FROM slack_integration_slackworkspaceconnection
                WHERE organization_id = %s AND is_active = TRUE
                LIMIT 1
            """, [instance.organization.id])
            row = cursor.fetchone()

            if row:
                # Connection exists, get the object and create preferences
                connection = SlackWorkspaceConnection.objects.using('default').get(id=row[0])
                create_default_preferences(instance.organization, connection)
    except Exception:
        # Skip if slack integration table doesn't exist or any other error
        # This is expected for new organizations or when slack integration is not set up
        logger.error(
            "Failed to create default Slack preferences for project %s (organization %s)",
            instance.pk, getattr(instance, "organization_id", None), exc_info=True,
        )


# ─── Task: created ────────────────────────────────────────────────────────────

@receiver(post_save, sender=Task)
def notify_on_task_creation(sender, instance, created, **kwargs):
    # Only fire when the task is first created AND already submitted
    if not created:
        return
    if instance.status != Task.Status.SUBMITTED:
        return  # Only notify for tasks born directly in SUBMITTED state
    connection = _get_connection(instance.project)
    if not connection:
        return
    preference = _check_preference(connection, instance.project, NotificationPreference.EventType.TASK_CREATED)
    if not preference:
        return
    channel_id = _resolve_channel(preference, connection)
    if not channel_id:
        return

    fallback, blocks = build_task_submitted(instance)
    send_slack_message(connection, channel_id, fallback, blocks=blocks)


# ─── Task: status change ──────────────────────────────────────────────────────

@receiver(pre_save, sender=Task)
def capture_task_old_status(sender, instance, **kwargs):
    """Snapshot the current status before the save so we can detect changes."""
    if instance.pk:
        try:
            instance._old_status = Task.objects.get(pk=instance.pk).status
        except Task.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Task)
def notify_on_task_status_change(sender, instance, created, **kwargs):
    """
    Route each meaningful status transition to a specific message builder.
    Transitions not listed here are intentionally silent.
    """
    if created:
        return
    old_status = getattr(instance, '_old_status', None)
    if not old_status or instance.status == old_status:
        return

    new_status = instance.status

    # ── Determine which scenario this is ──────────────────────────────────────
    if old_status == Task.Status.DRAFT and new_status == Task.Status.SUBMITTED:
        builder = lambda: build_task_submitted(instance)   # noqa: E731
        event_type = NotificationPreference.EventType.TASK_CREATED  # reuse TASK_CREATED pref

    elif new_status == Task.Status.UNDER_REVIEW:
        # Covers: SUBMITTED→UNDER_REVIEW (start review) and APPROVED→UNDER_REVIEW (forward)
        builder = lambda: build_task_under_review(instance, old_status)   # noqa: E731
        event_type = NotificationPreference.EventType.TASK_STATUS_CHANGE

    elif new_status == Task.Status.CANCELLED:
        builder = lambda: build_task_cancelled(instance, old_status)   # noqa: E731
        event_type = NotificationPreference.EventType.TASK_STATUS_CHANGE

    else:
        # All other transitions (APPROVED, REJECTED, LOCKED, etc.) are handled
        # by dedicated signals or are intentionally suppressed.
        return

    # ── Resolve connection + preference ───────────────────────────────────────
    connection = _get_connection(instance.project)
    if not connection:
        return
    preference = _check_preference(
        connection, instance.project, event_type,
        task_status=instance.status,
    )
    if not preference:
        return
    channel_id = _resolve_channel(preference, connection)
    if not channel_id:
        return

    fallback, blocks = builder()
    send_slack_message(connection, channel_id, fallback, blocks=blocks)


# ─── Task: approval result ────────────────────────────────────────────────────

@receiver(post_save, sender=ApprovalRecord)
def notify_on_task_approval_result(sender, instance, created, **kwargs):
    """Notify Slack when a task approval record is created."""
    if not created:
        return
    task = instance.task
    connection = _get_connection(task.project)
    if not connection:
        return
    preference = _check_preference(connection, task.project, NotificationPreference.EventType.TASK_STATUS_CHANGE)
    if not preference:
        return
    channel_id = _resolve_channel(preference, connection)
    if not channel_id:
        return

    fallback, blocks = build_task_approval(task, instance)
    send_slack_message(connection, channel_id, fallback, blocks=blocks)


# ─── Task: comment ────────────────────────────────────────────────────────────

@receiver(post_save, sender=TaskComment)
def notify_on_comment(sender, instance, created, **kwargs):
    if not created:
        return
    task = instance.task
    # Don't notify for comments on DRAFT tasks
    if task.status == Task.Status.DRAFT:
        return
    connection = _get_connection(task.project)
    if not connection:
        return
    preference = _check_preference(connection, task.project, NotificationPreference.EventType.COMMENT_UPDATED)
    if not preference:
        return
    channel_id = _resolve_channel(preference, connection)
    if not channel_id:
        return

    fallback, blocks = build_comment_added(task, instance)
    send_slack_message(connection, channel_id, fallback, blocks=blocks)


# ─── Decision: commit ─────────────────────────────────────────────────────────

@receiver(pre_save, sender=Decision)
def capture_decision_old_status(sender, instance, **kwargs):
    """Snapshot the current Decision status before save."""
    if instance.pk:
        try:
            instance._old_status = Decision.objects.get(pk=instance.pk).status
        except Decision.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None


@receiver(post_save, sender=Decision)
def notify_on_decision_commit(sender, instance, created, **kwargs):
    """
    Notify when a Decision leaves DRAFT state, or is approved.
    """
    if created:
        return

    old_status = getattr(instance, '_old_status', None)
    new_status = instance.status

    is_newly_committed_or_awaiting = (
        old_status == Decision.Status.DRAFT and
        new_status in (Decision.Status.COMMITTED, Decision.Status.AWAITING_APPROVAL)
    )
    is_approved = (
        old_status == Decision.Status.AWAITING_APPROVAL and
        new_status == Decision.Status.COMMITTED
    )

    if not (is_newly_committed_or_awaiting or is_approved):
        return

    # Resolve connection + preference (project-scoped if available, else org-wide)
    connection = None
    preference = None
    if getattr(instance, 'project', None):
        connection = _get_connection(instance.project)
        if connection:
            preference = _check_preference(
                connection, instance.project,
                NotificationPreference.EventType.DECISION_CREATED,
            )
    else:
        author = instance.author
        if author and getattr(author, 'organization', None):
            # public is always a fallback in the search_path; no manipulation needed.
            connection = SlackWorkspaceConnection.objects.filter(
                organization=author.organization,
                is_active=True,
            ).first()

            if connection:
                preference = NotificationPreference.objects.filter(
                    connection=connection,
                    event_type=NotificationPreference.EventType.DECISION_CREATED,
                    is_active=True,
                ).first()

    if not connection or not preference:
        return
    channel_id = _resolve_channel(preference, connection)
    if not channel_id:
        return

    if is_approved:
        # Fetch the latest approval transition to get the note
        transition = instance.state_transitions.filter(
            to_status=Decision.Status.COMMITTED,
            transition_method="approve"
        ).order_by("-timestamp").first()
        note = transition.note if transition else None
        fallback, blocks = build_decision_approved(instance, instance.approved_by, note)
    else:
        fallback, blocks = build_decision_committed(instance)
        
    send_slack_message(connection, channel_id, fallback, blocks=blocks)
