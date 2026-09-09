"""
Whether attending a meeting is enough to see it.

Until now it was not: visibility came only from owning, sharing or subscribing
to a calendar, so someone who booked time appeared on the host's calendar and
nowhere in their own. This widens event reads across the calendar module, so
the tenant boundary is tested as carefully as the feature itself.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from calendars.models import Calendar, Event, EventAttendee
from core.models import Organization, Project, ProjectMember

User = get_user_model()

EVENTS_URL = "/api/events/"


class AttendeeVisibilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Vis Org", slug="vis-org")
        self.host = User.objects.create_user(
            username="vishost", email="host@vis.com", password="x",
            organization=self.org,
        )
        self.guest = User.objects.create_user(
            username="visguest", email="guest@vis.com", password="x",
            organization=self.org,
        )
        self.bystander = User.objects.create_user(
            username="visbystander", email="by@vis.com", password="x",
            organization=self.org,
        )
        # Owned by the host, and never shared with the guest: the guest's only
        # connection to this meeting is being on it.
        self.calendar = Calendar.objects.create(
            organization=self.org, owner=self.host, name="Host Primary",
            timezone="UTC", is_primary=True,
        )
        start = timezone.now() + timedelta(days=1)
        self.event = Event.objects.create(
            organization=self.org, calendar=self.calendar, created_by=self.host,
            title="Intro Call with Grace", start_datetime=start,
            end_datetime=start + timedelta(minutes=30), timezone="UTC",
            status="confirmed",
        )
        EventAttendee.objects.create(
            organization=self.org, event=self.event, user=self.host,
            email=self.host.email, is_organizer=True, response_status="accepted",
        )
        EventAttendee.objects.create(
            organization=self.org, event=self.event, user=self.guest,
            email=self.guest.email, response_status="accepted",
            metadata={"source": "booking_link"},
        )

    def _titles_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.get(EVENTS_URL)
        assert response.status_code == status.HTTP_200_OK, response.json()
        body = response.json()
        rows = body if isinstance(body, list) else body.get("results", [])
        return [row["title"] for row in rows]

    def test_a_guest_sees_a_meeting_they_were_booked_into(self):
        assert "Intro Call with Grace" in self._titles_for(self.guest)

    def test_the_host_still_sees_their_own_meeting(self):
        assert "Intro Call with Grace" in self._titles_for(self.host)

    def test_someone_not_on_it_still_sees_nothing(self):
        # The widening must not turn every event in the org into public reading.
        assert "Intro Call with Grace" not in self._titles_for(self.bystander)

    def test_the_meeting_is_listed_once_not_once_per_attendee(self):
        # Joining across attendees duplicates rows without a distinct().
        titles = self._titles_for(self.guest)
        assert titles.count("Intro Call with Grace") == 1

    def test_a_cross_organisation_attendance_cannot_exist_in_the_first_place(self):
        # Orgs without their own schema share `public`, so attendance conferring
        # visibility would be a leak if one org's event could list another org's
        # user. The model refuses it, which is why the widening is safe; the
        # organisation filter in the query is the second line of defence.
        other_org = Organization.objects.create(name="Other Vis", slug="other-vis")
        outsider = User.objects.create_user(
            username="outsidervis", email="out@other.com", password="x",
            organization=other_org,
        )
        with self.assertRaises(ValidationError):
            EventAttendee.objects.create(
                organization=self.org, event=self.event, user=outsider,
                email=outsider.email, response_status="accepted",
            )
        assert "Intro Call with Grace" not in self._titles_for(outsider)

    def test_a_guest_on_the_project_can_cancel_from_their_own_calendar(self):
        # Seeing it is only half of Ray's "either party cancels". On a project
        # calendar a member maps to `edit`, so the guest can act on it in-app
        # rather than having to dig out the emailed token link.
        project = Project.objects.create(
            name="Vis Project", organization=self.org, owner=self.host
        )
        for user in (self.host, self.guest):
            ProjectMember.objects.create(
                project=project, user=user, role="member", is_active=True
            )
        project_calendar = Calendar.objects.create(
            organization=self.org, owner=self.host, project=project,
            name="Vis Project Calendar", timezone="UTC",
        )
        start = timezone.now() + timedelta(days=4)
        event = Event.objects.create(
            organization=self.org, calendar=project_calendar, created_by=self.host,
            title="Project Booking", start_datetime=start,
            end_datetime=start + timedelta(minutes=30), timezone="UTC",
        )
        EventAttendee.objects.create(
            organization=self.org, event=event, user=self.guest,
            email=self.guest.email, response_status="accepted",
            metadata={"source": "booking_link"},
        )

        client = APIClient()
        client.force_authenticate(user=self.guest)
        response = client.delete(f"{EVENTS_URL}{event.id}/")
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
        ), getattr(response, "data", response.content)
        event.refresh_from_db()
        assert event.is_deleted is True

    def test_a_deleted_attendance_stops_conferring_visibility(self):
        attendance = EventAttendee.objects.get(event=self.event, user=self.guest)
        attendance.is_deleted = True
        attendance.save(update_fields=["is_deleted"])
        assert "Intro Call with Grace" not in self._titles_for(self.guest)
