"""Tests for merging in-app and Google availability for booking links."""

from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from calendars.availability import BookingRules, WeeklyWindow
from calendars.models import Calendar, Event
from core.models import Organization
from google_calendar_integration.models import GoogleCalendarConnection
from google_calendar_integration.services import (
    get_merged_availability,
    get_merged_busy_intervals,
    is_slot_still_available,
)

User = get_user_model()

MERGE_PATH = "google_calendar_integration.services.fetch_google_busy_intervals"


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


# 2026-09-01 is a Tuesday.
DAY_START = utc(2026, 9, 1)
DAY_END = utc(2026, 9, 2)
WEEKDAYS_9_TO_5 = [
    WeeklyWindow(weekday=d, start=time(9, 0), end=time(17, 0)) for d in range(5)
]
HOURLY = BookingRules(duration_minutes=60, slot_increment_minutes=60)


class AvailabilityMergeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Merge Org", slug="merge-org")
        self.user = User.objects.create_user(
            username="mergeuser",
            email="merge@test.com",
            password="x",
            organization=self.org,
        )
        self.calendar = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            visibility="public",
            is_primary=True,
        )
        self.connection = GoogleCalendarConnection.objects.create(
            user=self.user, is_active=True, primary_calendar_id="primary"
        )

    def _add_event(self, start, end, title="Busy"):
        return Event.objects.create(
            organization=self.org,
            calendar=self.calendar,
            created_by=self.user,
            title=title,
            start_datetime=start,
            end_datetime=end,
            timezone="UTC",
        )

    def _availability(self, **overrides):
        kwargs = dict(
            calendars=[self.calendar],
            google_connection=self.connection,
            rules=HOURLY,
            windows=WEEKDAYS_9_TO_5,
            tz_name="UTC",
            range_start=DAY_START,
            range_end=DAY_END,
            now=utc(2026, 8, 31, 12),
        )
        kwargs.update(overrides)
        return get_merged_availability(**kwargs)

    # ── busy merge ───────────────────────────────────────────────────────

    def test_combines_platform_and_google_busy(self):
        self._add_event(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))
        with patch(
            MERGE_PATH,
            return_value=[(utc(2026, 9, 1, 15), utc(2026, 9, 1, 16))],
        ):
            busy = get_merged_busy_intervals(
                [self.calendar], self.connection, DAY_START, DAY_END
            )
        assert busy == [
            (utc(2026, 9, 1, 12), utc(2026, 9, 1, 13)),
            (utc(2026, 9, 1, 15), utc(2026, 9, 1, 16)),
        ]

    def test_overlapping_platform_and_google_busy_collapse(self):
        self._add_event(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))
        with patch(
            MERGE_PATH,
            return_value=[(utc(2026, 9, 1, 12, 30), utc(2026, 9, 1, 14))],
        ):
            busy = get_merged_busy_intervals(
                [self.calendar], self.connection, DAY_START, DAY_END
            )
        assert busy == [(utc(2026, 9, 1, 12), utc(2026, 9, 1, 14))]

    def test_no_google_connection_uses_platform_only(self):
        self._add_event(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))
        busy = get_merged_busy_intervals(
            [self.calendar], None, DAY_START, DAY_END
        )
        assert busy == [(utc(2026, 9, 1, 12), utc(2026, 9, 1, 13))]

    # ── availability ─────────────────────────────────────────────────────

    def test_google_busy_removes_a_slot_the_platform_does_not_know_about(self):
        with patch(
            MERGE_PATH,
            return_value=[(utc(2026, 9, 1, 15), utc(2026, 9, 1, 16))],
        ):
            slots = self._availability()
        starts = [s for s, _ in slots]
        assert utc(2026, 9, 1, 15) not in starts
        assert len(slots) == 7  # 09:00–17:00 minus one hour

    def test_both_sources_remove_their_own_slots(self):
        self._add_event(utc(2026, 9, 1, 10), utc(2026, 9, 1, 11))
        with patch(
            MERGE_PATH,
            return_value=[(utc(2026, 9, 1, 15), utc(2026, 9, 1, 16))],
        ):
            slots = self._availability()
        starts = [s for s, _ in slots]
        assert utc(2026, 9, 1, 10) not in starts
        assert utc(2026, 9, 1, 15) not in starts
        assert len(slots) == 6

    def test_availability_survives_google_being_unreachable(self):
        # fetch_google_busy_intervals swallows transport errors and returns [],
        # so the page still offers the platform's view of availability.
        self._add_event(utc(2026, 9, 1, 10), utc(2026, 9, 1, 11))
        with patch(MERGE_PATH, return_value=[]):
            slots = self._availability()
        assert len(slots) == 7
        assert utc(2026, 9, 1, 10) not in [s for s, _ in slots]

    # ── booking-time re-check ────────────────────────────────────────────

    def test_slot_is_available_when_nothing_conflicts(self):
        with patch(MERGE_PATH, return_value=[]):
            assert is_slot_still_available(
                calendars=[self.calendar],
                google_connection=self.connection,
                rules=HOURLY,
                windows=WEEKDAYS_9_TO_5,
                tz_name="UTC",
                slot_start=utc(2026, 9, 1, 10),
                now=utc(2026, 8, 31, 12),
            )

    def test_slot_taken_on_google_after_the_page_loaded_is_rejected(self):
        # The race the check exists for: the prospect saw 10:00 as free, then
        # the owner accepted a Google invite for it before submitting.
        with patch(
            MERGE_PATH,
            return_value=[(utc(2026, 9, 1, 10), utc(2026, 9, 1, 11))],
        ):
            assert not is_slot_still_available(
                calendars=[self.calendar],
                google_connection=self.connection,
                rules=HOURLY,
                windows=WEEKDAYS_9_TO_5,
                tz_name="UTC",
                slot_start=utc(2026, 9, 1, 10),
                now=utc(2026, 8, 31, 12),
            )

    def test_slot_outside_working_hours_is_rejected(self):
        with patch(MERGE_PATH, return_value=[]):
            assert not is_slot_still_available(
                calendars=[self.calendar],
                google_connection=self.connection,
                rules=HOURLY,
                windows=WEEKDAYS_9_TO_5,
                tz_name="UTC",
                slot_start=utc(2026, 9, 1, 20),
                now=utc(2026, 8, 31, 12),
            )

    def test_off_grid_start_time_is_rejected(self):
        # A hand-crafted request for 10:07 must not be accepted just because
        # the underlying time happens to be free.
        with patch(MERGE_PATH, return_value=[]):
            assert not is_slot_still_available(
                calendars=[self.calendar],
                google_connection=self.connection,
                rules=HOURLY,
                windows=WEEKDAYS_9_TO_5,
                tz_name="UTC",
                slot_start=utc(2026, 9, 1, 10, 7),
                now=utc(2026, 8, 31, 12),
            )

    def test_slot_inside_the_notice_period_is_rejected(self):
        rules = BookingRules(
            duration_minutes=60, slot_increment_minutes=60, min_notice_minutes=240
        )
        with patch(MERGE_PATH, return_value=[]):
            assert not is_slot_still_available(
                calendars=[self.calendar],
                google_connection=self.connection,
                rules=rules,
                windows=WEEKDAYS_9_TO_5,
                tz_name="UTC",
                slot_start=utc(2026, 9, 1, 10),
                now=utc(2026, 9, 1, 9),  # only one hour's notice
            )

    def test_soft_deleted_events_do_not_block(self):
        event = self._add_event(utc(2026, 9, 1, 10), utc(2026, 9, 1, 11))
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        with patch(MERGE_PATH, return_value=[]):
            slots = self._availability()
        assert utc(2026, 9, 1, 10) in [s for s, _ in slots]
        assert len(slots) == 8

    def test_submit_respects_busy_before_the_query_range(self):
        self._add_event(utc(2026, 9, 1, 9), utc(2026, 9, 1, 10))
        rules = BookingRules(duration_minutes=60, slot_increment_minutes=15, buffer_before_minutes=15)
        for minute, expected in ((0, False), (15, True)):
            assert is_slot_still_available(
                calendars=[self.calendar], google_connection=None, rules=rules,
                windows=WEEKDAYS_9_TO_5, tz_name="UTC",
                slot_start=utc(2026, 9, 1, 10, minute), now=utc(2026, 8, 31, 12),
            ) is expected

    def test_submit_respects_busy_after_the_query_range(self):
        self._add_event(utc(2026, 9, 1, 11), utc(2026, 9, 1, 12))
        rules = BookingRules(duration_minutes=60, slot_increment_minutes=15, buffer_after_minutes=15)
        for hour, minute, expected in ((10, 0, False), (9, 45, True)):
            assert is_slot_still_available(
                calendars=[self.calendar], google_connection=None, rules=rules,
                windows=WEEKDAYS_9_TO_5, tz_name="UTC",
                slot_start=utc(2026, 9, 1, hour, minute), now=utc(2026, 8, 31, 12),
            ) is expected

    def test_google_query_includes_both_buffers_without_expanding_slots(self):
        rules = BookingRules(duration_minutes=60, slot_increment_minutes=15,
                             buffer_before_minutes=15, buffer_after_minutes=30)
        with patch(MERGE_PATH, return_value=[]) as fetch:
            slots = self._availability(rules=rules, range_start=utc(2026, 9, 1, 10),
                                       range_end=utc(2026, 9, 1, 11))
        assert slots == [(utc(2026, 9, 1, 10), utc(2026, 9, 1, 11))]
        assert fetch.call_args.args[1:] == (utc(2026, 9, 1, 9, 45), utc(2026, 9, 1, 11, 30))
