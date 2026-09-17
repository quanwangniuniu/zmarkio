"""
Tests that a booking actually reaches Google Calendar.

The public booking tests assert the export task is *queued*; these assert the
export itself builds the right Google API call. Everything below the HTTP
boundary is exercised — only `requests` is mocked.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from calendars.models import Calendar, Event
from core.models import Organization
from google_calendar_integration.models import GoogleCalendarConnection
from google_calendar_integration.services import export_event_to_google

User = get_user_model()

START = datetime(2027, 3, 2, 10, 0, tzinfo=dt_timezone.utc)


class BookingExportToGoogleTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Export Org", slug="export-org")
        self.user = User.objects.create_user(
            username="exportowner",
            email="owner@export.com",
            password="x",
            organization=self.org,
        )
        self.primary = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            is_primary=True,
        )
        self.connection = GoogleCalendarConnection.objects.create(
            user=self.user,
            is_active=True,
            primary_calendar_id="owner@export.com",
            # Well in the future so the export doesn't try to refresh the token.
            token_expiry=timezone.now() + timedelta(days=1),
        )
        self.connection.set_access_token("token-abc")
        self.connection.save()

    def _booking_event(self, calendar=None):
        """An event shaped like one the public booking endpoint creates."""
        return Event.objects.create(
            organization=self.org,
            calendar=calendar or self.primary,
            created_by=self.user,
            title="Intro Call with Grace Hopper",
            description="Looking forward to it.",
            start_datetime=START,
            end_datetime=START + timedelta(minutes=60),
            timezone="UTC",
            status="confirmed",
        )

    def test_booking_is_sent_to_google_with_the_right_payload(self):
        event = self._booking_event()

        response = MagicMock()
        response.json.return_value = {"id": "google-event-1", "etag": '"abc"'}
        response.raise_for_status.return_value = None

        with patch(
            "google_calendar_integration.services.requests.post", return_value=response
        ) as post:
            export_event_to_google(event)

        post.assert_called_once()
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        headers = post.call_args.kwargs["headers"]

        assert url.endswith("/calendars/owner%40export.com/events")
        assert headers["Authorization"] == "Bearer token-abc"
        assert body["summary"] == "Intro Call with Grace Hopper"
        assert body["start"]["dateTime"] == START.isoformat()
        assert body["end"]["dateTime"] == (START + timedelta(minutes=60)).isoformat()
        assert body["start"]["timeZone"] == "UTC"

    def test_google_event_id_is_recorded_for_later_reconciliation(self):
        event = self._booking_event()

        response = MagicMock()
        response.json.return_value = {"id": "google-event-1", "etag": '"abc"'}
        response.raise_for_status.return_value = None

        with patch(
            "google_calendar_integration.services.requests.post", return_value=response
        ):
            export_event_to_google(event)

        event.refresh_from_db()
        assert "google-event-1" in str(event.metadata)

    def test_export_is_skipped_when_the_link_calendar_is_not_primary(self):
        """
        Regression guard for a real gap.

        should_export_event_to_google() only exports events on the owner's
        *primary* calendar. A booking link may point at any calendar the owner
        owns, so a link on a secondary calendar produces a local event that
        never reaches Google — silently, because the export only logs.
        """
        secondary = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Secondary",
            timezone="UTC",
            is_primary=False,
        )
        event = self._booking_event(calendar=secondary)

        with patch("google_calendar_integration.services.requests.post") as post:
            export_event_to_google(event)

        post.assert_not_called()

    def test_export_is_skipped_without_a_google_connection(self):
        self.connection.delete()
        event = self._booking_event()

        with patch("google_calendar_integration.services.requests.post") as post:
            export_event_to_google(event)

        post.assert_not_called()

    def test_retry_after_lost_insert_response_updates_same_google_event(self):
        import requests
        from google_calendar_integration.tasks import export_event_to_google_task
        event = self._booking_event()
        remote = {}
        submitted_ids = []

        def insert(*args, **kwargs):
            payload = kwargs["json"]
            submitted_ids.append(payload["id"])
            if payload["id"] not in remote:
                remote[payload["id"]] = payload
                raise requests.Timeout("Google accepted the event; response was lost")
            response = MagicMock(status_code=409)
            return response

        def update(url, **kwargs):
            event_id = url.rsplit("/", 1)[-1]
            assert event_id in remote
            remote[event_id].update(kwargs["json"])
            response = MagicMock(status_code=200)
            response.json.return_value = {"id": event_id, "etag": "recovered"}
            return response

        with patch("google_calendar_integration.services.requests.post", side_effect=insert), patch(
            "google_calendar_integration.services.requests.patch", side_effect=update
        ):
            result = export_event_to_google_task.apply(args=(str(event.pk),), throw=False)
        assert result.successful()
        assert len(submitted_ids) == 2 and submitted_ids[0] == submitted_ids[1]
        assert len(remote) == 1
        event.refresh_from_db()
        self.connection.refresh_from_db()
        assert event.metadata["google_calendar_event_id"] == submitted_ids[0]
        assert self.connection.last_error_message is None

    def test_retry_after_server_failure_exports_existing_local_booking(self):
        import requests
        from google_calendar_integration.tasks import export_event_to_google_task
        event = self._booking_event()
        failed = requests.Response()
        failed.status_code = 503
        error = requests.HTTPError(response=failed)
        success = MagicMock(status_code=200)
        success.json.return_value = {"id": "recovered-id", "etag": "etag"}
        with patch("google_calendar_integration.services.requests.post", side_effect=[error, success]) as post:
            result = export_event_to_google_task.apply(args=(str(event.pk),), throw=False)
        assert result.successful()
        assert post.call_count == 2
        event.refresh_from_db()
        assert event.status == "confirmed" and not event.is_deleted
        assert event.metadata["google_calendar_event_id"] == "recovered-id"

    def test_permanent_google_error_fails_without_retries(self):
        import requests
        from google_calendar_integration.tasks import export_event_to_google_task
        event = self._booking_event()
        failed = requests.Response()
        failed.status_code = 403
        failed._content = b'{"error":{"errors":[{"reason":"forbidden"}]}}'
        with patch("google_calendar_integration.services.requests.post", side_effect=requests.HTTPError(response=failed)) as post:
            result = export_event_to_google_task.apply(args=(str(event.pk),), throw=False)
        assert result.failed()
        assert post.call_count == 1
        self.connection.refresh_from_db()
        assert self.connection.needs_reconnect

    def test_transient_failure_stops_after_retry_limit(self):
        import requests
        from google_calendar_integration.tasks import export_event_to_google_task
        event = self._booking_event()
        with patch("google_calendar_integration.services.requests.post", side_effect=requests.Timeout()) as post:
            result = export_event_to_google_task.apply(args=(str(event.pk),), throw=False)
        assert result.failed()
        assert post.call_count == 6  # original attempt plus five retries
        self.connection.refresh_from_db()
        assert self.connection.last_error_message
        assert not self.connection.needs_reconnect

    def test_cancellation_removes_remote_insert_even_if_response_was_lost(self):
        import requests
        event = self._booking_event()
        with patch("google_calendar_integration.services.requests.post", side_effect=requests.Timeout()) as post:
            with self.assertRaises(requests.Timeout):
                export_event_to_google(event)
        inserted_id = post.call_args.kwargs["json"]["id"]
        event.is_deleted = True
        event.save(update_fields=["is_deleted"])
        response = MagicMock(status_code=204)
        with patch("google_calendar_integration.services.requests.delete", return_value=response) as delete:
            export_event_to_google(event)
        assert delete.call_args.args[0].endswith('/' + inserted_id)
