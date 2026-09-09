"""
Availability math for booking links.

Deliberately model-free: everything here takes plain values and returns plain
values, so the same logic serves the in-app calendar, the Google merge, and the
public booking page without any of them depending on each other. That also
makes the slot arithmetic — the part most likely to be wrong — directly
unit-testable without database or HTTP fixtures.

Conventions:
- Every datetime crossing this module's boundary is timezone-aware UTC.
- Intervals are half-open [start, end): an event ending at 10:00 does not
  collide with one starting at 10:00.
- Weekdays follow Python's convention (Monday=0 … Sunday=6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

Interval = tuple[datetime, datetime]

# Hard ceiling on how many slots one request may produce, so a wide date range
# with a small increment can't be used to exhaust memory on a public endpoint.
MAX_SLOTS = 2000


@dataclass(frozen=True)
class BookingRules:
    """Booking constraints for one link. Durations are in minutes."""

    duration_minutes: int
    slot_increment_minutes: int = 15
    # Free time required immediately before / after a candidate booking.
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    # Earliest a prospect may book, relative to now.
    min_notice_minutes: int = 0
    # How far ahead the link accepts bookings.
    max_advance_days: int = 60

    def __post_init__(self) -> None:
        if self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive.")
        if self.slot_increment_minutes <= 0:
            raise ValueError("slot_increment_minutes must be positive.")
        if self.max_advance_days <= 0:
            raise ValueError("max_advance_days must be positive.")
        if self.buffer_before_minutes < 0 or self.buffer_after_minutes < 0:
            raise ValueError("Buffers cannot be negative.")
        if self.min_notice_minutes < 0:
            raise ValueError("min_notice_minutes cannot be negative.")


@dataclass(frozen=True)
class WeeklyWindow:
    """A recurring weekly availability window in the owner's local time."""

    weekday: int  # Monday=0 … Sunday=6
    start: time
    end: time

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be 0 (Monday) through 6 (Sunday).")
        if self.start >= self.end:
            raise ValueError("Window start must be earlier than its end.")


# ── Interval algebra ─────────────────────────────────────────────────────


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """Sort and coalesce overlapping or touching intervals."""
    cleaned = [(s, e) for s, e in intervals if e > s]
    if not cleaned:
        return []
    cleaned.sort(key=lambda pair: pair[0])

    merged = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:  # overlapping or exactly adjacent
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(base: list[Interval], blocked: list[Interval]) -> list[Interval]:
    """Remove `blocked` from `base`, returning what remains of `base`."""
    blocked_merged = merge_intervals(blocked)
    remaining: list[Interval] = []

    for start, end in merge_intervals(base):
        cursor = start
        for block_start, block_end in blocked_merged:
            if block_end <= cursor or block_start >= end:
                continue  # no overlap with what's left of this base interval
            if block_start > cursor:
                remaining.append((cursor, block_start))
            cursor = max(cursor, block_end)
            if cursor >= end:
                break
        if cursor < end:
            remaining.append((cursor, end))
    return remaining


def pad_busy_intervals(
    busy: list[Interval], rules: BookingRules
) -> list[Interval]:
    """
    Grow busy intervals so buffers are respected.

    The padding is deliberately crossed: requiring free time *before* a new
    booking means an existing event blocks later than it ends, and requiring
    free time *after* means it blocks earlier than it starts.
    """
    before = timedelta(minutes=rules.buffer_before_minutes)
    after = timedelta(minutes=rules.buffer_after_minutes)
    if not before and not after:
        return merge_intervals(busy)
    return merge_intervals([(start - after, end + before) for start, end in busy])


# ── Working hours ────────────────────────────────────────────────────────


