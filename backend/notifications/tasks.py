"""
Periodic Celery tasks that fire system-triggered notifications.

Beat schedule (registered in backend/backend/celery.py):
  fire-calendar-reminders                  every 1 min
  fire-task-overdue-notifications          every 1 h
  fire-decision-deadline-notifications     every 12 h
  fire-meeting-starting-soon-notifications every 5 min
"""

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────
# How far ahead to look for meetings that are "starting soon" (minutes).
_MEETING_SOON_LOOKAHEAD_MINUTES = 10
# Suppress a second MEETING_STARTING_SOON for the same meeting within this window.
_MEETING_SOON_DEDUP_MINUTES = 20
# Suppress repeated TASK_OVERDUE notifications within this window.
_TASK_OVERDUE_DEDUP_HOURS = 24
# Hours a decision must be stuck in AWAITING_APPROVAL before we notify.
_DECISION_STALE_HOURS = 24
# Suppress repeated DECISION_DEADLINE notifications within this window.
_DECISION_DEDUP_HOURS = 24
# How far back in the past we still pick up calendar reminders (guards against
# duplicate delivery after a worker restart).
_CALENDAR_REMINDER_LOOKBACK_MINUTES = 5


# ── Calendar reminders ────────────────────────────────────────────────────────

@shared_task(name="notifications.tasks.fire_calendar_reminders")
def fire_calendar_reminders() -> int:
    """
    Send in-app notifications for EventReminder records whose scheduled_time
    has arrived and have not yet been dispatched.
    """
    from calendars.models import EventReminder
    from notifications.models import NotificationCategory, NotificationEventType
    from notifications.services import create_notification

    now = timezone.now()
    lookback = now - timedelta(minutes=_CALENDAR_REMINDER_LOOKBACK_MINUTES)

    pending = EventReminder.objects.filter(
        is_sent=False,
        method__in=["notification", "popup"],
        scheduled_time__lte=now,
        scheduled_time__gte=lookback,
    ).select_related("event", "user")

    sent_count = 0
    for reminder in pending:
        recipient_id = reminder.user_id or (
            reminder.event.created_by_id if reminder.event_id else None
        )
        if not recipient_id:
            logger.warning(
                "fire_calendar_reminders: reminder %s has no recipient — skipped",
                reminder.id,
            )
            continue

        event = reminder.event
        start_str = (
            event.start_datetime.strftime("%b %d, %Y %H:%M")
            if event.start_datetime
            else ""
        )

        try:
            create_notification(
                recipient_id=recipient_id,
                actor_id=None,
                category=NotificationCategory.COLLABORATION,
                event_type=NotificationEventType.CALENDAR_REMINDER,
                title=f"Reminder: {event.title}",
                body=f"Your event starts at {start_str}.",
                related_object_type="calendar_event",
                related_object_id=str(event.id),
                action_url="",
                metadata={
                    "change_type": "calendar_event",
                    "event_title": event.title,
                    "start_time": (
                        event.start_datetime.isoformat()
                        if event.start_datetime
                        else None
                    ),
                },
            )
            sent_count += 1
        except Exception:
            logger.exception(
                "fire_calendar_reminders: error processing reminder %s", reminder.id
            )
        finally:
            # Always mark sent so we don't retry on the next tick.
            EventReminder.objects.filter(pk=reminder.pk).update(
                is_sent=True, sent_at=now
            )

    logger.info("fire_calendar_reminders: processed %d reminders", sent_count)
    return sent_count


# ── Task overdue ──────────────────────────────────────────────────────────────

