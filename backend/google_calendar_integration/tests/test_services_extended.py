"""
Higher-coverage tests for google_calendar_integration.services (OAuth, HTTP paths, export/import).

Uses mocks for `requests` / `refresh_google_tokens` so tests stay offline and fast.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from calendars.models import Calendar, Event
from core.models import Organization
from google_calendar_integration.constants import (
    METADATA_GOOGLE_EVENT_ID_KEY,
    METADATA_SOURCE_GOOGLE_CALENDAR,
    METADATA_SOURCE_KEY,
)
from google_calendar_integration.models import GoogleCalendarConnection
from google_calendar_integration.services import (
    disconnect_user_calendar,
    ensure_import_calendar,
    exchange_code_for_token,
    export_event_to_google,
    export_primary_calendar_events_to_google,
    fetch_google_email,
    fetch_primary_calendar_id,
    get_access_token_for_api,
    import_events_for_connection,
    platform_event_to_google_body,
    promote_google_import_calendar_to_primary,
    run_google_calendar_api,
    should_export_event_to_google,
)

User = get_user_model()


def _mock_http_json(data, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    r.raise_for_status = MagicMock()
    return r


class ExchangeCodeForTokenTests(TestCase):
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="cid",
        GOOGLE_OAUTH_CLIENT_SECRET="sec",
        GOOGLE_CALENDAR_OAUTH_REDIRECT_URI="http://localhost/cb",
    )
    @patch("google_calendar_integration.services.requests.post")
    def test_exchange_code_for_token_sets_token_expiry(self, mock_post):
        mock_post.return_value = _mock_http_json(
            {"access_token": "a", "refresh_token": "r", "expires_in": 7200}
        )
        out = exchange_code_for_token("auth-code")
        self.assertEqual(out["access_token"], "a")
        self.assertIn("token_expiry", out)
        mock_post.assert_called_once()


class FetchGoogleApiTests(TestCase):
    @patch("google_calendar_integration.services.requests.get")
    def test_fetch_google_email(self, mock_get):
        mock_get.return_value = _mock_http_json({"email": "u@gmail.com"})
        self.assertEqual(fetch_google_email("tok"), "u@gmail.com")

    @patch("google_calendar_integration.services.requests.get")
    def test_fetch_primary_prefers_primary_flag(self, mock_get):
        mock_get.return_value = _mock_http_json(
            {
                "items": [
                    {"id": "other@group.calendar", "primary": False},
                    {"id": "foo@cal", "primary": True},
                ]
            }
        )
        self.assertEqual(fetch_primary_calendar_id("tok"), "foo@cal")

    @patch("google_calendar_integration.services.requests.get")
    def test_fetch_primary_falls_back_to_primary_id_string(self, mock_get):
        mock_get.return_value = _mock_http_json(
            {"items": [{"id": "primary", "primary": False}]}
        )
        self.assertEqual(fetch_primary_calendar_id("tok"), "primary")

    @patch("google_calendar_integration.services.requests.get")
    def test_fetch_primary_raises_when_none(self, mock_get):
        mock_get.return_value = _mock_http_json({"items": []})
        with self.assertRaises(ValueError):
            fetch_primary_calendar_id("tok")


class GetAccessTokenForApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="T", slug="t-gat")
        self.user = User.objects.create_user(
            username="gat", email="gat@test.com", password="x", organization=self.org
        )
        self.conn = GoogleCalendarConnection.objects.create(user=self.user, is_active=True)
        self.conn.set_access_token("at")
        self.conn.save()

    def test_raises_when_no_access_token(self):
        c = GoogleCalendarConnection.objects.create(
            user=User.objects.create_user(
                username="gat2", email="gat2@test.com", password="x", organization=self.org
            ),
            is_active=True,
        )
        with self.assertRaises(ValueError):
            get_access_token_for_api(c)

    def test_returns_when_not_expired(self):
        self.conn.token_expiry = timezone.now() + timedelta(hours=1)
        self.conn.save()
        self.assertEqual(get_access_token_for_api(self.conn), "at")

    def test_returns_cached_when_expired_but_no_refresh(self):
        self.conn.token_expiry = timezone.now() - timedelta(hours=1)
        self.conn.set_refresh_token(None)
        self.conn.save()
        self.assertEqual(get_access_token_for_api(self.conn), "at")

    @patch("google_calendar_integration.services.refresh_google_tokens")
    def test_refreshes_when_expired_and_refresh_present(self, mock_refresh):
        self.conn.set_refresh_token("rt")
        self.conn.token_expiry = timezone.now() - timedelta(hours=1)
        self.conn.save()
        mock_refresh.return_value = {
            "access_token": "new",
            "token_expiry": timezone.now() + timedelta(hours=1),
        }
        out = get_access_token_for_api(self.conn)
        self.assertEqual(out, "new")
        mock_refresh.assert_called_once_with("rt")

    @patch("google_calendar_integration.services.refresh_google_tokens")
    def test_persist_sets_refresh_when_google_returns_new_refresh(self, mock_refresh):
        self.conn.set_refresh_token("rt")
        self.conn.token_expiry = timezone.now() - timedelta(hours=1)
        self.conn.save()
        mock_refresh.return_value = {
            "access_token": "n",
            "refresh_token": "rt2",
            "token_expiry": timezone.now() + timedelta(hours=1),
        }
        get_access_token_for_api(self.conn)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.get_refresh_token(), "rt2")


class RunGoogleCalendarApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="R", slug="t-rga")
        self.user = User.objects.create_user(
            username="rga", email="rga@test.com", password="x", organization=self.org
        )
        self.conn = GoogleCalendarConnection.objects.create(user=self.user, is_active=True)
        self.conn.set_access_token("at")
        self.conn.set_refresh_token("rt")
        self.conn.token_expiry = timezone.now() + timedelta(hours=1)
        self.conn.save()

    def test_success_returns_fn_result(self):
        self.assertEqual(
            run_google_calendar_api(self.conn, lambda t: t + "!"),
            "at!",
        )

    def test_non_401_http_error_reraises(self):
        def boom(_t):
            exc = requests.HTTPError()
            exc.response = MagicMock(status_code=502)
            raise exc

        with self.assertRaises(requests.HTTPError):
            run_google_calendar_api(self.conn, boom)

    @patch("google_calendar_integration.services.refresh_google_tokens")
    def test_retries_once_on_401_with_refresh(self, mock_refresh):
        self.conn.set_refresh_token("rt")
        self.conn.save()
        calls = []

        def fn(token):
            calls.append(token)
            if len(calls) == 1:
                exc = requests.HTTPError()
                exc.response = MagicMock(status_code=401)
                raise exc
            return "done"

        mock_refresh.return_value = {
            "access_token": "fresh",
            "token_expiry": timezone.now() + timedelta(hours=1),
        }
        self.conn.set_access_token("at")
        self.conn.save()

        out = run_google_calendar_api(self.conn, fn)
        self.assertEqual(out, "done")
        mock_refresh.assert_called_once()

    def test_401_without_refresh_reraises(self):
        self.conn.set_refresh_token(None)
        self.conn.save()

        def fn(_t):
            exc = requests.HTTPError()
            exc.response = MagicMock(status_code=401)
            raise exc

        with self.assertRaises(requests.HTTPError):
            run_google_calendar_api(self.conn, fn)


class EnsureImportCalendarTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="E", slug="t-eic")
        self.user = User.objects.create_user(
            username="eic", email="eic@test.com", password="x", organization=self.org
        )

    @patch("google_calendar_integration.services.get_user_organization", return_value=None)
    def test_raises_without_organization(self, _mock_go):
        with self.assertRaises(ValueError):
            ensure_import_calendar(self.user, "a@b.com")

    def test_returns_existing_calendar(self):
        name = "Google (dup@test.com)"
        existing = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name=name,
            timezone="UTC",
            visibility="public",
            is_primary=False,
        )
        got = ensure_import_calendar(self.user, "dup@test.com")
        self.assertEqual(got.pk, existing.pk)

    def test_creates_when_missing(self):
        c = ensure_import_calendar(self.user, "new@test.com")
        self.assertTrue(c.pk)
        self.assertIn("Google (new@test.com)", c.name)


class PromoteEarlyExitTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="P", slug="t-pe")
        self.user = User.objects.create_user(
            username="pe", email="pe@test.com", password="x", organization=self.org
        )

    def test_skips_when_deleted(self):
        cal = Calendar(
            organization=self.org,
            owner=self.user,
            name="X",
            timezone="UTC",
            visibility="public",
            is_primary=False,
        )
        cal.is_deleted = True
        cal.save()
        promote_google_import_calendar_to_primary(cal)
        cal.refresh_from_db()
        self.assertFalse(cal.is_primary)

    def test_skips_without_owner(self):
        cal = Calendar.objects.create(
            organization=self.org,
            owner=None,
            name="Orphan",
            timezone="UTC",
            visibility="public",
            is_primary=False,
        )
        promote_google_import_calendar_to_primary(cal)
        cal.refresh_from_db()
        self.assertFalse(cal.is_primary)


class UpsertImportExtendedTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="U", slug="t-uie")
        self.user = User.objects.create_user(
            username="uie", email="uie@test.com", password="x", organization=self.org
        )
        self.imp = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Google (uie@test.com)",
            timezone="UTC",
            visibility="public",
            is_primary=False,
        )
        self.conn = GoogleCalendarConnection.objects.create(
            user=self.user,
            is_active=True,
            primary_calendar_id="p@x",
            import_calendar=self.imp,
        )
        self.conn = GoogleCalendarConnection.objects.select_related(
            "import_calendar"
        ).get(pk=self.conn.pk)

    @patch("google_calendar_integration.services.upsert_imported_event")
    def test_import_events_skips_when_inactive(self, mock_upsert):
        self.conn.is_active = False
        self.conn.save()
        import_events_for_connection(self.conn)
        mock_upsert.assert_not_called()

    def test_import_returns_without_primary_calendar_id(self):
        self.conn.primary_calendar_id = None
        self.conn.save()
        import_events_for_connection(self.conn)

    def test_import_returns_without_import_calendar_id(self):
        self.conn.import_calendar = None
        self.conn.import_calendar_id = None
        self.conn.save()
        import_events_for_connection(self.conn)

    @patch("google_calendar_integration.services.run_google_calendar_api")
    @patch("google_calendar_integration.services.reconcile_removed_events_after_google_import")
    def test_import_single_page_happy_path(self, mock_reconcile, mock_run):
        base = timezone.now()
        item = {
            "id": "ge1",
            "status": "tentative",
            "start": {"dateTime": base.replace(microsecond=0).isoformat(), "timeZone": "UTC"},
            "end": {
                "dateTime": (base + timedelta(hours=1)).replace(microsecond=0).isoformat(),
                "timeZone": "UTC",
            },
            "summary": "T",
            "etag": "e1",
            "recurringEventId": "rec1",
        }
        mock_run.return_value = {"items": [item], "nextPageToken": None}
        self.conn.refresh_from_db()
        import_events_for_connection(self.conn)
        mock_run.assert_called()
        mock_reconcile.assert_called_once()

    def test_import_http_error_sets_reconnect_on_401(self):
        self.conn.refresh_from_db()

        def raise_401(conn, fn):
            exc = requests.HTTPError()
            exc.response = MagicMock(status_code=401)
            raise exc

        with patch(
            "google_calendar_integration.services.run_google_calendar_api",
            side_effect=raise_401,
        ):
            with self.assertRaises(requests.HTTPError):
                import_events_for_connection(self.conn)
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.needs_reconnect)


class PlatformBodyShouldExportTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="B", slug="t-pb")
        self.user = User.objects.create_user(
            username="pb", email="pb@test.com", password="x", organization=self.org
        )
        self.cal = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            visibility="public",
            is_primary=True,
        )

    def test_platform_event_to_google_body_all_day(self):
        now = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        ev = Event(
            title="A",
            description="",
            start_datetime=now,
            end_datetime=now,
            timezone="UTC",
            is_all_day=True,
        )
        body = platform_event_to_google_body(ev)
        self.assertIn("date", body["start"])

    def test_should_export_skips_non_primary(self):
        non = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Sec",
            timezone="UTC",
            visibility="public",
            is_primary=False,
        )
        ev = Event.objects.create(
            organization=self.org,
            calendar=non,
            created_by=self.user,
            title="x",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        self.assertFalse(should_export_event_to_google(ev))

    def test_should_export_skips_recurring(self):
        ev = MagicMock()
        ev.calendar = self.cal
        ev.id = "evt-r"
        ev.is_recurring = True
        self.assertFalse(should_export_event_to_google(ev))

    def test_should_export_skips_import_source(self):
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="x",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
            metadata={METADATA_SOURCE_KEY: METADATA_SOURCE_GOOGLE_CALENDAR},
        )
        self.assertFalse(should_export_event_to_google(ev))


class ExportEventToGoogleServiceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="X", slug="t-ex")
        self.user = User.objects.create_user(
            username="exs", email="exs@test.com", password="x", organization=self.org
        )
        self.cal = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            visibility="public",
            is_primary=True,
        )
        self.conn = GoogleCalendarConnection.objects.create(
            user=self.user,
            is_active=True,
            primary_calendar_id="p@cal",
        )

    def test_export_skips_when_no_calendar_owner_path_logs(self):
        ev = MagicMock()
        ev.calendar = None
        ev.id = "evt-nc"
        export_event_to_google(ev)

    def test_export_skips_when_no_connection(self):
        GoogleCalendarConnection.objects.filter(user=self.user).delete()
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="t",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        export_event_to_google(ev)

    def test_export_skips_needs_reconnect(self):
        self.conn.needs_reconnect = True
        self.conn.save()
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="t",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        export_event_to_google(ev)

    @patch("google_calendar_integration.services.run_google_calendar_api")
    def test_export_insert_branch(self, mock_run):
        mock_run.return_value = {"id": "gid-new", "etag": "etag1"}
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="t",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
            metadata={},
        )
        export_event_to_google(ev)
        ev.refresh_from_db()
        self.assertEqual((ev.metadata or {}).get(METADATA_GOOGLE_EVENT_ID_KEY), "gid-new")
        self.conn.refresh_from_db()
        self.assertIsNotNone(self.conn.last_export_at)

    @patch("google_calendar_integration.services.run_google_calendar_api")
    def test_export_update_branch(self, mock_run):
        mock_run.return_value = {"etag": "etag2"}
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="t",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
            metadata={METADATA_GOOGLE_EVENT_ID_KEY: "gid-existing"},
        )
        export_event_to_google(ev)
        mock_run.assert_called()
        self.conn.refresh_from_db()
        self.assertIsNotNone(self.conn.last_export_at)

    @patch("google_calendar_integration.services.run_google_calendar_api")
    def test_export_soft_deleted_with_google_id_calls_delete_path(self, mock_run):
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="t",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
            is_deleted=True,
            metadata={METADATA_GOOGLE_EVENT_ID_KEY: "gid-del"},
        )
        export_event_to_google(ev)
        mock_run.assert_called_once()

    @patch("google_calendar_integration.services.run_google_calendar_api")
    def test_export_http_error_403_sets_reconnect_message(self, mock_run):
        exc = requests.HTTPError()
        exc.response = MagicMock(status_code=403)
        mock_run.side_effect = exc
        ev = Event.objects.create(
            organization=self.org,
            calendar=self.cal,
            created_by=self.user,
            title="t",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        with self.assertRaises(requests.HTTPError):
            export_event_to_google(ev)
        self.conn.refresh_from_db()
        self.assertTrue(self.conn.needs_reconnect)


class ExportPrimaryCalendarTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="EP", slug="t-ep")
        self.user = User.objects.create_user(
            username="ep", email="ep@test.com", password="x", organization=self.org
        )
        self.conn = GoogleCalendarConnection.objects.create(
            user=self.user,
            is_active=True,
            primary_calendar_id="p",
        )

    @patch("google_calendar_integration.services.get_user_organization", return_value=None)
    @patch("google_calendar_integration.services.logger")
    def test_skips_when_no_organization(self, _log, _mock_org):
        export_primary_calendar_events_to_google(self.conn)

    @patch("google_calendar_integration.services.export_event_to_google")
    def test_calls_export_per_event(self, mock_export):
        cal = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Primary",
            timezone="UTC",
            visibility="public",
            is_primary=True,
        )
        e1 = Event.objects.create(
            organization=self.org,
            calendar=cal,
            created_by=self.user,
            title="a",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        Event.objects.create(
            organization=self.org,
            calendar=cal,
            created_by=self.user,
            title="b",
            start_datetime=timezone.now(),
            end_datetime=timezone.now() + timedelta(hours=1),
            timezone="UTC",
        )
        export_primary_calendar_events_to_google(self.conn)
        self.assertEqual(mock_export.call_count, 2)
        e2 = Event.objects.exclude(pk=e1.pk).get(calendar=cal)
        ids = {mock_export.call_args_list[i][0][0].pk for i in range(2)}
        self.assertEqual(ids, {e1.pk, e2.pk})


class DisconnectUserCalendarTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="D", slug="t-dis")
        self.user = User.objects.create_user(
            username="dis", email="dis@test.com", password="x", organization=self.org
        )

    def test_noop_when_no_connection(self):
        disconnect_user_calendar(self.user)

    def test_clears_connection_and_deletes_import_calendar(self):
        imp = Calendar.objects.create(
            organization=self.org,
            owner=self.user,
            name="Google (d@test.com)",
            timezone="UTC",
            visibility="public",
            is_primary=False,
        )
        conn = GoogleCalendarConnection.objects.create(
            user=self.user,
            import_calendar_id=imp.id,
            is_active=True,
        )
        conn.set_access_token("x")
        conn.save()
        disconnect_user_calendar(self.user)
        self.assertFalse(
            GoogleCalendarConnection.objects.filter(user=self.user, is_active=True).exists()
        )
        self.assertFalse(Calendar.objects.filter(pk=imp.pk).exists())
