"""
Who gets told what, and - just as importantly - who does not.

Guests have no account, so the rule throughout is that only people already in
the system are notified. Everything here is about that boundary.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from calendars.models import BookingLink, Calendar, Event, EventAttendee
from core.models import Organization, Project, ProjectMember
from notifications.models import Notification

User = get_user_model()

LIST_URL = "/api/booking-links/"


class BookingLinkNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Notify Org", slug="notify-org")
        self.creator = User.objects.create_user(
            username="creator", email="creator@notify.com", password="x",
            organization=self.org,
        )
        self.host = User.objects.create_user(
            username="host", email="host@notify.com", password="x",
            organization=self.org,
        )
        self.guest = User.objects.create_user(
            username="guest", email="guest@notify.com", password="x",
            organization=self.org,
        )
        self.project = Project.objects.create(
            name="Notify Project", organization=self.org, owner=self.creator
        )
        for user in (self.creator, self.host, self.guest):
            ProjectMember.objects.create(
                project=self.project, user=user, role="member", is_active=True
            )
        self.calendar = Calendar.objects.create(
            organization=self.org, owner=self.creator, project=self.project,
            name="Project Calendar", timezone="UTC",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.creator)

    def _create(self, **overrides):
        payload = {
            "slug": "catch-up",
            "title": "Catch up",
            "calendar_id": str(self.calendar.id),
            "duration_minutes": 30,
        }
        payload.update(overrides)
        return self.client.post(LIST_URL, payload, format="json")

    def test_the_host_is_told_when_someone_sets_up_a_link_for_them(self):
        assert self._create(host_id=self.host.id).status_code == status.HTTP_201_CREATED
        assert Notification.objects.filter(recipient=self.host).count() == 1

    def test_setting_up_your_own_link_notifies_nobody(self):
        assert self._create().status_code == status.HTTP_201_CREATED
        # The host is the actor, and there is no guest to tell.
        assert Notification.objects.count() == 0

    def test_a_named_guest_is_told(self):
        response = self._create(host_id=self.host.id, invitee_ids=[self.guest.id])
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        note = Notification.objects.get(recipient=self.guest)
        assert note.action_url == "/book/notify-org/catch-up"
        assert note.metadata.get("link_slug") == "catch-up"
        assert note.metadata.get("organization_slug") == "notify-org"

    def test_every_named_guest_is_told(self):
        second = User.objects.create_user(
            username="guest2", email="guest2@notify.com", password="x",
            organization=self.org,
        )
        ProjectMember.objects.create(
            project=self.project, user=second, role="member", is_active=True
        )
        response = self._create(
            host_id=self.host.id, invitee_ids=[self.guest.id, second.id]
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert Notification.objects.filter(recipient=self.guest).count() == 1
        assert Notification.objects.filter(recipient=second).count() == 1

    def test_an_emailed_guest_gets_no_in_app_notification(self):
        # Nothing to notify: an address is not an account.
        response = self._create(
            host_id=self.host.id, invitee_emails=["stranger@example.com"]
        )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert Notification.objects.filter(recipient=self.host).count() == 1
        assert Notification.objects.count() == 1

    def test_editing_the_rules_does_not_re_announce_the_link(self):
        created = self._create(host_id=self.host.id)
        link_id = created.json()["id"]
        Notification.objects.all().delete()

        response = self.client.patch(
            f"{LIST_URL}{link_id}/", {"duration_minutes": 45}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        # Only newly attached people are worth interrupting.
        assert Notification.objects.count() == 0

    def test_adding_a_guest_later_does_notify_them(self):
        created = self._create(host_id=self.host.id)
        link_id = created.json()["id"]
        Notification.objects.all().delete()

        response = self.client.patch(
            f"{LIST_URL}{link_id}/", {"invitee_ids": [self.guest.id]}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        assert Notification.objects.filter(recipient=self.guest).count() == 1

    def test_adding_a_second_guest_leaves_the_first_alone(self):
        created = self._create(host_id=self.host.id, invitee_ids=[self.guest.id])
        link_id = created.json()["id"]
        Notification.objects.all().delete()

        second = User.objects.create_user(
            username="guest3", email="guest3@notify.com", password="x",
            organization=self.org,
        )
        ProjectMember.objects.create(
            project=self.project, user=second, role="member", is_active=True
        )
        response = self.client.patch(
            f"{LIST_URL}{link_id}/",
            {"invitee_ids": [self.guest.id, second.id]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK, response.json()
        # Only the newcomer is interrupted; the first guest already knew.
        assert Notification.objects.filter(recipient=second).count() == 1
        assert Notification.objects.filter(recipient=self.guest).count() == 0

    def test_a_failing_notification_does_not_sink_the_link(self):
        from unittest.mock import patch

        with patch(
            "calendars.booking_notifications.create_notification",
            side_effect=RuntimeError("bell is down"),
        ):
            response = self._create(host_id=self.host.id)
        # The link is the deliverable; the bell is not.
        assert response.status_code == status.HTTP_201_CREATED
        assert BookingLink.objects.filter(slug="catch-up").exists()


class HostCancellationTests(TestCase):
    """
    The other half of "either party can cancel".

    A guest cancelling tells the host via the public endpoint. This covers the
    reverse: the host deleting the meeting from their own calendar has to reach
    the guest, or the guest turns up to a meeting that no longer exists.
    """

    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name="Cancel Org", slug="cancel-org")
        self.host = User.objects.create_user(
            username="cancelhost", email="host@cancel.com", password="x",
            organization=self.org,
        )
        self.guest = User.objects.create_user(
            username="cancelguest", email="guest@cancel.com", password="x",
            organization=self.org,
        )
        self.calendar = Calendar.objects.create(
            organization=self.org, owner=self.host, name="Primary",
            timezone="UTC", is_primary=True,
        )
        start = timezone.now() + timedelta(days=2)
        self.event = Event.objects.create(
            organization=self.org,
            calendar=self.calendar,
            created_by=self.host,
            title="Intro Call with Grace",
            start_datetime=start,
            end_datetime=start + timedelta(minutes=30),
            timezone="UTC",
            status="confirmed",
        )
        EventAttendee.objects.create(
            organization=self.org, event=self.event, user=self.host,
            email=self.host.email, is_organizer=True, response_status="accepted",
        )
        EventAttendee.objects.create(
            organization=self.org, event=self.event, user=self.guest,
            email=self.guest.email, response_status="accepted",
            metadata={"source": "booking_link", "booking_link_slug": "intro"},
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.host)

    def test_the_guest_is_told_when_the_host_cancels(self):
        response = self.client.delete(f"/api/events/{self.event.id}/")
        assert response.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)
        assert Notification.objects.filter(recipient=self.guest).count() == 1

    def test_the_host_does_not_notify_themselves(self):
        self.client.delete(f"/api/events/{self.event.id}/")
        assert Notification.objects.filter(recipient=self.host).count() == 0

    def test_deleting_an_ordinary_event_announces_nothing(self):
        # Every event deletion runs through this path; only bookings warrant a
        # notification, or the bell fills with routine tidying up.
        start = timezone.now() + timedelta(days=3)
        plain = Event.objects.create(
            organization=self.org, calendar=self.calendar, created_by=self.host,
            title="Team sync", start_datetime=start,
            end_datetime=start + timedelta(minutes=30), timezone="UTC",
        )
        EventAttendee.objects.create(
            organization=self.org, event=plain, user=self.guest,
            email=self.guest.email, response_status="accepted",
        )
        self.client.delete(f"/api/events/{plain.id}/")
        assert Notification.objects.count() == 0
