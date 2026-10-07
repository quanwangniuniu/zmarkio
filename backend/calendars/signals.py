import datetime as dt

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone

from decision.models import Decision
from task.models import Task
from .models import CalendarEvent


def _calendar_event_title(prefix="", title="", suffix=""):
    max_length = CalendarEvent._meta.get_field('title').max_length
    prefix = prefix or ""
    title = title or ""
    suffix = suffix or ""
    full_title = f"{prefix}{title}{suffix}"
    if len(full_title) <= max_length:
        return full_title

    available_title_length = max_length - len(prefix) - len(suffix)
    if available_title_length <= 0:
        return full_title[:max_length]

    if available_title_length > 3:
        title = f"{title[:available_title_length - 3]}..."
    else:
        title = title[:available_title_length]
    return f"{prefix}{title}{suffix}"


@receiver(post_save, sender=Decision)
def generate_calendar_events_for_decision(sender, instance, created, **kwargs):
    """
    Auto-generate CalendarEvents when a Decision is created or updated.
    - Decision Event: created when Decision has committed_at
    - Decision Review Event: created when Decision has approved_at

    IMPORTANT: Skip soft-deleted Decisions. Without this guard, the post_save
    signal fired by decision.save(update_fields=["is_deleted", ...]) during the
    Decision soft-delete path would recreate the CalendarEvent we just deleted,
    causing deleted Decisions to remain visible on the calendar.
    """
    # Guard: do not generate or update calendar events for soft-deleted decisions
    if instance.is_deleted:
        return

    organization = getattr(instance, 'organization', None) or (
        instance.project.organization if instance.project else None
    )
    if not organization:
        return

    # Decision Event — Use planned_decision_date, committed_at, or created_at as the time
    event_time = instance.planned_decision_date or instance.committed_at or instance.created_at
    if event_time:
        project_prefix = f"[{instance.project.name}] " if instance.project else ""

        if instance.title and instance.title.strip():
            title = instance.title.strip()
        elif instance.context_summary and instance.context_summary.strip():
            title = instance.context_summary.strip()[:50]
            if len(instance.context_summary.strip()) > 50:
                title += "..."
        else:
            title = f"Decision #{instance.id}"

        CalendarEvent.objects.update_or_create(
            event_type=CalendarEvent.EventType.DECISION,
            decision=instance,
            defaults={
                'organization': organization,
                'title': _calendar_event_title(project_prefix, title),
                'start_time': event_time,
            }
        )

    # Decision Review Event — uses approved_at as time
    if instance.approved_at:
        project_prefix = f"[{instance.project.name}] " if instance.project else ""

        if instance.title and instance.title.strip():
            title = instance.title.strip()
        elif instance.context_summary and instance.context_summary.strip():
            title = instance.context_summary.strip()[:50]
            if len(instance.context_summary.strip()) > 50:
                title += "..."
        else:
            title = f"Decision #{instance.id}"

        CalendarEvent.objects.update_or_create(
            event_type=CalendarEvent.EventType.DECISION_REVIEW,
            decision=instance,
            defaults={
                'organization': organization,
                'title': _calendar_event_title(f"{project_prefix}Review: ", title),
                'start_time': instance.approved_at,
            }
        )


@receiver(post_save, sender=Task)
def generate_calendar_events_for_task(sender, instance, created, **kwargs):
    """
    Auto-generate CalendarEvents when a Task is created or updated.
    - Task Event: created when Task has start_date or due_date.

    Bug 1 fix: use start_date as event start_time and due_date as end_time,
    so tasks with both dates render as a continuous multi-day event on the calendar.

    Note: start_date is the existing Task field used by the frontend for task
    start date input. planned_start_date was considered but is not wired to the
    frontend, so start_date is the correct field to use here.
    """
    try:
        organization = getattr(instance.project, 'organization', None)
    except Exception:
        return
    if not organization:
        return

    # Task Event — generated if there is start_date or due_date
    if instance.start_date or instance.due_date:
        def _to_utc(d, t):
            return dt.datetime.combine(d, t, tzinfo=dt.timezone.utc)

        if instance.start_date and instance.due_date:
            # Task has both start and due date — create a continuous duration event
            start_time = _to_utc(instance.start_date, dt.time.min)
            end_time = _to_utc(instance.due_date, dt.time.max)
        elif instance.start_date:
            # Only start date — single day event
            start_time = _to_utc(instance.start_date, dt.time.min)
            end_time = _to_utc(instance.start_date, dt.time.max)
        else:
            # Only due date — single day event (backward compatibility)
            start_time = _to_utc(instance.due_date, dt.time.min)
            end_time = _to_utc(instance.due_date, dt.time.max)

        project_prefix = f"[{instance.project.name}] " if instance.project else ""
        status_suffix = f" ({instance.get_status_display()})" if instance.status else ""
        title = instance.summary or f"Task #{instance.id}"

        qs = CalendarEvent.objects.filter(
            event_type=CalendarEvent.EventType.TASK,
            task=instance,
        )
        count = qs.count()
        if count > 1:
            # Deduplicate: keep the first row, delete the rest.
            keep_id = qs.order_by('id').values_list('id', flat=True).first()
            qs.exclude(id=keep_id).delete()
            qs = CalendarEvent.objects.filter(id=keep_id)

        new_defaults = {
            'organization': organization,
            'title': _calendar_event_title(project_prefix, title, status_suffix),
            'start_time': start_time,
            'end_time': end_time,
        }
        if count == 0:
            CalendarEvent.objects.create(
                event_type=CalendarEvent.EventType.TASK,
                task=instance,
                **new_defaults,
            )
        else:
            qs.update(**new_defaults)


@receiver(post_delete, sender=Decision)
def delete_calendar_events_for_decision(sender, instance, **kwargs):
    """
    Auto-delete CalendarEvents when a Decision is hard-deleted.
    Note: soft-delete is handled by the Decision API before this hard-delete signal.
    """
    CalendarEvent.objects.filter(decision=instance).delete()


@receiver(post_delete, sender=Task)
def delete_calendar_events_for_task(sender, instance, **kwargs):
    """
    Auto-delete CalendarEvents when a Task is deleted.
    """
    CalendarEvent.objects.filter(task=instance).delete()
