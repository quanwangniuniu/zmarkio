"""
Find a guest's booking again without the mail they never received.

Contact matching is for delivery to the recorded email address. It does not
prove the caller owns a booking and must never expose cancellation tokens.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet
from django.utils import timezone

from .booking_write import BOOKING_SOURCE, GUEST_ROLE, event_belongs_to_booking_link
from .models import Event, EventAttendee


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def phones_match(stored: str, query: str) -> bool:
    """Same number even if one side kept spaces or a country code."""
    left = _digits(stored)
    right = _digits(query)
    if not left or not right:
        return False
    if left == right:
        return True
    return len(left) >= 8 and len(right) >= 8 and (
        left.endswith(right) or right.endswith(left)
    )


def _upcoming_guest_attendees(link) -> QuerySet[EventAttendee]:
    return (
        EventAttendee.objects.filter(
            organization=link.organization,
            is_organizer=False,
            event__is_deleted=False,
            event__start_datetime__gte=timezone.now(),
            event__metadata__source=BOOKING_SOURCE,
        )
        .exclude(event__status="cancelled")
        .filter(Q(event__metadata__booking_role__isnull=True) | ~Q(event__metadata__booking_role=GUEST_ROLE))
        .select_related("event")
    )


def _dedupe_canonical(events: list[Event]) -> list[Event]:
    by_group: dict[str, Event] = {}
    for event in events:
        group = (event.metadata or {}).get("booking_group") or str(event.pk)
        existing = by_group.get(group)
        if existing is None:
            by_group[group] = event
    return sorted(by_group.values(), key=lambda event: event.start_datetime)


def find_guest_bookings(
    *,
    link,
    name: str = "",
    email: str = "",
    phone: str = "",
) -> list[Event]:
    """
    Upcoming bookings on this link matching exactly one of name / email / phone.

    Dedupes primary + project copies. The cancel token names the canonical
    (host primary) row when both exist. The host's own attendee row is
    ignored so looking up the owner does not list every booking.
    """
    name = (name or "").strip()
    email = (email or "").strip()
    phone = (phone or "").strip()

    attendees = _upcoming_guest_attendees(link)
    if email:
        attendees = attendees.filter(email__iexact=email)
    elif name:
        attendees = attendees.filter(display_name__iexact=name)
    matched: list[Event] = []
    for attendee in attendees:
        event = attendee.event
        if not event_belongs_to_booking_link(event, link):
            continue
        if email and (attendee.email or "").lower() == email.lower():
            matched.append(event)
        elif name and (attendee.display_name or "").strip().lower() == name.lower():
            matched.append(event)
        elif phone and phones_match(attendee.phone or "", phone):
            matched.append(event)

    return _dedupe_canonical(matched)


def find_viewer_bookings(link, user) -> list[Event]:
    """Upcoming bookings on this link for the signed-in viewer only."""
    if not getattr(user, "is_authenticated", False):
        return []
    email = (getattr(user, "email", None) or "").strip()
    if not email:
        return []
    return find_guest_bookings(link=link, email=email)


def serialize_viewer_bookings(events: list[Event]) -> list[dict]:
    return [
        {
            "start": event.start_datetime.isoformat().replace("+00:00", "Z"),
            "end": event.end_datetime.isoformat().replace("+00:00", "Z"),
            "title": event.title,
        }
        for event in events
    ]
