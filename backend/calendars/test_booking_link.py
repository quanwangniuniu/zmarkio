"""Tests for the BookingLink model and its availability adapter."""

from datetime import time

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase

from calendars.models import BookingLink, Calendar, CalendarSettings
from calendars.services import rules_from_booking_link, schedule_from_booking_link
from core.models import Organization

User = get_user_model()


class BookingLinkTestBase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Book Org", slug="book-org")
        self.user = User.objects.create_user(
            username="bookuser",
            email="book@test.com",
            password="x",
            organization=self.org,
        )
        self.calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            is_primary=True,
        )

    def _link(self, **overrides):
        kwargs = dict(
            organization=self.org,
            owner=self.user,
            calendar=self.calendar,
            slug="office-hours",
            title="Office Hours",
            duration_minutes=30,
        )
        kwargs.update(overrides)
        return BookingLink.objects.create(**kwargs)


class BookingLinkModelTests(BookingLinkTestBase):
    def test_creates_with_sensible_defaults(self):
        link = self._link()
        assert link.slot_increment_minutes == 15
        assert link.max_advance_days == 60
        assert link.is_active is True
        assert link.availability_windows == []

    def test_slug_is_unique_within_an_organization(self):
        self._link()
        with pytest.raises((ValidationError, IntegrityError)):
            self._link(title="Duplicate")

    def test_same_slug_allowed_in_a_different_organization(self):
        self._link()
        other_org = Organization.objects.create(name="Other", slug="other-org")
        other_user = User.objects.create_user(
            username="otheruser",
            email="other@test.com",
            password="x",
            organization=other_org,
        )
        other_calendar = Calendar.objects.create(
            organization=other_org, owner=other_user, name="Primary", timezone="UTC"
        )
        link = BookingLink.objects.create(
            organization=other_org,
            owner=other_user,
            calendar=other_calendar,
            slug="office-hours",
            title="Office Hours",
        )
        assert link.pk is not None

    def test_calendar_must_belong_to_the_same_organization(self):
        other_org = Organization.objects.create(name="Foreign", slug="foreign-org")
        foreign_calendar = Calendar.objects.create(
            organization=other_org, owner=None, name="Foreign", timezone="UTC"
        )
        with pytest.raises(ValidationError):
            self._link(calendar=foreign_calendar)

    def test_rejects_non_positive_duration(self):
        with pytest.raises(ValidationError):
            self._link(duration_minutes=0)

    def test_rejects_zero_booking_horizon(self):
        with pytest.raises(ValidationError):
            self._link(max_advance_days=0)

    def test_rejects_malformed_availability_windows(self):
        bad_windows = [
            [{"weekday": 0, "start": "09:00"}],                  # missing end
            [{"weekday": 9, "start": "09:00", "end": "17:00"}],  # weekday out of range
            [{"weekday": 0, "start": "9am", "end": "17:00"}],    # unparseable time
            [{"weekday": 0, "start": "17:00", "end": "09:00"}],  # inverted
            ["not-an-object"],
        ]
        for windows in bad_windows:
            with pytest.raises(ValidationError):
                BookingLink(
                    organization=self.org,
                    owner=self.user,
                    calendar=self.calendar,
                    slug=f"bad-{bad_windows.index(windows)}",
                    title="Bad",
                    availability_windows=windows,
                ).full_clean()

    def test_accepts_well_formed_windows(self):
        link = self._link(
            availability_windows=[{"weekday": 0, "start": "09:00", "end": "17:00"}]
        )
        assert link.availability_windows[0]["weekday"] == 0


