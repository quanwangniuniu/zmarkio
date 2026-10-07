"""Invite notification stays in step with the actual booking."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from calendars.booking_invite_state import mark_invite_booked, mark_invite_unbooked
from calendars.booking_write import create_booking_events
from calendars.models import BookingLink, Calendar
from calendars.test_public_booking import WEEKDAY_WINDOWS, next_weekday_at
from core.models import Organization
from notifications.models import Notification, NotificationCategory, NotificationEventType

User = get_user_model()


class BookingInviteStateTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="State Org", slug="state-org")
        self.host = User.objects.create_user(
            username="statehost",
            email="host@state.com",
            password="x",
            organization=self.org,
        )
        self.guest = User.objects.create_user(
            username="stateguest",
            email="guest@state.com",
            password="x",
            organization=self.org,
        )
        self.calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.host,
            name="Primary",
            timezone="UTC",
            is_primary=True,
        )
        self.link = BookingLink.objects.create(
            organization=self.org,
            owner=self.host,
            calendar=self.calendar,
            slug="intro",
            title="Intro",
            duration_minutes=30,
            timezone="UTC",
            availability_windows=WEEKDAY_WINDOWS,
        )
        self.notice = Notification.objects.create(
            recipient=self.guest,
            actor=self.host,
            category=NotificationCategory.MEETINGS,
            event_type=NotificationEventType.MEETING_PARTICIPANT_ADDED,
            title="Invite",
            related_object_type="booking_link",
            related_object_id=str(self.link.id),
            metadata={"source": "booking_link"},
        )
        start = next_weekday_at(10)
        self.event, _ = create_booking_events(
            link=self.link,
            title="Intro with Guest",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=self.guest,
            guest_name="Guest",
            guest_email=self.guest.email,
        )

    def test_booking_marks_the_invite_accepted_with_a_slot(self):
        mark_invite_booked(self.link, self.guest, self.event, "token-1")
        self.notice.refresh_from_db()
        assert self.notice.responded is True
        assert self.notice.response == "accept"
        assert self.notice.metadata.get("booked") is True
        assert self.notice.metadata.get("cancel_token") == "token-1"

    def test_guest_cancel_reopens_the_invite(self):
        mark_invite_booked(self.link, self.guest, self.event, "token-1")
        mark_invite_unbooked(self.link, self.guest)
        self.notice.refresh_from_db()
        assert self.notice.responded is False
        assert self.notice.metadata.get("booked") is False
        assert self.notice.metadata.get("can_rebook") is True

    def test_legacy_accept_without_a_slot_can_still_be_declined(self):
        self.notice.responded = True
        self.notice.response = "accept"
        self.notice.save(update_fields=["responded", "response"])
        client = APIClient()
        client.force_authenticate(user=self.guest)
        response = client.post(
            f"/api/notifications/{self.notice.id}/respond/",
            {"action": "reject"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        self.notice.refresh_from_db()
        assert self.notice.response == "reject"