def expand_weekly_windows(
    windows: list[WeeklyWindow],
    range_start: datetime,
    range_end: datetime,
    tz_name: str,
) -> list[Interval]:
    """
    Materialise recurring weekly windows into concrete UTC intervals.

    Windows are anchored to local wall-clock time and converted per day, so a
    "09:00–17:00" window stays 09:00–17:00 local across a DST transition rather
    than drifting by an hour.
    """
    if not windows:
        return []

    tz = ZoneInfo(tz_name)
    by_weekday: dict[int, list[WeeklyWindow]] = {}
    for window in windows:
        by_weekday.setdefault(window.weekday, []).append(window)

    # Walk local dates, padded by a day each side so a window that straddles
    # the range boundary in local time is still considered.
    local_start = range_start.astimezone(tz)
    local_end = range_end.astimezone(tz)
    current: date = local_start.date() - timedelta(days=1)
    last: date = local_end.date() + timedelta(days=1)

    materialised: list[Interval] = []
    while current <= last:
        for window in by_weekday.get(current.weekday(), []):
            start = datetime.combine(current, window.start, tzinfo=tz).astimezone(dt_timezone.utc)
            end = datetime.combine(current, window.end, tzinfo=tz).astimezone(dt_timezone.utc)
            # Clip to the requested range rather than dropping partial windows.
            start = max(start, range_start)
            end = min(end, range_end)
            if end > start:
                materialised.append((start, end))
        current += timedelta(days=1)

    return merge_intervals(materialised)


# ── Slot generation ──────────────────────────────────────────────────────


def generate_slots(
    free: list[Interval], rules: BookingRules, tz_name: str
) -> list[Interval]:
    """
    Cut bookable slots out of free intervals.

    Start times are aligned to the increment measured from local midnight, so a
    15-minute increment yields :00/:15/:30/:45 rather than offsets inherited
    from whenever the previous meeting happened to end. Local midnight — not
    UTC — because zones like Asia/Kolkata sit at a half-hour offset.
    """
    tz = ZoneInfo(tz_name)
    duration = timedelta(minutes=rules.duration_minutes)
    increment = timedelta(minutes=rules.slot_increment_minutes)

    slots: list[Interval] = []
    for start, end in free:
        cursor = _align_to_increment(start, tz, increment)
        while cursor + duration <= end:
            slots.append((cursor, cursor + duration))
            if len(slots) >= MAX_SLOTS:
                return slots
            cursor += increment
    return slots


def _align_to_increment(
    moment: datetime, tz: ZoneInfo, increment: timedelta
) -> datetime:
    """Round `moment` up to the next increment boundary from local midnight."""
    local = moment.astimezone(tz)
    midnight = datetime.combine(local.date(), time(0, 0), tzinfo=tz)
    elapsed = local - midnight
    step = increment.total_seconds()
    if step <= 0:
        return moment
    steps = int(-(-elapsed.total_seconds() // step))  # ceiling division
    aligned_local = (midnight + timedelta(seconds=steps * step)).replace(fold=local.fold)
    aligned = aligned_local.astimezone(dt_timezone.utc)
    return aligned if aligned >= moment else moment


# ── Orchestration ────────────────────────────────────────────────────────


def compute_available_slots(
    *,
    windows: list[WeeklyWindow],
    busy: list[Interval],
    rules: BookingRules,
    tz_name: str,
    range_start: datetime,
    range_end: datetime,
    now: datetime,
) -> list[Interval]:
    """
    Bookable slots for one link, as UTC intervals.

    Applies, in order: the booking horizon and notice period, the owner's
    weekly windows, then busy time (padded by the configured buffers).
    """
    earliest = max(range_start, now + timedelta(minutes=rules.min_notice_minutes))
    latest = min(range_end, now + timedelta(days=rules.max_advance_days))
    if earliest >= latest:
        return []

    open_windows = expand_weekly_windows(windows, earliest, latest, tz_name)
    if not open_windows:
        return []

    free = subtract_intervals(open_windows, pad_busy_intervals(busy, rules))
    return generate_slots(free, rules, tz_name)
