"""Keep the original booking-link invite notification in step with the booking."""

from django.utils import timezone
from django.db.models import Q

from notifications.models import Notification


def _invite_queryset(link, user):
    return Notification.objects.filter(
        recipient=user,
        related_object_type="booking_link",
        related_object_id=str(link.id),
    )


def mark_invite_booked(link, user, event, cancel_token: str) -> None:
    if user is None:
        return
    start = event.start_datetime.isoformat().replace("+00:00", "Z")
    end = event.end_datetime.isoformat().replace("+00:00", "Z")
    for notice in _invite_queryset(link, user):
        meta = dict(notice.metadata or {})
        meta.update(
            {
                "booked": True,
                "can_rebook": True,
                "event_id": str(event.pk),
                "start": start,
                "end": end,
                "cancel_token": cancel_token,
            }
        )
        notice.metadata = meta
        notice.responded = True
        notice.response = "accept"
        notice.save(update_fields=["metadata", "responded", "response"])


def mark_invite_unbooked(link, user) -> None:
    if user is None:
        return
    for notice in _invite_queryset(link, user):
        meta = dict(notice.metadata or {})
        meta["booked"] = False
        meta.pop("event_id", None)
        meta.pop("start", None)
        meta.pop("end", None)
        meta.pop("cancel_token", None)
        if meta.get("can_rebook") is False:
            notice.metadata = meta
            notice.save(update_fields=["metadata"])
            continue
        meta["can_rebook"] = True
        notice.metadata = meta
        notice.responded = False
        notice.response = ""
        notice.save(update_fields=["metadata", "responded", "response"])


def invite_is_booked(notification) -> bool:
    return bool((notification.metadata or {}).get("booked"))


def find_upcoming_guest_booking(link, user):
    from .booking_write import BOOKING_SOURCE
    from .models import Event, EventAttendee

    if user is None:
        return None
    attendee_event_ids = EventAttendee.objects.filter(
        user=user,
        is_organizer=False,
        event__organization=link.organization,
        event__is_deleted=False,
    ).exclude(event__status="cancelled").values_list("event_id", flat=True)
    return (
        Event.objects.filter(
            pk__in=attendee_event_ids,
            organization=link.organization,
            start_datetime__gte=timezone.now(),
            metadata__source=BOOKING_SOURCE,
        ).filter(
            Q(metadata__booking_link_id=str(link.pk)) |
            Q(metadata__booking_link_id__isnull=True, metadata__booking_link_slug=link.slug)
        )
        .filter(Q(metadata__booking_role__isnull=True) | ~Q(metadata__booking_role="guest"))
        .order_by("start_datetime")
        .first()
    )
