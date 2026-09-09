"""
CalendarEvent query service.
Abstracts raw database queries from the API layer.

Also hosts the recurring-event scope logic (this / this-and-future / all):
keeping this business logic here keeps the views thin (fat core, thin edges).
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from typing import Any

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

# availability holds only pure functions and imports nothing from this app,
# so a module-level import here cannot create a cycle.
from .availability import BookingRules, WeeklyWindow
from .models import (
    CalendarEvent,
    CalendarSettings,
    Event,
    EventAttendee,
    EventReminder,
    RecurrenceException,
    RecurrenceRule,
)


def get_calendar_events(
    organization,
    start: str | None = None,
    end: str | None = None,
    event_type: str | None = None,
    project_id: str | None = None,
) -> QuerySet:
    """
    Query CalendarEvents for a given organization with optional filters.
    Returns a sorted, UI-ready queryset.

    Args:
        organization: The organization to scope the query to
        start: ISO datetime string for start of range (inclusive)
        end: ISO datetime string for end of range (exclusive)
        event_type: Filter by event type (decision / task / decision_review)
        project_id: Filter by project ID

    Returns:
        QuerySet of CalendarEvent ordered by start_time
    """
    queryset = CalendarEvent.objects.filter(
        organization=organization,
    ).select_related('decision', 'task', 'review')

    if start:
        queryset = queryset.filter(start_time__gte=start)

    if end:
        queryset = queryset.filter(start_time__lt=end)

    if event_type:
        queryset = queryset.filter(event_type=event_type)

    if project_id:
        queryset = queryset.filter(
            Q(decision__project_id=project_id) |
            Q(task__project_id=project_id)
        )

    return queryset.order_by('start_time')


# ---------------------------------------------------------------------------
# Recurring-event scope logic (this / this-and-future / all)
# ---------------------------------------------------------------------------
#
# Editable Event fields a scope edit may carry. We deliberately exclude
# recurrence/identity fields so a scope edit can never mutate the pattern or
# tenant of an event by accident.
_EDITABLE_EVENT_FIELDS = frozenset(
    {
        "calendar_id",
        "title",
        "description",
        "start_datetime",
        "end_datetime",
        "timezone",
        "is_all_day",
        "location",
        "location_lat",
        "location_lng",
        "status",
        "event_type",
        "color",
        "visibility",
        "has_conference",
        "conference_data",
        "guests_can_modify",
        "guests_can_invite_others",
        "guests_can_see_other_guests",
        "attachments",
        "metadata",
    }
)


def _sanitized_scope_payload(data: dict | None) -> dict:
    """
    Strip recurrence/identity keys from an incoming scope-edit payload so the
    EventCreateUpdateSerializer never tries to (a) toggle is_recurring without a
    matching pattern (its validate() would reject that) or (b) rewrite the
    recurrence rule. Scope edits change occurrence data, not the pattern.
    """
    if not data:
        return {}
    return {
        key: value
        for key, value in data.items()
        if key in _EDITABLE_EVENT_FIELDS
    }


def _rule_step(rule: RecurrenceRule) -> timedelta | None:
    """
    Step between occurrences for the patterns expansion supports (DAILY/WEEKLY).
    Returns None for patterns expansion does not currently materialize.
    """
    interval = max(int(rule.interval or 1), 1)
    if rule.frequency == "DAILY":
        return timedelta(days=interval)
    if rule.frequency == "WEEKLY":
        return timedelta(weeks=interval)
    return None


def _count_occurrences_before(
    series_start: datetime, boundary: datetime, rule: RecurrenceRule
) -> int:
    """
    Count occurrences with start in [series_start, boundary). Used to recompute
    COUNT when splitting a count-bounded series. Strict-less upper bound mirrors
    the expansion off-by-one rule so the boundary occurrence belongs to the new
    series, not the capped master.
    """
    step = _rule_step(rule)
    if step is None:
        return 0
    count = 0
    current = series_start
    while current < boundary:
        count += 1
        current = current + step
    return count


def _occurrence_on_series(
    series_start: datetime, occurrence_start: datetime, rule: RecurrenceRule
) -> bool:
    """True when `occurrence_start` is a valid occurrence start for the series."""
    if occurrence_start < series_start:
        return False
    step = _rule_step(rule)
    if step is None:
        return False
    if rule.until is not None and occurrence_start >= rule.until:
        return False

    delta = occurrence_start - series_start
    step_seconds = step.total_seconds()
    if step_seconds <= 0 or delta.total_seconds() % step_seconds != 0:
        return False

    if rule.count is not None:
        index = int(delta.total_seconds() // step_seconds)
        if index >= rule.count:
            return False
    return True


def _get_or_create_modified_event(
    event: Event, original_start: datetime
) -> tuple[Event, RecurrenceException | None]:
    """
    Return the per-occurrence override Event for `original_start`, creating a
    one-off clone (and its RecurrenceException) if none exists yet. The clone is
    never recurring and never carries a recurrence_rule, honoring the
    RecurrenceException recursion guard.
    """
    exc = (
        RecurrenceException.objects.filter(
            organization=event.organization,
            recurrence_rule=event.recurrence_rule,
            original_event=event,
            exception_date=original_start,
        )
        .select_related("modified_event")
        .first()
    )

    if exc and not exc.is_cancelled and exc.modified_event_id:
        return exc.modified_event, exc

    cloned = Event.objects.get(pk=event.pk)
    cloned.pk = None
    cloned.id = uuid.uuid4()
    cloned.is_recurring = False
    cloned.recurrence_rule = None
    cloned.original_start = original_start
    duration = event.end_datetime - event.start_datetime
    cloned.start_datetime = original_start
    cloned.end_datetime = original_start + duration
    cloned.ical_uid = None
    cloned.is_deleted = False
    cloned.save()

    if exc:
        exc.is_cancelled = False
        exc.modified_event = cloned
        exc.exception_date = original_start
        exc.organization = event.organization
        exc.recurrence_rule = event.recurrence_rule
        exc.original_event = event
        exc.save()
    else:
        exc = RecurrenceException.objects.create(
            organization=event.organization,
            recurrence_rule=event.recurrence_rule,
            original_event=event,
            exception_date=original_start,
            is_cancelled=False,
            modified_event=cloned,
        )

    return cloned, exc


def modify_single_occurrence(
    event: Event, original_start: datetime, data: dict, context: dict
) -> Event:
    """
    Scope = "this only": apply the edit to a single occurrence via a
    RecurrenceException override, leaving the master series untouched.
    """
    from .serializers import EventCreateUpdateSerializer

    if not _occurrence_on_series(
        event.start_datetime, original_start, event.recurrence_rule
    ):
        raise ValueError(
            "The selected occurrence is not part of this event series."
        )

    with transaction.atomic():
        modified_event, _exc = _get_or_create_modified_event(event, original_start)

        serializer = EventCreateUpdateSerializer(
            modified_event,
            data=_sanitized_scope_payload(data),
            partial=True,
            context=context,
        )
        serializer.is_valid(raise_exception=True)
        return serializer.save()


def cancel_single_occurrence(event: Event, original_start: datetime) -> None:
    """
    Scope = "this only" delete: cancel a single occurrence, leaving the master
    series intact. Any existing per-occurrence override is soft-deleted.
    """
    if not _occurrence_on_series(
        event.start_datetime, original_start, event.recurrence_rule
    ):
        raise ValueError(
            "The selected occurrence is not part of this event series."
        )

    with transaction.atomic():
        exc = (
            RecurrenceException.objects.filter(
                organization=event.organization,
                recurrence_rule=event.recurrence_rule,
                original_event=event,
                exception_date=original_start,
            )
            .select_related("modified_event")
            .first()
        )

        if exc:
            if exc.modified_event_id:
                exc.modified_event.is_deleted = True
                exc.modified_event.save(update_fields=["is_deleted", "updated_at"])
            exc.modified_event = None
            exc.is_cancelled = True
            exc.save()
        else:
            RecurrenceException.objects.create(
                organization=event.organization,
                recurrence_rule=event.recurrence_rule,
                original_event=event,
                exception_date=original_start,
                is_cancelled=True,
                modified_event=None,
            )


def split_series_from_occurrence(
    event: Event, original_start: datetime, data: dict, context: dict
) -> Event:
    """
    Scope = "this and future": Google-style split. Cap the master series just
    before `original_start`, then create a NEW recurring Event + RecurrenceRule
    starting at `original_start` carrying the edited values.

    ATOMICITY: every write below — capping the master rule, creating the new
    rule/event, re-pointing future exceptions, and copying attendees/reminders —
    runs inside a SINGLE transaction. The master cap is never committed on its
    own, so a failure mid-split can never leave a "master capped but new series
    missing" (data-loss) state.
    """
    from .serializers import EventCreateUpdateSerializer

    master_rule = event.recurrence_rule

    if not _occurrence_on_series(event.start_datetime, original_start, master_rule):
        raise ValueError(
            "The selected occurrence is not part of this event series."
        )

    with transaction.atomic():
        # Snapshot original bounding before we mutate the master rule.
        original_count = master_rule.count
        original_until = master_rule.until
        occurrences_before = _count_occurrences_before(
            event.start_datetime, original_start, master_rule
        )

        # --- Cap the master rule (inside the transaction) ---------------------
        # The count/until CheckConstraint forbids setting both, so we mirror
        # whichever bound the master already used.
        if original_count is not None:
            master_rule.count = occurrences_before
            master_rule.until = None
        else:
            master_rule.until = original_start
            master_rule.count = None
        master_rule.save()

        # --- Build the new rule for the future series -------------------------
        if original_count is not None:
            new_count = max(original_count - occurrences_before, 0)
            new_until = None
        else:
            new_count = None
            new_until = original_until  # keep the original end (may be None)

        new_rule = RecurrenceRule.objects.create(
            organization=event.organization,
            frequency=master_rule.frequency,
            interval=master_rule.interval,
            by_day=list(master_rule.by_day or []),
            by_month_day=list(master_rule.by_month_day or []),
            by_set_pos=list(master_rule.by_set_pos or []),
            by_month=list(master_rule.by_month or []),
            count=new_count,
            until=new_until,
            exception_dates=list(master_rule.exception_dates or []),
        )

        # --- Create the new series master event -------------------------------
        new_event = Event.objects.get(pk=event.pk)
        new_event.pk = None
        new_event.id = uuid.uuid4()
        new_event.ical_uid = None
        new_event.is_recurring = True
        new_event.recurrence_rule = new_rule
        new_event.original_start = None
        new_event.is_deleted = False
        duration = event.end_datetime - event.start_datetime
        new_event.start_datetime = original_start
        new_event.end_datetime = original_start + duration
        new_event.metadata = {
            **(event.metadata or {}),
            "split_from": str(event.id),
            "split_at": original_start.isoformat(),
        }
        new_event.save()

        # Apply the edited values onto the new series master.
        payload = _sanitized_scope_payload(data)
        if payload:
            serializer = EventCreateUpdateSerializer(
                new_event,
                data=payload,
                partial=True,
                context=context,
            )
            serializer.is_valid(raise_exception=True)
            new_event = serializer.save()

        # --- Re-point future per-occurrence overrides to the new series -------
        # Exceptions strictly before the split stay on the (capped) master.
        RecurrenceException.objects.filter(
            organization=event.organization,
            recurrence_rule=master_rule,
            original_event=event,
            exception_date__gte=original_start,
        ).update(recurrence_rule=new_rule, original_event=new_event)

        # --- Copy attendees and reminders onto the new series -----------------
        for attendee in EventAttendee.objects.filter(event=event, is_deleted=False):
            attendee.pk = None
            attendee.id = uuid.uuid4()
            attendee.event = new_event
            attendee.save()

        for reminder in EventReminder.objects.filter(event=event, is_deleted=False):
            reminder.pk = None
            reminder.id = uuid.uuid4()
            reminder.event = new_event
            reminder.save()

        return new_event


def update_entire_series(event: Event, data: dict, context: dict) -> Event:
    """
    Scope = "all": edit the whole series by updating the master event. Thin
    wrapper around the create/update serializer so all three scopes share one
    home in services.py. The "all" HTTP path also reuses the existing
    PATCH /events/{id}/ endpoint, which delegates to the same serializer.
    """
    from .serializers import EventCreateUpdateSerializer

    with transaction.atomic():
        serializer = EventCreateUpdateSerializer(
            event,
            data=data,
            partial=True,
            context=context,
        )
        serializer.is_valid(raise_exception=True)
        return serializer.save()

# ── Occurrence expansion ─────────────────────────────────────────────────
# Moved here from views.py so non-view callers (booking links) can reuse the
# recurrence logic without importing the API layer. views.py re-exports both
# names, so existing imports keep working.


def _events_intersecting_range(start_dt, end_dt, base_qs=None):
    """
    Return events that may appear in [start_dt, end_dt).

    Non-recurring events use wall-clock overlap. Recurring masters are included
    when the series can still produce instances in the window (so split-born
    series remain visible after their first occurrence day).
    """
    if base_qs is None:
        base_qs = Event.objects.all()

    non_recurring = Q(
        is_recurring=False,
        start_datetime__lt=end_dt,
        end_datetime__gt=start_dt,
    )
    recurring = Q(
        is_recurring=True,
        recurrence_rule__isnull=False,
        start_datetime__lt=end_dt,
    ) & (
        Q(recurrence_rule__until__isnull=True)
        | Q(recurrence_rule__until__gt=start_dt)
    )
    return base_qs.filter(non_recurring | recurring)


def _expand_recurring_event(
    event: Event,
    time_min,
    time_max,
    max_results: int = 250,
):
    """
    Expand a recurring event into concrete instances within [time_min, time_max).
    Currently supports simple DAILY and WEEKLY patterns based on start_datetime.
    """
    if not event.is_recurring or not event.recurrence_rule_id:
        return []

    rule = event.recurrence_rule
    frequency = rule.frequency
    interval = max(int(rule.interval or 1), 1)

    duration = event.end_datetime - event.start_datetime
    instances: list[Any] = []

    # Load exceptions for this event/rule within range
    exceptions = RecurrenceException.objects.filter(
        organization=event.organization,
        recurrence_rule=rule,
        original_event=event,
        exception_date__gte=time_min,
        exception_date__lt=time_max,
    ).select_related("modified_event")
    exceptions_by_date = {exc.exception_date: exc for exc in exceptions}

    # Fast-forward to first occurrence that could intersect [time_min, time_max)
    if frequency == "DAILY":
        step = timezone.timedelta(days=interval)
    elif frequency == "WEEKLY":
        step = timezone.timedelta(weeks=interval)
    else:
        # For now only basic DAILY/WEEKLY patterns are supported in expansion.
        return []

    # Honor the series bounds so a capped/split series stops generating.
    # `until` is treated as exclusive (strict-less): an occurrence exactly at
    # `until` belongs to the next (split) series, never the capped master.
    rule_until = rule.until
    rule_count = rule.count

    # Skip occurrences that end at or before time_min (first that can intersect
    # the window has start > time_min - duration).
    occurrence_index = _count_occurrences_before(
        event.start_datetime, time_min - duration, rule
    )
    current = event.start_datetime + (step * occurrence_index)

    if rule_count is not None and occurrence_index >= rule_count:
        return []
    if rule_until is not None and current >= rule_until:
        return []

    while current + duration <= time_max and len(instances) < max_results:
        if rule_count is not None and occurrence_index >= rule_count:
            break
        if rule_until is not None and current >= rule_until:
            break

        # Check intersection with requested window
        if current < time_max and (current + duration) > time_min:
            exc = exceptions_by_date.get(current)
            if exc:
                if exc.is_cancelled:
                    # Skip cancelled instance
                    pass
                else:
                    # Use modified event instance
                    instances.append(exc.modified_event)
            else:
                # Create a lightweight instance based on the master event
                attrs = {}
                for field in Event._meta.fields:
                    name = field.name
                    attrs[name] = getattr(event, name)

                # Override fields specific to this occurrence
                attrs["id"] = event.id  # master id; original_start differentiates instances
                attrs["start_datetime"] = current
                attrs["end_datetime"] = current + duration
                attrs["original_start"] = current

                instance_obj = SimpleNamespace(**attrs)
                instances.append(instance_obj)

        current = current + step
        occurrence_index += 1

    return instances



# ── Busy intervals for availability ─────────────────────────────


def _recurring_busy_intervals(event, time_min, time_max):
    """Expand every supported recurrence rule without truncating boundary overlaps."""
    from zoneinfo import ZoneInfo
    from dateutil.rrule import rrulestr
    from django.utils.dateparse import parse_datetime

    rule = event.recurrence_rule
    duration = event.end_datetime - event.start_datetime
    try:
        starts = rrulestr(rule.generate_rrule_string(),
                         dtstart=event.start_datetime.astimezone(ZoneInfo(event.timezone)))
        excluded = {parse_datetime(value) for value in rule.exception_dates or []}
    except (ValueError, TypeError, KeyError):
        # Invalid imported recurrence data is unknown availability, never free.
        return [(time_min, time_max)]
    exceptions = {exc.exception_date: exc for exc in RecurrenceException.objects.filter(
        original_event=event, recurrence_rule=rule,
        exception_date__gt=time_min - duration, exception_date__lt=time_max,
    ).select_related("modified_event")}
    result = []
    for occurrence in starts.xafter(time_min - duration, inc=False):
        start = occurrence.astimezone(timezone.utc)
        if start >= time_max or (rule.until and start >= rule.until):
            break
        if start in excluded:
            continue
        exception = exceptions.get(start)
        if exception:
            replacement = exception.modified_event
            if exception.is_cancelled or not replacement or replacement.is_deleted or replacement.status == "cancelled":
                continue
            result.append((replacement.start_datetime, replacement.end_datetime))
        else:
            result.append((start, start + duration))
    return result


def get_busy_intervals_by_calendar(
    calendars, time_min: datetime, time_max: datetime
) -> dict[str, list[tuple[datetime, datetime]]]:
    """
    Busy intervals per calendar within [time_min, time_max).

    Recurring series are expanded to concrete instances, and each calendar's
    intervals are merged so overlapping events collapse into one block.
    Returns {calendar_id: [(start, end), ...]}.
    """
    result: dict[str, list[tuple[datetime, datetime]]] = {}

    for calendar in calendars:
        events = _events_intersecting_range(
            time_min,
            time_max,
            Event.objects.filter(calendar=calendar, is_deleted=False).exclude(status="cancelled").select_related(
                "recurrence_rule"
            ),
        )

        intervals: list[tuple[datetime, datetime]] = []
        for event in events:
            if event.is_recurring and event.recurrence_rule_id:
                intervals.extend(_recurring_busy_intervals(event, time_min, time_max))
            else:
                intervals.append((event.start_datetime, event.end_datetime))

        result[str(calendar.id)] = merge_busy_intervals(intervals)

    return result


def get_busy_intervals(
    calendars, time_min: datetime, time_max: datetime
) -> list[tuple[datetime, datetime]]:
    """
    Busy intervals across all given calendars, flattened and merged.

    This is the form booking availability needs: a single "when is this person
    unavailable" timeline, rather than a per-calendar breakdown.
    """
    combined: list[tuple[datetime, datetime]] = []
    for intervals in get_busy_intervals_by_calendar(calendars, time_min, time_max).values():
        combined.extend(intervals)
    return merge_busy_intervals(combined)


def merge_busy_intervals(
    intervals: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """Sort and coalesce overlapping or touching intervals."""
    cleaned = [(start, end) for start, end in intervals if end > start]
    if not cleaned:
        return []
    cleaned.sort(key=lambda pair: pair[0])

    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


# ── Booking link → availability inputs ──────────────────────────

# Fallback when neither the link nor the owner's settings say otherwise.
DEFAULT_WORKING_WINDOW = (time(9, 0), time(17, 0))
DEFAULT_WORKING_WEEKDAYS = (0, 1, 2, 3, 4)  # Mon–Fri, Python convention


def rules_from_booking_link(link) -> BookingRules:
    """Translate a BookingLink's stored rules into availability BookingRules."""
    return BookingRules(
        duration_minutes=link.duration_minutes,
        slot_increment_minutes=link.slot_increment_minutes,
        buffer_before_minutes=link.buffer_before_minutes,
        buffer_after_minutes=link.buffer_after_minutes,
        min_notice_minutes=link.min_notice_minutes,
        max_advance_days=link.max_advance_days,
    )


