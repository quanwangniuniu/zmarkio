"""Public POST regressions for availability checks and atomic writes."""
from datetime import timedelta
from unittest.mock import patch

import requests
from calendars.models import Event
from calendars.test_public_booking import PublicBookingTestBase, in_org, next_weekday_at
from google_calendar_integration.models import GoogleCalendarConnection


class BookingSafetyTests(PublicBookingTestBase):
    def _submit(self, start):
        return self.client.post(self.booking_url, {
            "name": "Boundary Guest", "email": "boundary@example.com", "start": start.isoformat()
        }, format="json")

    def test_post_rejects_busy_event_in_before_buffer(self):
        start = next_weekday_at(10)
        with in_org(self.org):
            self.link.buffer_before_minutes = 15
            self.link.save(update_fields=["buffer_before_minutes"])
            Event.objects.create(
                organization=self.org, calendar=self.calendar, created_by=self.user,
                title="Previous", start_datetime=start-timedelta(hours=1), end_datetime=start,
                timezone="UTC",
            )
        self.assertEqual(self._submit(start).status_code, 409)
        with in_org(self.org):
            self.assertEqual(Event.objects.count(), 1)

    def test_post_rejects_busy_event_in_after_buffer(self):
        start = next_weekday_at(10)
        with in_org(self.org):
            self.link.buffer_after_minutes = 15
            self.link.save(update_fields=["buffer_after_minutes"])
            Event.objects.create(
                organization=self.org, calendar=self.calendar, created_by=self.user,
                title="Next", start_datetime=start+timedelta(hours=1),
                end_datetime=start+timedelta(hours=2), timezone="UTC",
            )
        self.assertEqual(self._submit(start).status_code, 409)

    def test_google_failure_blocks_post_then_recovery_allows_it(self):
        connection = GoogleCalendarConnection.objects.create(
            user=self.user, is_active=True, primary_calendar_id="primary"
        )
        connection.set_access_token("test-token")
        connection.save()
        start = next_weekday_at(10)
        with patch("google_calendar_integration.services.run_google_calendar_api", side_effect=requests.Timeout()):
            # Browsing retains its platform-only fallback; confirming requires
            # a successful check when a Google connection is configured.
            self.assertEqual(self.client.get(self.availability_url).status_code, 200)
            self.assertEqual(self._submit(start).status_code, 503)
        with in_org(self.org):
            self.assertEqual(Event.objects.count(), 0)
        with patch("google_calendar_integration.services.run_google_calendar_api",
                   return_value={"calendars": {"primary": {"busy": []}}}):
            self.assertEqual(self._submit(start).status_code, 201)
        with in_org(self.org):
            self.assertEqual(Event.objects.count(), 1)

    def test_failed_second_write_rolls_back_host_event(self):
        from calendars.booking_write import _write_event
        from django.contrib.auth import get_user_model
        guest = get_user_model().objects.create_user(
            username="atomic-guest", email="boundary@example.com", organization=self.org
        )
        self.client.force_authenticate(user=guest)
        # The second call writes the registered guest's personal copy.
        calls = 0
        def write(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("guest copy failed")
            return _write_event(**kwargs)
        with patch("calendars.booking_write._write_event", side_effect=write):
            with self.assertRaises(RuntimeError):
                self._submit(next_weekday_at(10))
        with in_org(self.org):
            self.assertEqual(Event.objects.count(), 0)

    def test_post_rejects_recurring_event_extending_past_slot(self):
        from calendars.models import RecurrenceRule
        start = next_weekday_at(10)
        with in_org(self.org):
            rule = RecurrenceRule.objects.create(organization=self.org, frequency="DAILY")
            Event.objects.create(organization=self.org, calendar=self.calendar, created_by=self.user,
                title="Recurring busy", start_datetime=start-timedelta(days=1),
                end_datetime=start-timedelta(days=1)+timedelta(hours=2), is_recurring=True, recurrence_rule=rule)
        self.assertEqual(self._submit(start).status_code, 409)
