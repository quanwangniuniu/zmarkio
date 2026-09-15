"""
Where a booking is written, and how its copies stay in step.

The public HTTP tests still cover the happy path when the link calendar *is*
the host primary (one row). This file is the dual-write case: project calendar
as "book into", host primary as the diary / Google target.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from calendars.booking_write import (
    GUEST_ROLE,
    HOST_PRIMARY_ROLE,
    MIRROR_ROLE,
    PERSONAL_ROLE,
    TEAM_ROLE,
    calendars_for_booking_availability,
    cancel_booking_events,
    create_booking_events,
    ensure_host_primary_calendar,
    event_belongs_to_booking_link,
    prefer_visible_booking_copy,
    sync_booking_siblings,
)
from calendars.models import BookingLink, Calendar, Event
from calendars.test_public_booking import (
    FREEBUSY_PATH,
    WEEKDAY_WINDOWS,
    in_org,
    next_weekday_at,
)
from core.models import Organization, Project, ProjectMember

User = get_user_model()


class BookingWriteHelpersTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Write Org", slug="write-org")
        self.host = User.objects.create_user(
            username="writehost",
            email="host@write.com",
            password="x",
            organization=self.org,
        )
        self.primary = Calendar.objects.create(
            organization=self.org,
            owner=self.host,
            name="Primary",
            timezone="UTC",
            is_primary=True,
        )
        self.project = Project.objects.create(
            name="Harbor", organization=self.org, owner=self.host
        )
        ProjectMember.objects.create(
            user=self.host, project=self.project, role="owner", is_active=True
        )
        self.project_calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.host,
            project=self.project,
            name="Harbor Calendar",
            timezone="UTC",
            is_primary=False,
        )
        self.link = BookingLink.objects.create(
            organization=self.org,
            owner=self.host,
            calendar=self.project_calendar,
            slug="intro",
            title="Intro",
            duration_minutes=30,
            timezone="UTC",
            availability_windows=WEEKDAY_WINDOWS,
        )

    def test_availability_uses_only_the_link_calendar(self):
        calendars = calendars_for_booking_availability(self.link)
        assert [c.id for c in calendars] == [self.project_calendar.id]

    def test_availability_uses_personal_calendar_when_the_link_is_personal(self):
        self.link.calendar = self.primary
        self.link.save(update_fields=["calendar"])
        calendars = calendars_for_booking_availability(self.link)
        assert [c.id for c in calendars] == [self.primary.id]

    def test_ensure_reuses_existing_primary(self):
        assert ensure_host_primary_calendar(self.link).id == self.primary.id

    def test_ensure_creates_a_personal_primary_when_missing(self):
        self.primary.is_primary = False
        self.primary.save(update_fields=["is_primary"])
        self.primary.delete()
        created = ensure_host_primary_calendar(self.link)
        assert created.is_primary is True
        assert created.project_id is None
        assert created.owner_id == self.host.id

    def test_create_writes_only_the_team_calendar(self):
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="Booked by Grace",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        assert event.calendar_id == self.project_calendar.id
        assert event.metadata["booking_role"] == TEAM_ROLE
        assert Event.objects.filter(title="Intro with Grace").count() == 1
        assert not Event.objects.filter(calendar=self.primary, title="Intro with Grace").exists()

    def test_create_writes_only_the_personal_calendar(self):
        self.link.calendar = self.primary
        self.link.save(update_fields=["calendar"])
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        assert Event.objects.filter(title="Intro with Grace").count() == 1
        assert event.calendar_id == self.primary.id
        assert event.metadata["booking_role"] == PERSONAL_ROLE

    def test_create_writes_a_personal_copy_for_a_signed_in_guest(self):
        guest = User.objects.create_user(
            username="writeguest",
            email="guest@write.com",
            password="x",
            organization=self.org,
        )
        self.link.calendar = self.primary
        self.link.save(update_fields=["calendar"])
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=guest,
            guest_name="Grace",
            guest_email=guest.email,
        )
        rows = list(Event.objects.filter(title="Intro with Grace").order_by("created_at"))
        assert len(rows) == 2
        assert event.calendar_id == self.primary.id
        guest_copy = next(row for row in rows if row.calendar_id != self.primary.id)
        assert guest_copy.calendar.owner_id == guest.id
        assert guest_copy.calendar.project_id is None
        assert guest_copy.metadata["booking_role"] == GUEST_ROLE
        assert guest_copy.metadata["booking_group"] == event.metadata["booking_group"]

    def test_week_view_hides_a_personal_booking_when_team_is_selected(self):
        self.link.calendar = self.primary
        self.link.save(update_fields=["calendar"])
        start = timezone.now() + timedelta(days=1)
        create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        client = APIClient()
        client.force_authenticate(user=self.host)
        response = client.get(
            "/api/views/week/",
            {
                "start_date": start.date().isoformat(),
                "calendar_ids": str(self.project_calendar.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        titles = [
            row["title"]
            for row in response.json()["events"]
            if row["title"] == "Intro with Grace"
        ]
        assert titles == []

    def test_a_guest_without_their_own_copy_still_sees_the_host_booking(self):
        guest = User.objects.create_user(
            username="viewguest",
            email="viewguest@write.com",
            password="x",
            organization=self.org,
        )
        ProjectMember.objects.create(
            user=guest, project=self.project, role="member", is_active=True
        )
        self.link.calendar = self.primary
        self.link.save(update_fields=["calendar"])
        start = timezone.now() + timedelta(days=1)
        event, _ = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email=guest.email,
        )
        from calendars.models import EventAttendee

        attendee = EventAttendee.objects.get(event=event, is_organizer=False)
        attendee.user = guest
        attendee.save(update_fields=["user", "updated_at"])
        client = APIClient()
        client.force_authenticate(user=guest)
        response = client.get(
            "/api/views/week/",
            {
                "start_date": start.date().isoformat(),
                "calendar_ids": str(self.project_calendar.id),
                "project_id": self.project.id,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        titles = [
            row["title"]
            for row in response.json()["events"]
            if row["title"] == "Intro with Grace"
        ]
        assert titles == ["Intro with Grace"]

    def test_cancel_soft_deletes_the_booking(self):
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        cancel_booking_events(event)
        for row in Event.objects.filter(title="Intro with Grace"):
            assert row.is_deleted is True
            assert row.status == "cancelled"

    def test_sync_moves_a_legacy_sibling(self):
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        mirror = Event.objects.create(
            organization=self.org,
            calendar=self.primary,
            created_by=self.host,
            title=event.title,
            start_datetime=event.start_datetime,
            end_datetime=event.end_datetime,
            timezone="UTC",
            metadata={
                "source": "booking_link",
                "booking_link_slug": self.link.slug,
                "booking_role": HOST_PRIMARY_ROLE,
                "booking_group": event.metadata["booking_group"],
            },
        )
        event.start_datetime = start + timedelta(hours=2)
        event.end_datetime = start + timedelta(hours=2, minutes=30)
        event.title = "Moved"
        event.save(update_fields=["start_datetime", "end_datetime", "title", "updated_at"])
        sync_booking_siblings(event)
        mirror.refresh_from_db()
        assert mirror.start_datetime == event.start_datetime
        assert mirror.title == "Moved"

    def test_token_event_on_the_link_calendar_belongs_to_the_link(self):
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        assert event.calendar_id == self.link.calendar_id
        assert event_belongs_to_booking_link(event, self.link) is True

    def test_unrelated_event_on_primary_does_not_belong(self):
        start = timezone.now() + timedelta(days=1)
        other = Event.objects.create(
            organization=self.org,
            calendar=self.primary,
            created_by=self.host,
            title="Standup",
            start_datetime=start,
            end_datetime=start + timedelta(minutes=30),
            timezone="UTC",
        )
        assert event_belongs_to_booking_link(other, self.link) is False

    def test_view_keeps_the_project_copy_when_that_calendar_is_selected(self):
        primary_copy = SimpleNamespace(
            calendar_id=self.primary.id,
            metadata={"booking_group": "g1", "booking_role": HOST_PRIMARY_ROLE},
        )
        project_copy = SimpleNamespace(
            calendar_id=self.project_calendar.id,
            metadata={"booking_group": "g1", "booking_role": MIRROR_ROLE},
        )
        other = SimpleNamespace(calendar_id=self.primary.id, metadata={})
        kept = prefer_visible_booking_copy(
            [primary_copy, project_copy, other],
            visible_calendar_ids=[self.project_calendar.id],
        )
        assert kept == [other, project_copy]

    def test_week_view_shows_one_card_when_both_copies_are_visible(self):
        start = timezone.now() + timedelta(days=1)
        create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=self.host,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        client = APIClient()
        client.force_authenticate(user=self.host)
        response = client.get(
            "/api/views/week/",
            {
                "start_date": start.date().isoformat(),
                "calendar_ids": str(self.project_calendar.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        titles = [
            row["title"]
            for row in response.json()["events"]
            if row["title"] == "Intro with Grace"
        ]
        assert len(titles) == 1

    def test_deleting_the_booking_cancels_legacy_siblings_too(self):
        start = timezone.now() + timedelta(days=1)
        event, _guest = create_booking_events(
            link=self.link,
            title="Intro with Grace",
            description="",
            start=start,
            end=start + timedelta(minutes=30),
            guest_user=None,
            guest_name="Grace",
            guest_email="grace@example.com",
        )
        sibling = Event.objects.create(
            organization=self.org,
            calendar=self.primary,
            created_by=self.host,
            title=event.title,
            start_datetime=event.start_datetime,
            end_datetime=event.end_datetime,
            timezone="UTC",
            metadata={
                "source": "booking_link",
                "booking_link_slug": self.link.slug,
                "booking_role": HOST_PRIMARY_ROLE,
                "booking_group": event.metadata["booking_group"],
            },
        )
        client = APIClient()
        client.force_authenticate(user=self.host)
        response = client.delete(f"/api/events/{event.id}/")
        assert response.status_code == status.HTTP_204_NO_CONTENT
        event.refresh_from_db()
        sibling.refresh_from_db()
        assert event.is_deleted is True
        assert sibling.is_deleted is True


class PublicBookingHostDiaryTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.org = Organization.objects.create(name="Diary Org", slug="diary-org")
        self.host = User.objects.create_user(
            username="diaryhost",
            email="host@diary.com",
            password="x",
            organization=self.org,
        )
        with in_org(self.org):
            self.primary = Calendar.objects.create(
                organization=self.org,
                owner=self.host,
                name="Primary",
                timezone="UTC",
                is_primary=True,
            )
            project = Project.objects.create(
                name="Harbor", organization=self.org, owner=self.host
            )
            self.project_calendar = Calendar.objects.create(
                organization=self.org,
                owner=self.host,
                project=project,
                name="Harbor Calendar",
                timezone="UTC",
                is_primary=False,
            )
            self.link = BookingLink.objects.create(
                organization=self.org,
                owner=self.host,
                calendar=self.project_calendar,
                slug="intro-call",
                title="Intro Call",
                duration_minutes=60,
                slot_increment_minutes=60,
                min_notice_minutes=0,
                timezone="UTC",
                availability_windows=WEEKDAY_WINDOWS,
            )
        self.client = APIClient()
        self.availability_url = f"/api/public/book/{self.org.slug}/{self.link.slug}/"
        self.booking_url = f"{self.availability_url}bookings/"

    def _book(self, start=None):
        start = start or next_weekday_at(10)
        with patch(FREEBUSY_PATH, return_value=[]):
            response = self.client.post(
                self.booking_url,
                {
                    "name": "Grace Hopper",
                    "email": "grace@example.com",
                    "start": start.isoformat().replace("+00:00", "Z"),
                },
                format="json",
            )
        assert response.status_code == status.HTTP_201_CREATED, response.json()
        return start, response.json()

    def test_team_booking_writes_only_the_project_calendar(self):
        start, _body = self._book()
        with in_org(self.org):
            on_project = Event.objects.get(
                calendar=self.project_calendar, start_datetime=start
            )
            assert on_project.metadata["booking_role"] == TEAM_ROLE
            assert not Event.objects.filter(
                calendar=self.primary, start_datetime=start
            ).exists()

    def test_team_booking_does_not_export_to_google(self):
        with patch(FREEBUSY_PATH, return_value=[]), patch(
            "calendars.views.export_event_to_google_task"
        ) as task:
            with self.captureOnCommitCallbacks(execute=True):
                self._book()
        assert not task.delay.called

    def test_busy_on_host_primary_does_not_hide_a_team_slot(self):
        blocked = next_weekday_at(12)
        with in_org(self.org):
            Event.objects.create(
                organization=self.org,
                calendar=self.primary,
                created_by=self.host,
                title="Already busy",
                start_datetime=blocked,
                end_datetime=blocked + timedelta(hours=1),
                timezone="UTC",
            )
        with patch(FREEBUSY_PATH, return_value=[]):
            body = self.client.get(self.availability_url).json()
        starts = [slot["start"] for slot in body["slots"]]
        assert blocked.isoformat().replace("+00:00", "Z") in starts

    def test_busy_on_the_project_calendar_hides_the_team_slot(self):
        blocked = next_weekday_at(12)
        with in_org(self.org):
            Event.objects.create(
                organization=self.org,
                calendar=self.project_calendar,
                created_by=self.host,
                title="Team standup",
                start_datetime=blocked,
                end_datetime=blocked + timedelta(hours=1),
                timezone="UTC",
            )
        with patch(FREEBUSY_PATH, return_value=[]):
            body = self.client.get(self.availability_url).json()
        starts = [slot["start"] for slot in body["slots"]]
        assert blocked.isoformat().replace("+00:00", "Z") not in starts

    def test_guest_cancel_removes_the_booking(self):
        _start, booked = self._book()
        response = self.client.post(
            f"{self.availability_url}cancel/",
            {"token": booked["cancel_token"]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        with in_org(self.org):
            rows = Event.objects.filter(title__startswith="Intro Call")
            assert rows.count() == 1
            assert all(row.is_deleted and row.status == "cancelled" for row in rows)

    def test_feed_accepts_the_primary_event_token(self):
        _start, booked = self._book()
        response = self.client.get(
            f"{self.availability_url}calendar.ics?token={booked['cancel_token']}"
        )
        assert response.status_code == status.HTTP_200_OK
        assert "BEGIN:VEVENT" in response.content.decode()