@shared_task(name="notifications.tasks.fire_task_overdue_notifications")
def fire_task_overdue_notifications() -> int:
    """
    Notify task owners when a task is past its due_date and still active.
    Rate-limited to once per _TASK_OVERDUE_DEDUP_HOURS per task–owner pair.
    """
    from task.models import Task
    from notifications.models import (
        NotificationCategory,
        NotificationEventType,
        Notification,
    )
    from notifications.services import create_notification
    from notifications.action_urls import task_action_url

    today = timezone.now().date()
    dedup_cutoff = timezone.now() - timedelta(hours=_TASK_OVERDUE_DEDUP_HOURS)

    # APPROVED / LOCKED / CANCELLED are terminal — no overdue alert needed.
    terminal_statuses = {"APPROVED", "LOCKED", "CANCELLED"}

    overdue_tasks = (
        Task.objects.filter(due_date__lt=today, owner__isnull=False)
        .exclude(status__in=terminal_statuses)
        .select_related("owner")
    )

    # Pre-build set of (related_object_id, recipient_id) pairs notified recently.
    recent_pairs: set[tuple[str, int]] = set(
        Notification.objects.filter(
            event_type=NotificationEventType.TASK_OVERDUE,
            created_at__gte=dedup_cutoff,
        ).values_list("related_object_id", "recipient_id")
    )

    sent_count = 0
    for task in overdue_tasks:
        if (str(task.id), task.owner_id) in recent_pairs:
            continue

        try:
            create_notification(
                recipient_id=task.owner_id,
                actor_id=None,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OVERDUE,
                title=f"Task overdue: {task.summary}",
                body=f"This task was due on {task.due_date}.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=task_action_url(task.slug),
                metadata={
                    "task_id": task.id,
                    "project_id": task.project_id,
                    "change_type": "task_overdue",
                    "old_value": str(task.due_date),
                    "new_value": "Overdue",
                },
            )
            sent_count += 1
        except Exception:
            logger.exception(
                "fire_task_overdue_notifications: error for task %s", task.id
            )

    logger.info("fire_task_overdue_notifications: sent %d notifications", sent_count)
    return sent_count


# ── Decision deadline ─────────────────────────────────────────────────────────

@shared_task(name="notifications.tasks.fire_decision_deadline_notifications")
def fire_decision_deadline_notifications() -> int:
    """
    Remind decision authors when their decision has been stuck in
    AWAITING_APPROVAL for more than _DECISION_STALE_HOURS.
    Fires at most once per _DECISION_DEDUP_HOURS per decision.
    """
    from decision.models import Decision
    from notifications.models import (
        NotificationCategory,
        NotificationEventType,
        Notification,
    )
    from notifications.services import create_notification
    from notifications.action_urls import decision_action_url
    dedup_cutoff = timezone.now() - timedelta(hours=_DECISION_DEDUP_HOURS)

    stale_decisions = Decision.objects.filter(
        status=Decision.Status.AWAITING_APPROVAL,
        updated_at__lte=stale_cutoff,
        author__isnull=False,
    ).select_related("author", "project")

    recently_notified_ids: set[str] = set(
        Notification.objects.filter(
            event_type=NotificationEventType.DECISION_DEADLINE,
            created_at__gte=dedup_cutoff,
        ).values_list("related_object_id", flat=True)
    )

    sent_count = 0
    for decision in stale_decisions:
        if str(decision.id) in recently_notified_ids:
            continue

        stale_since = (
            decision.updated_at.strftime("%b %d, %Y")
            if decision.updated_at
            else "recently"
        )
        label = decision.title or f"Decision #{decision.project_seq}"
        action_url = (
            decision_action_url(decision.slug, decision.project_id)
            if decision.project_id
            else ""
        )

        try:
            create_notification(
                recipient_id=decision.author_id,
                actor_id=None,
                category=NotificationCategory.DECISIONS,
                event_type=NotificationEventType.DECISION_DEADLINE,
                title=f"Decision awaiting approval: {label}",
                body=f"This decision has been awaiting approval since {stale_since}.",
                related_object_type="decision",
                related_object_id=str(decision.id),
                action_url=action_url,
                metadata={
                    "change_type": "decision_deadline",
                    "new_value": f"Awaiting approval since {stale_since}",
                    "decision_title": label,
                },
            )
            sent_count += 1
        except Exception:
            logger.exception(
                "fire_decision_deadline_notifications: error for decision %s",
                decision.id,
            )

    logger.info(
        "fire_decision_deadline_notifications: sent %d notifications", sent_count
    )
    return sent_count


# ── Meeting starting soon ─────────────────────────────────────────────────────

