"""Tests for Celery tasks in google_calendar_integration."""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from calendars.models import Calendar, Event
from core.models import Organization
from google_calendar_integration.models import GoogleCalendarConnection
from google_calendar_integration.tasks import (
    export_event_to_google_task,
    import_for_connection_task,
    sync_all_google_calendar_imports,
)

User = get_user_model()


class ImportForConnectionTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Task Org", slug="task-org")
        self.user = User.objects.create_user(
            username="taskuser",
            email="task@test.com",
            password="x",
            organization=self.org,
        )

    @patch("google_calendar_integration.tasks.import_events_for_connection")
    def test_skips_when_connection_missing(self, mock_import):
        r = import_for_connection_task.apply(args=(999_999,))
        self.assertTrue(r.successful())
        mock_import.assert_not_called()

    @patch("google_calendar_integration.tasks.import_events_for_connection")
    def test_calls_import_when_connection_active(self, mock_import):
        conn = GoogleCalendarConnection.objects.create(user=self.user, is_active=True)
        r = import_for_connection_task.apply(args=(conn.pk,))
        self.assertTrue(r.successful())
        mock_import.assert_called_once()
        self.assertEqual(mock_import.call_args[0][0].pk, conn.pk)

    @patch("google_calendar_integration.tasks.import_events_for_connection", side_effect=RuntimeError("boom"))
    @patch("google_calendar_integration.tasks.logger")
    def test_logs_on_import_exception(self, mock_logger, _mock_import):
        conn = GoogleCalendarConnection.objects.create(user=self.user, is_active=True)
        r = import_for_connection_task.apply(args=(conn.pk,))
        self.assertTrue(r.successful())
        mock_logger.exception.assert_called()


class SyncAllImportsTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Sync Org", slug="sync-org")
        self.user = User.objects.create_user(
            username="syncuser",
            email="sync@test.com",
            password="x",
            organization=self.org,
        )

    @patch("google_calendar_integration.tasks.import_events_for_connection")
    def test_iterates_active_connections(self, mock_import):
        c1 = GoogleCalendarConnection.objects.create(user=self.user, is_active=True)
        u2 = User.objects.create_user(
            username="u2",
            email="u2@test.com",
            password="x",
            organization=self.org,
        )
        c2 = GoogleCalendarConnection.objects.create(user=u2, is_active=True)
        u3 = User.objects.create_user(
            username="u3",
            email="u3@test.com",
            password="x",
            organization=self.org,
        )
        GoogleCalendarConnection.objects.create(user=u3, is_active=False)
        r = sync_all_google_calendar_imports.apply()
        self.assertTrue(r.successful())
        self.assertEqual(mock_import.call_count, 2)
        called_ids = {mock_import.call_args_list[i][0][0].pk for i in range(2)}
        self.assertEqual(called_ids, {c1.pk, c2.pk})

    @patch("google_calendar_integration.tasks.import_events_for_connection", side_effect=[None, RuntimeError("x")])
    @patch("google_calendar_integration.tasks.logger")
    def test_sync_all_logs_per_connection_exception(self, mock_logger, _mock_import):
        GoogleCalendarConnection.objects.create(user=self.user, is_active=True)
        u2 = User.objects.create_user(
            username="u2b",
            email="u2b@test.com",
            password="x",
            organization=self.org,
        )
        GoogleCalendarConnection.objects.create(user=u2, is_active=True)
        r = sync_all_google_calendar_imports.apply()
        self.assertTrue(r.successful())
        self.assertGreaterEqual(mock_logger.exception.call_count, 1)


class ExportEventToGoogleTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Ex Org", slug="ex-org")
        self.user = User.objects.create_user(
            username="exuser",
            email="ex@test.com",
            password="x",
            organization=self.org,
        )
        self.cal = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            visibility="public",
            is_primary=True,
        )

    @patch("google_calendar_integration.services.export_event_to_google")
    def test_noop_when_event_missing(self, mock_export):
        missing_id = str(uuid.UUID(int=0))
        r = export_event_to_google_task.apply(args=(missing_id,))
        self.assertTrue(r.successful())
        mock_export.assert_not_called()

    @patch("google_calendar_integration.services.export_event_to_google")
    def test_calls_export_for_existing_event(self, mock_export):
        now = timezone.now()
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="T",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            timezone="UTC",
        )
        r = export_event_to_google_task.apply(args=(str(ev.pk),))
        self.assertTrue(r.successful())
        mock_export.assert_called_once()
        self.assertEqual(mock_export.call_args[0][0].pk, ev.pk)

    @patch("google_calendar_integration.services.export_event_to_google", side_effect=RuntimeError("x"))
    @patch("google_calendar_integration.tasks.logger")
    def test_logs_on_export_exception(self, mock_logger, _mock_export):
        now = timezone.now()
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="T",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            timezone="UTC",
        )
        r = export_event_to_google_task.apply(args=(str(ev.pk),))
        self.assertTrue(r.failed())
        mock_logger.exception.assert_called()
