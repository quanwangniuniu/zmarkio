"""Tests for the Google free/busy lookup used by booking links."""

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Organization
from google_calendar_integration.models import GoogleCalendarConnection
from google_calendar_integration.services import fetch_google_busy_intervals

User = get_user_model()

TIME_MIN = datetime(2026, 9, 1, tzinfo=dt_timezone.utc)
TIME_MAX = datetime(2026, 9, 2, tzinfo=dt_timezone.utc)


class FetchGoogleBusyIntervalsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="FB Org", slug="fb-org")
        self.user = User.objects.create_user(
            username="fbuser", email="fb@test.com", password="x", organization=self.org
        )
        self.connection = GoogleCalendarConnection.objects.create(
            user=self.user, primary_calendar_id="primary", is_active=True
        )
        self.connection.set_access_token("token-123")
        self.connection.save()

    def _run(self, payload):
        with patch(
            "google_calendar_integration.services.run_google_calendar_api",
            return_value=payload,
        ):
            return fetch_google_busy_intervals(self.connection, TIME_MIN, TIME_MAX)

    def test_parses_busy_periods_as_utc(self):
        intervals = self._run({
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "2026-09-01T12:00:00Z", "end": "2026-09-01T13:00:00Z"},
                    ]
                }
            }
        })
        assert intervals == [
            (
                datetime(2026, 9, 1, 12, tzinfo=dt_timezone.utc),
                datetime(2026, 9, 1, 13, tzinfo=dt_timezone.utc),
            )
        ]

    def test_normalises_offset_times_to_utc(self):
        intervals = self._run({
            "calendars": {
                "primary": {
                    "busy": [
                        {
                            "start": "2026-09-01T09:00:00+02:00",
                            "end": "2026-09-01T10:00:00+02:00",
                        },
                    ]
                }
            }
        })
        assert intervals[0][0] == datetime(2026, 9, 1, 7, tzinfo=dt_timezone.utc)

    def test_collects_across_multiple_calendars(self):
        intervals = self._run({
            "calendars": {
                "primary": {
                    "busy": [{"start": "2026-09-01T12:00:00Z", "end": "2026-09-01T13:00:00Z"}]
                },
                "team@example.com": {
                    "busy": [{"start": "2026-09-01T15:00:00Z", "end": "2026-09-01T16:00:00Z"}]
                },
            }
        })
        assert len(intervals) == 2

    def test_skips_calendars_google_reports_errors_for(self):
        # Google reports per-calendar problems inline instead of failing the call.
        intervals = self._run({
            "calendars": {
                "primary": {
                    "busy": [{"start": "2026-09-01T12:00:00Z", "end": "2026-09-01T13:00:00Z"}]
                },
                "gone@example.com": {"errors": [{"domain": "global", "reason": "notFound"}]},
            }
        })
        assert len(intervals) == 1

    def test_ignores_malformed_and_zero_length_periods(self):
        intervals = self._run({
            "calendars": {
                "primary": {
                    "busy": [
                        {"start": "nonsense", "end": "2026-09-01T13:00:00Z"},
                        {"start": "2026-09-01T12:00:00Z"},
                        {"start": "2026-09-01T14:00:00Z", "end": "2026-09-01T14:00:00Z"},
                    ]
                }
            }
        })
        assert intervals == []

    def test_returns_empty_when_google_call_fails(self):
        # A booking page must still render in-app availability if Google is down.
        with patch(
            "google_calendar_integration.services.run_google_calendar_api",
            side_effect=requests.ConnectionError("boom"),
        ):
            assert fetch_google_busy_intervals(self.connection, TIME_MIN, TIME_MAX) == []

    def test_skips_connections_that_cannot_be_used(self):
        for field, value in (("is_active", False), ("needs_reconnect", True)):
            setattr(self.connection, field, value)
            self.connection.save()
            assert fetch_google_busy_intervals(self.connection, TIME_MIN, TIME_MAX) == []
            setattr(self.connection, field, GoogleCalendarConnection._meta.get_field(field).default)
            self.connection.save()

    def test_skips_when_no_access_token(self):
        self.connection.set_access_token(None)
        self.connection.save()
        assert fetch_google_busy_intervals(self.connection, TIME_MIN, TIME_MAX) == []

    def test_no_connection_is_not_an_error(self):
        assert fetch_google_busy_intervals(None, TIME_MIN, TIME_MAX) == []

    def test_strict_check_rejects_transport_and_inline_failures(self):
        from google_calendar_integration.services import GoogleAvailabilityUnavailable
        for outcome in (requests.Timeout(), {"calendars": {"primary": {"errors": [{"reason": "internalError"}]}}}, {"calendars": {}}):
            with patch("google_calendar_integration.services.run_google_calendar_api") as call:
                if isinstance(outcome, Exception):
                    call.side_effect = outcome
                else:
                    call.return_value = outcome
                with self.assertRaises(GoogleAvailabilityUnavailable):
                    fetch_google_busy_intervals(self.connection, TIME_MIN, TIME_MAX, strict=True)

    def test_strict_check_rejects_expired_authorization_but_allows_no_connection(self):
        from google_calendar_integration.services import GoogleAvailabilityUnavailable
        self.connection.needs_reconnect = True
        with self.assertRaises(GoogleAvailabilityUnavailable):
            fetch_google_busy_intervals(self.connection, TIME_MIN, TIME_MAX, strict=True)
        assert fetch_google_busy_intervals(None, TIME_MIN, TIME_MAX, strict=True) == []
