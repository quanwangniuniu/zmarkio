"""Recurring busy time must never disappear at a booking query boundary."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from django.test import TestCase
from calendars.models import RecurrenceRule, RecurrenceException
from calendars.services import _recurring_busy_intervals
from types import SimpleNamespace
from unittest.mock import patch


class RecurringBusyTests(TestCase):
    def intervals(self, start, end, *, frequency="DAILY", by_day=None, until=None, count=None,
                  zone="UTC", seed=None, exception_dates=None):
        event = SimpleNamespace(
            recurrence_rule=RecurrenceRule(frequency=frequency, interval=1,
                by_day=by_day or [], until=until, count=count, exception_dates=exception_dates or []),
            start_datetime=seed or start, end_datetime=(seed or start)+timedelta(hours=2),
            timezone=zone,
        )
        with patch("calendars.services.RecurrenceException.objects.filter") as exceptions:
            exceptions.return_value.select_related.return_value = []
            return _recurring_busy_intervals(event, start, end)

    def test_occurrence_extending_beyond_query_end_is_busy(self):
        start = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
        self.assertEqual(self.intervals(start, start+timedelta(minutes=30)), [(start, start+timedelta(hours=2))])

    def test_monthly_and_yearly_rules_are_considered(self):
        start = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
        for frequency, seed in [("MONTHLY", start.replace(month=8)), ("YEARLY", start.replace(year=2025))]:
            self.assertEqual(len(self.intervals(start, start+timedelta(minutes=30), frequency=frequency, seed=seed)), 1)

    def test_weekly_byday_checks_all_selected_weekdays(self):
        start = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
        self.assertEqual(len(self.intervals(start, start+timedelta(hours=1), frequency="WEEKLY", by_day=["MO", "TH"], seed=start-timedelta(days=3))), 1)

    def test_local_time_survives_daylight_saving_transition(self):
        zone = ZoneInfo("Australia/Sydney")
        seed = datetime(2026, 10, 3, 9, tzinfo=zone)
        start = datetime(2026, 10, 4, 9, tzinfo=zone).astimezone(timezone.utc)
        self.assertEqual(self.intervals(start, start+timedelta(hours=1), seed=seed, zone="Australia/Sydney")[0][0], start)

    def test_count_until_and_excluded_dates_are_honoured(self):
        start = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
        end = start+timedelta(hours=1)
        self.assertEqual(self.intervals(start, end, seed=start-timedelta(days=1), count=1), [])
        self.assertEqual(self.intervals(start, end, seed=start-timedelta(days=1), until=start), [])
        self.assertEqual(self.intervals(start, end, exception_dates=[start.isoformat()]), [])

    def test_second_dst_hour_keeps_slot_increment_alignment(self):
        from calendars.availability import _align_to_increment
        zone = ZoneInfo("America/New_York")
        moment = datetime(2026, 11, 1, 1, 7, tzinfo=zone, fold=1).astimezone(timezone.utc)
        expected = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1).astimezone(timezone.utc)
        self.assertEqual(_align_to_increment(moment, zone, timedelta(minutes=15)), expected)