class BookingLinkAdapterTests(BookingLinkTestBase):
    def test_rules_mirror_the_stored_values(self):
        link = self._link(
            duration_minutes=45,
            slot_increment_minutes=30,
            buffer_before_minutes=10,
            buffer_after_minutes=5,
            min_notice_minutes=120,
            max_advance_days=14,
        )
        rules = rules_from_booking_link(link)
        assert rules.duration_minutes == 45
        assert rules.slot_increment_minutes == 30
        assert rules.buffer_before_minutes == 10
        assert rules.buffer_after_minutes == 5
        assert rules.min_notice_minutes == 120
        assert rules.max_advance_days == 14

    def test_explicit_windows_win(self):
        link = self._link(
            availability_windows=[
                {"weekday": 1, "start": "10:00", "end": "12:00"},
                {"weekday": 3, "start": "14:00", "end": "16:00"},
            ]
        )
        windows = schedule_from_booking_link(link).windows
        assert [(w.weekday, w.start, w.end) for w in windows] == [
            (1, time(10, 0), time(12, 0)),
            (3, time(14, 0), time(16, 0)),
        ]

    def test_falls_back_to_owner_working_hours(self):
        CalendarSettings.objects.create(
            organization=self.org,
            user=self.user,
            working_hours_enabled=True,
            working_hours_start=time(8, 0),
            working_hours_end=time(16, 0),
            working_days=[1, 2, 3],  # Mon–Wed in the Sunday=0 convention
        )
        windows = schedule_from_booking_link(self._link()).windows
        # Sunday=0 → Python Monday=0, so 1,2,3 become 0,1,2.
        assert [w.weekday for w in windows] == [0, 1, 2]
        assert windows[0].start == time(8, 0)
        assert windows[0].end == time(16, 0)

    def test_ignores_working_hours_when_disabled(self):
        CalendarSettings.objects.create(
            organization=self.org,
            user=self.user,
            working_hours_enabled=False,
            working_hours_start=time(8, 0),
            working_hours_end=time(16, 0),
            working_days=[1],
        )
        windows = schedule_from_booking_link(self._link()).windows
        assert [w.weekday for w in windows] == [0, 1, 2, 3, 4]
        assert windows[0].start == time(9, 0)

    def test_defaults_to_weekdays_nine_to_five(self):
        windows = schedule_from_booking_link(self._link()).windows
        assert [w.weekday for w in windows] == [0, 1, 2, 3, 4]
        assert windows[0].start == time(9, 0)
        assert windows[0].end == time(17, 0)

    def test_sunday_converts_to_python_weekday_six(self):
        CalendarSettings.objects.create(
            organization=self.org,
            user=self.user,
            working_hours_enabled=True,
            working_hours_start=time(9, 0),
            working_hours_end=time(17, 0),
            working_days=[0],  # Sunday
        )
        windows = schedule_from_booking_link(self._link()).windows
        assert [w.weekday for w in windows] == [6]

    def test_settings_windows_carry_the_settings_timezone(self):
        """
        Regression guard.

        Working hours from CalendarSettings are wall-clock in the *settings*
        timezone. Applying the link's timezone to them shifted a 09:00-17:00 day
        by the offset between the two zones — a link created in UTC would offer
        an owner in Sydney slots overnight.
        """
        CalendarSettings.objects.create(
            organization=self.org,
            user=self.user,
            timezone="Australia/Sydney",
            working_hours_enabled=True,
            working_hours_start=time(9, 0),
            working_hours_end=time(17, 0),
            working_days=[1, 2, 3, 4, 5],
        )
        schedule = schedule_from_booking_link(self._link(timezone="UTC"))
        assert schedule.timezone == "Australia/Sydney"

    def test_explicit_windows_carry_the_link_timezone(self):
        # The owner wrote these hours against the link's own zone.
        schedule = schedule_from_booking_link(
            self._link(
                timezone="Europe/London",
                availability_windows=[{"weekday": 0, "start": "09:00", "end": "17:00"}],
            )
        )
        assert schedule.timezone == "Europe/London"

    def test_default_windows_fall_back_to_the_link_timezone(self):
        schedule = schedule_from_booking_link(self._link(timezone="Europe/Berlin"))
        assert schedule.timezone == "Europe/Berlin"
        assert [w.weekday for w in schedule.windows] == [0, 1, 2, 3, 4]
