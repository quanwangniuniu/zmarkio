"""
Where a public booking is written, and how leftover copies stay in step.

A link is either team (project calendar) or personal (the host's own
calendar). Slots and the booking itself use that one calendar — a personal
dentist appointment does not hide a team slot, and a team standup does not
land on the personal diary.

Older links dual-wrote a personal row plus a project mirror. Those still
share `metadata.booking_group` so cancel and time edits cover every copy.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Calendar, Event, EventAttendee

BOOKING_SOURCE = "booking_link"
HOST_PRIMARY_ROLE = "host_primary"
MIRROR_ROLE = "project_mirror"
PERSONAL_ROLE = "personal"
TEAM_ROLE = "team"
GUEST_ROLE = "guest"
CANONICAL_ROLES = {HOST_PRIMARY_ROLE, PERSONAL_ROLE, TEAM_ROLE, GUEST_ROLE}

_SYNC_FIELDS = (
    "title",
    "description",
    "start_datetime",
    "end_datetime",
    "status",
    "timezone",
)


def host_primary_calendar(link) -> Calendar | None:
    return Calendar.objects.filter(
        organization=link.organization,
        owner=link.owner,
        is_primary=True,
        is_deleted=False,
    ).first()


@transaction.atomic
def ensure_personal_calendar(*, organization, owner, timezone: str = "UTC") -> Calendar:
    """
    The host's personal diary. Created on demand so a booking link is never
    blocked by a missing calendar.

    Never promotes a project calendar: those stay the team's shared week view,
    and `is_primary` is what the Google connect flow keys off.
    """
    # Serialize first-time creation even when bookings use different calendars.
    get_user_model().objects.select_for_update(no_key=True).get(pk=owner.pk)
    existing = Calendar.objects.filter(
        organization=organization,
        owner=owner,
        is_primary=True,
        is_deleted=False,
    ).first()
    if existing:
        return existing

    personal = (
        Calendar.objects.filter(
            organization=organization,
            owner=owner,
            project__isnull=True,
            is_deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    if personal:
        personal.is_primary = True
        personal.save(update_fields=["is_primary", "updated_at"])
        return personal

    return Calendar.objects.create(
        organization=organization,
        owner=owner,
        name="My Calendar",
        timezone=timezone or "UTC",
        is_primary=True,
        visibility="private",
    )


def ensure_host_primary_calendar(link) -> Calendar:
    return ensure_personal_calendar(
        organization=link.organization,
        owner=link.owner,
        timezone=getattr(link, "timezone", None) or "UTC",
    )


def is_team_booking_calendar(calendar) -> bool:
    return bool(calendar and getattr(calendar, "project_id", None))


def booking_link_scope(link) -> str:
    return "team" if is_team_booking_calendar(getattr(link, "calendar", None)) else "personal"


def calendars_for_booking_availability(link) -> list[Calendar]:
    """Busy time comes only from the calendar this link books into."""
    calendar = getattr(link, "calendar", None)
    if calendar is None or getattr(calendar, "is_deleted", False):
        return []
    return [calendar]


def host_has_primary_calendar(owner) -> bool:
    if owner is None:
        return False
    return Calendar.objects.filter(
        owner=owner,
        is_primary=True,
        is_deleted=False,
    ).exists()


def event_belongs_to_booking_link(event, link) -> bool:
    """
    The cancel/feed token names an event; the URL names a link.

    New bookings live on `link.calendar`. Older dual-writes may sit on the
    host primary instead — those still match by booking_link_slug.
    """
    meta = event.metadata or {}
    if event.organization_id != link.organization_id or meta.get("source") != BOOKING_SOURCE:
        return False
    if meta.get("booking_link_id"):
        return meta["booking_link_id"] == str(link.pk)
    return meta.get("booking_link_slug") == link.slug


def booking_siblings(event):
    group = (event.metadata or {}).get("booking_group")
    if not group:
        return Event.objects.filter(pk=event.pk)
    return Event.objects.filter(
        organization=event.organization,
        metadata__source=BOOKING_SOURCE,
        metadata__booking_group=group,
    )


def lock_booking_event(event):
    """Use the host calendar lock for writes through any copy of a booking.

    Call inside an atomic block and reload after waiting, so an old edit cannot
    resurrect an event that another request has just cancelled.
    """
    canonical = booking_siblings(event).exclude(metadata__booking_role=GUEST_ROLE).order_by("pk").first()
    Calendar.objects.select_for_update(no_key=True).get(pk=(canonical or event).calendar_id)
    return Event.objects.select_for_update().get(pk=event.pk)


@transaction.atomic
def cancel_booking_events(event) -> list[Event]:
    """Soft-delete every copy of this booking. Idempotent per row."""
    event = lock_booking_event(event)
    updated: list[Event] = []
    for sibling in booking_siblings(event):
        if sibling.is_deleted and sibling.status == "cancelled":
            updated.append(sibling)
            continue
        sibling.status = "cancelled"
        sibling.is_deleted = True
        sibling.save(update_fields=["status", "is_deleted", "updated_at"])
        updated.append(sibling)
    return updated


@transaction.atomic
def sync_booking_siblings(event) -> list[Event]:
    """
    Keep the project copy (or the personal copy) in step after a host edit.

    Dragging the Harbor card must move the Google-exportable primary too.
    """
    synced: list[Event] = []
    for sibling in booking_siblings(event).exclude(pk=event.pk).filter(is_deleted=False):
        for field in _SYNC_FIELDS:
            setattr(sibling, field, getattr(event, field))
        sibling.save(update_fields=[*_SYNC_FIELDS, "updated_at"])
        synced.append(sibling)
    return synced


def prefer_visible_booking_copy(events, visible_calendar_ids) -> list:
    """
    One card per booking when both the personal and project copies are in view.

    Prefer the copy that sits on a calendar the user actually asked to see
    (the project week view), then the host-primary row.
    """
    visible = {str(calendar_id) for calendar_id in (visible_calendar_ids or [])}
    ungrouped: list = []
    groups: dict[str, list] = {}
    for event in events:
        group = (getattr(event, "metadata", None) or {}).get("booking_group")
        if not group:
            ungrouped.append(event)
            continue
        groups.setdefault(group, []).append(event)

    chosen: list = []
    role_rank = {MIRROR_ROLE: 0, HOST_PRIMARY_ROLE: 1}
    for copies in groups.values():
        on_visible = [
            event
            for event in copies
            if str(getattr(event, "calendar_id", "")) in visible
        ]
        pool = on_visible or copies
        pool.sort(
            key=lambda event: role_rank.get(
                (getattr(event, "metadata", None) or {}).get("booking_role"),
                2,
            )
        )
        chosen.append(pool[0])
    return ungrouped + chosen


@transaction.atomic
def create_booking_events(
    *,
    link,
    title: str,
    description: str,
    start: datetime,
    end: datetime,
    guest_user,
    guest_name: str,
    guest_email: str,
    guest_phone: str = "",
):
    """
    Write the host event on the link's calendar.

    Team links land on the project calendar; personal links land on the
    host's own calendar. A signed-in guest also gets a copy on their
    personal diary. Returns (event, guest_user). The cancel token names
    the host row.
    """
    calendar = getattr(link, "calendar", None) or ensure_host_primary_calendar(link)
    role = TEAM_ROLE if is_team_booking_calendar(calendar) else PERSONAL_ROLE
    group = str(uuid.uuid4())
    event = _write_event(
        link=link,
        calendar=calendar,
        title=title,
        description=description,
        start=start,
        end=end,
        guest_user=guest_user,
        guest_name=guest_name,
        guest_email=guest_email,
        guest_phone=guest_phone,
        metadata={
            "source": BOOKING_SOURCE,
            "booking_link_id": str(link.pk),
            "booking_link_slug": link.slug,
            "booking_role": role,
            "booking_group": group,
        },
    )
    # A signed-in teammate should find the meeting on their own diary, not
    # only by leaking the host's row onto every calendar they open.
    if (
        guest_user is not None
        and getattr(guest_user, "pk", None)
        and guest_user.pk != getattr(link, "owner_id", None)
        and guest_user.organization_id == link.organization_id
    ):
        guest_calendar = ensure_personal_calendar(
            organization=link.organization,
            owner=guest_user,
            timezone=getattr(link, "timezone", None) or "UTC",
        )
        if guest_calendar.id != calendar.id:
            _write_event(
                link=link,
                calendar=guest_calendar,
                title=title,
                description=description,
                start=start,
                end=end,
                guest_user=guest_user,
                guest_name=guest_name,
                guest_email=guest_email,
                guest_phone=guest_phone,
                metadata={
                    "source": BOOKING_SOURCE,
                    "booking_link_id": str(link.pk),
                    "booking_link_slug": link.slug,
                    "booking_role": GUEST_ROLE,
                    "booking_group": group,
                },
            )
    return event, guest_user


def _write_event(
    *,
    link,
    calendar,
    title: str,
    description: str,
    start: datetime,
    end: datetime,
    guest_user,
    guest_name: str,
    guest_email: str,
    guest_phone: str,
    metadata: dict,
) -> Event:
    event = Event.objects.create(
        organization=link.organization,
        calendar=calendar,
        created_by=link.owner,
        title=title,
        description=description,
        start_datetime=start,
        end_datetime=end,
        timezone=link.timezone,
        status="confirmed",
        metadata=metadata,
    )
    EventAttendee.objects.create(
        organization=link.organization,
        event=event,
        user=link.owner,
        email=link.owner.email or "",
        display_name=link.owner.get_full_name() or link.owner.username,
        is_organizer=True,
        response_status="accepted",
    )
    EventAttendee.objects.create(
        organization=link.organization,
        event=event,
        user=guest_user,
        email=guest_email,
        phone=guest_phone,
        display_name=guest_name,
        response_status="accepted",
        metadata={
            "source": BOOKING_SOURCE,
            "booking_link_slug": link.slug,
        },
    )
    return event