@shared_task(name="notifications.tasks.fire_meeting_starting_soon_notifications")
def fire_meeting_starting_soon_notifications() -> int:
    """
    Notify all meeting participants when their meeting starts within
    _MEETING_SOON_LOOKAHEAD_MINUTES minutes.
    Deduplicates within a _MEETING_SOON_DEDUP_MINUTES window per meeting.
    """
    from meetings.models import Meeting
    from notifications.models import (
        NotificationCategory,
        NotificationEventType,
        Notification,
    )
    from notifications.services import create_notification
    from notifications.action_urls import meeting_action_url

    now = timezone.now()
    lookahead = now + timedelta(minutes=_MEETING_SOON_LOOKAHEAD_MINUTES)
    dedup_cutoff = now - timedelta(minutes=_MEETING_SOON_DEDUP_MINUTES)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    candidates = Meeting.objects.filter(
        scheduled_date__in=[today, tomorrow],
        scheduled_time__isnull=False,
        is_archived=False,
    ).prefetch_related("participant_links")

    # Narrow down to meetings whose combined datetime is inside the window.
    target_meetings: list[tuple[Meeting, datetime]] = []
    for meeting in candidates:
        if not meeting.scheduled_date or not meeting.scheduled_time:
            continue
        combined = datetime.combine(meeting.scheduled_date, meeting.scheduled_time)
        scheduled_dt = timezone.make_aware(combined, timezone.utc)
        if now <= scheduled_dt <= lookahead:
            target_meetings.append((meeting, scheduled_dt))

    recently_notified_meeting_ids: set[str] = set(
        Notification.objects.filter(
            event_type=NotificationEventType.MEETING_STARTING_SOON,
            created_at__gte=dedup_cutoff,
        ).values_list("related_object_id", flat=True)
    )

    sent_count = 0
    for meeting, scheduled_dt in target_meetings:
        if str(meeting.id) in recently_notified_meeting_ids:
            continue

        participant_ids = list(
            meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
        )
        minutes_away = max(1, int((scheduled_dt - now).total_seconds() / 60))
        start_label = f"{minutes_away} minute{'s' if minutes_away != 1 else ''} from now"
        start_str = scheduled_dt.strftime("%H:%M")

        for uid in participant_ids:
            try:
                create_notification(
                    recipient_id=uid,
                    actor_id=None,
                    category=NotificationCategory.MEETINGS,
                    event_type=NotificationEventType.MEETING_STARTING_SOON,
                    title=f"Meeting starting soon: {meeting.title}",
                    body=f"Your meeting starts in {start_label}.",
                    related_object_type="meeting",
                    related_object_id=str(meeting.id),
                    action_url=meeting_action_url(meeting.slug, meeting.project_id),
                    metadata={
                        "change_type": "meeting_start",
                        "start_time": start_label,
                        "start_time_exact": start_str,
                        "meeting_title": meeting.title,
                    },
                )
                sent_count += 1
            except Exception:
                logger.exception(
                    "fire_meeting_starting_soon_notifications: error meeting=%s uid=%s",
                    meeting.id,
                    uid,
                )

    logger.info(
        "fire_meeting_starting_soon_notifications: sent %d notifications", sent_count
    )
    return sent_count


# ── Message reminders ─────────────────────────────────────────────────────────

_MESSAGE_REMINDER_LOOKBACK_MINUTES = 5

@shared_task(name="notifications.tasks.fire_message_reminders")
def fire_message_reminders() -> int:
    """
    Send in-app notifications for MessageReminder records whose remind_at time
    has arrived and have not yet been dispatched.
    """
    from chat.models import MessageReminder
    from chat.url_helpers import build_messages_action_url
    from notifications.models import NotificationCategory, NotificationEventType
    from notifications.services import create_notification

    now = timezone.now()
    lookback = now - timedelta(minutes=_MESSAGE_REMINDER_LOOKBACK_MINUTES)

    pending = MessageReminder.objects.filter(
        is_sent=False,
        remind_at__lte=now,
        remind_at__gte=lookback,
    ).select_related(
        'message__sender',
        'message__chat',
        'user'
    )

    sent_count = 0
    for reminder in pending:
        try:
            message = reminder.message
            sender = message.sender
            chat = message.chat

            # Build action URL to navigate to the message
            action_url = build_messages_action_url(chat.slug, message_id=message.id)

            # Truncate message content for notification body
            content_preview = message.content[:200] if message.content else "[Attachment]"

            # Build notification title
            sender_name = sender.username or sender.email
            title = f"Reminder: Message from {sender_name}"

            create_notification(
                recipient_id=reminder.user_id,
                actor_id=sender.id,
                category=NotificationCategory.COLLABORATION,
                event_type=NotificationEventType.MESSAGE_REMINDER,
                title=title,
                body=content_preview,
                related_object_type="message",
                related_object_id=str(message.id),
                action_url=action_url,
                metadata={
                    "message_id": message.id,
                    "chat_id": chat.id,
                    "project_id": chat.project_id,
                    "sender_name": sender_name,
                    "reminder_note": reminder.note,
                },
            )
            sent_count += 1
        except Exception:
            logger.exception(
                "fire_message_reminders: error processing reminder=%s",
                reminder.id,
            )
        finally:
            # Mark as sent regardless of success (idempotency)
            MessageReminder.objects.filter(pk=reminder.pk).update(
                is_sent=True,
                sent_at=now,
            )

    logger.info("fire_message_reminders: sent %d reminders", sent_count)
    return sent_count