@dataclass(frozen=True)
class AvailabilitySchedule:
    """
    Weekly windows plus the timezone they are expressed in.

    These travel together on purpose. Windows are wall-clock times, so they are
    meaningless without the zone they were written in — and the two can come
    from different places. Returning them separately let the link's timezone be
    applied to windows inherited from CalendarSettings, which silently shifted
    a 09:00-17:00 day by the offset between the two zones.
    """

    windows: list[WeeklyWindow]
    timezone: str


def schedule_from_booking_link(link) -> AvailabilitySchedule:
    """
    The link's weekly availability, and the zone it is written in.

    Precedence, timezone always taken from the same source as the windows:
      1. the link's own `availability_windows`, in the link's timezone;
      2. the owner's CalendarSettings working hours, in the *settings* timezone;
      3. Mon-Fri 09:00-17:00, in the link's timezone.
    """
    explicit = link.availability_windows or []
    if explicit:
        return AvailabilitySchedule(
            windows=[
                WeeklyWindow(
                    weekday=int(window["weekday"]),
                    start=_parse_hhmm(window["start"]),
                    end=_parse_hhmm(window["end"]),
                )
                for window in explicit
            ],
            timezone=link.timezone,
        )

    settings_obj = CalendarSettings.objects.filter(user_id=link.owner_id).first()
    if settings_obj and settings_obj.working_hours_enabled:
        start = settings_obj.working_hours_start or DEFAULT_WORKING_WINDOW[0]
        end = settings_obj.working_hours_end or DEFAULT_WORKING_WINDOW[1]
        weekdays = _weekdays_from_settings(settings_obj)
        if start < end and weekdays:
            return AvailabilitySchedule(
                windows=[
                    WeeklyWindow(weekday=day, start=start, end=end) for day in weekdays
                ],
                # The hours are wall-clock in the owner's settings zone, not the
                # link's — using the link's zone here is what caused the shift.
                timezone=settings_obj.timezone or link.timezone,
            )

    start, end = DEFAULT_WORKING_WINDOW
    return AvailabilitySchedule(
        windows=[
            WeeklyWindow(weekday=day, start=start, end=end)
            for day in DEFAULT_WORKING_WEEKDAYS
        ],
        timezone=link.timezone,
    )


def _weekdays_from_settings(settings_obj) -> list[int]:
    """
    Convert CalendarSettings.working_days to Python weekdays (Monday=0).

    ASSUMPTION: working_days uses Sunday=0 … Saturday=6, matching the model's
    WEEK_START_CHOICES. Nothing in the codebase reads this field, so the
    convention is inferred rather than documented — revisit if the calendar UI
    starts writing it with different semantics.
    """
    raw = settings_obj.working_days or []
    converted: list[int] = []
    for value in raw:
        try:
            js_weekday = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= js_weekday <= 6:
            converted.append((js_weekday - 1) % 7)  # Sunday=0 → Monday=0
    return sorted(set(converted))


def _parse_hhmm(value) -> time:
    """Accept either a 'HH:MM' string or an existing time object."""
    if isinstance(value, time):
        return value
    return datetime.strptime(str(value), "%H:%M").time()
