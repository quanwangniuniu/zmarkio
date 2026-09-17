from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from calendars.models import Calendar, Event
from calendars.permissions import get_user_organization
from google_docs_integration.services import refresh_google_tokens

from .constants import (
    METADATA_GOOGLE_ETAG_KEY,
    METADATA_GOOGLE_EVENT_ID_KEY,
    METADATA_ICAL_UID_KEY,
    METADATA_RECURRING_EVENT_ID_KEY,
    METADATA_SOURCE_GOOGLE_CALENDAR,
    METADATA_SOURCE_KEY,
)
from .models import GoogleCalendarConnection

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class GoogleAvailabilityUnavailable(Exception):
    """A connected calendar could not be checked before confirming a booking."""


def is_retryable_google_error(exc) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return False
    code = exc.response.status_code
    if code in (408, 429) or code >= 500:
        return True
    if code == 403:
        try:
            errors = exc.response.json().get("error", {}).get("errors", [])
            return any(e.get("reason") in (
                "rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"
            ) for e in errors)
        except (ValueError, TypeError, AttributeError):
            return False
    return False

GOOGLE_CALENDAR_SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar",
]


def get_calendar_redirect_uri() -> str:
    uri = getattr(settings, "GOOGLE_CALENDAR_OAUTH_REDIRECT_URI", "") or ""
    return uri.strip()


def build_google_calendar_auth_url(state: str) -> str:
    redirect_uri = get_calendar_redirect_uri()
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_CALENDAR_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_OAUTH_BASE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> dict:
    redirect_uri = get_calendar_redirect_uri()
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    expires_in = int(payload.get("expires_in", 3600))
    payload["token_expiry"] = timezone.now() + timedelta(seconds=expires_in)
    return payload


def _persist_refreshed_tokens(connection: GoogleCalendarConnection, payload: dict) -> None:
    connection.set_access_token(payload["access_token"])
    new_refresh = payload.get("refresh_token")
    if new_refresh:
        connection.set_refresh_token(new_refresh)
    connection.token_expiry = payload.get("token_expiry")
    connection.save(
        update_fields=[
            "encrypted_access_token",
            "encrypted_refresh_token",
            "token_expiry",
            "updated_at",
        ]
    )


def get_access_token_for_api(connection: GoogleCalendarConnection) -> str:
    if not connection.get_access_token():
        raise ValueError("Google Calendar is not connected.")
    now = timezone.now()
    buffer = timedelta(minutes=2)
    expiry_ok = connection.token_expiry and connection.token_expiry > now + buffer
    if expiry_ok:
        return connection.get_access_token() or ""
    if not connection.get_refresh_token():
        return connection.get_access_token() or ""
    payload = refresh_google_tokens(connection.get_refresh_token())
    _persist_refreshed_tokens(connection, payload)
    return connection.get_access_token() or ""


def run_google_calendar_api(connection: GoogleCalendarConnection, fn):
    token = get_access_token_for_api(connection)
    try:
        return fn(token)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 401:
            raise
        if not connection.get_refresh_token():
            raise
        payload = refresh_google_tokens(connection.get_refresh_token())
        _persist_refreshed_tokens(connection, payload)
        return fn(connection.get_access_token() or "")


def fetch_google_email(access_token: str) -> str | None:
    response = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("email")


def fetch_primary_calendar_id(access_token: str) -> str:
    r = requests.get(
        f"{GOOGLE_CALENDAR_API_BASE}/users/me/calendarList",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"maxResults": 250},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    for item in data.get("items", []):
        if item.get("primary"):
            return item["id"]
    for item in data.get("items", []):
        if item.get("id") == "primary":
            return "primary"
    raise ValueError("No primary Google calendar found.")


def ensure_import_calendar(user, google_email: str | None) -> Calendar:
    org = get_user_organization(user)
    if not org:
        raise ValueError("User has no organization.")
    label = google_email or "Calendar"
    name = f"Google ({label})"
    existing = (
        Calendar.objects.filter(
            organization=org,
            owner=user,
            name=name,
            is_deleted=False,
        )
        .first()
    )
    if existing:
        return existing
    return Calendar.objects.create(
        organization=org,
        owner=user,
        name=name,
        description="Imported events from Google Calendar",
        is_primary=False,
    )


def promote_google_import_calendar_to_primary(google_calendar: Calendar) -> None:
    """
    Mark the Google-linked local calendar ("Google (email)") as the user's primary calendar.

    Google Calendar integration uses this single calendar for both import (events land here)
    and export (eligible events are read from primary). Calendar.save demotes any other
    primary for the same owner/organization (see calendars.Calendar.save).
    """
    if google_calendar.is_deleted or not google_calendar.owner_id:
        return
    google_calendar.is_primary = True
    google_calendar.save(update_fields=["is_primary", "updated_at"])


def _google_time_to_platform(start: dict, end: dict) -> tuple[datetime, datetime, bool, str]:
    tz = "UTC"
    if "dateTime" in start:
        s = parse_datetime(start["dateTime"])
        e = parse_datetime(end["dateTime"])
        tz = start.get("timeZone") or end.get("timeZone") or "UTC"
        if s and e and timezone.is_naive(s):
            s = timezone.make_aware(s, timezone.utc)
        if e and timezone.is_naive(e):
            e = timezone.make_aware(e, timezone.utc)
        if not s or not e:
            raise ValueError("Invalid dateTime from Google")
        return s, e, False, tz
    if "date" in start:
        sd = parse_date(start["date"])
        ed = parse_date(end["date"])
        if not sd or not ed:
            raise ValueError("Invalid date from Google")
        # Google end date is exclusive for all-day
        s = timezone.make_aware(datetime.combine(sd, datetime.min.time()), timezone.utc)
        e_exclusive = ed
        e_inclusive = e_exclusive - timedelta(days=1)
        e = timezone.make_aware(
            datetime.combine(e_inclusive, datetime.max.time().replace(microsecond=0)),
            timezone.utc,
        )
        return s, e, True, tz
    raise ValueError("Unsupported Google event time format")


def _ical_uid_for_imported_event(google_event: dict) -> str:
    ge_id = google_event.get("id") or ""
    return f"{ge_id}@google-import"


def upsert_imported_event(
    connection: GoogleCalendarConnection,
    google_event: dict,
) -> None:
    if not connection.import_calendar_id:
        raise ValueError("import_calendar not set")
    status = google_event.get("status", "confirmed")
    if status == "cancelled":
        external_id = google_event.get("id")
        if external_id:
            now = timezone.now()
            org_id = connection.import_calendar.organization_id
            Event.objects.filter(
                organization_id=org_id,
                calendar_id=connection.import_calendar_id,
                external_id=external_id,
                is_deleted=False,
            ).update(is_deleted=True, status="cancelled", updated_at=now)
            # Platform-primary copy that was exported to Google (same calendar as import source)
            Event.objects.filter(
                organization_id=org_id,
                calendar__owner=connection.user,
                calendar__is_primary=True,
                is_deleted=False,
                metadata__google_calendar_event_id=external_id,
            ).update(is_deleted=True, status="cancelled", updated_at=now)
        return

    start = google_event.get("start") or {}
    end = google_event.get("end") or {}
    if not start or not end:
        return

    s_dt, e_dt, all_day, tz = _google_time_to_platform(start, end)
    external_id = google_event.get("id")
    if not external_id:
        return

    ical_uid = _ical_uid_for_imported_event(google_event)
    etag = google_event.get("etag", "")

    meta = {
        METADATA_SOURCE_KEY: METADATA_SOURCE_GOOGLE_CALENDAR,
        METADATA_GOOGLE_ETAG_KEY: etag,
        METADATA_ICAL_UID_KEY: google_event.get("iCalUID"),
    }
    if google_event.get("recurringEventId"):
        meta[METADATA_RECURRING_EVENT_ID_KEY] = google_event["recurringEventId"]

    defaults = {
        "title": google_event.get("summary") or "(No title)",
        "description": google_event.get("description") or "",
        "start_datetime": s_dt,
        "end_datetime": e_dt,
        "timezone": tz,
        "is_all_day": all_day,
        "location": google_event.get("location") or "",
        "status": "confirmed" if status == "confirmed" else "tentative",
        "metadata": meta,
        "ical_uid": ical_uid,
        "is_recurring": False,
        "recurrence_rule": None,
    }

    event = Event.objects.filter(
        organization_id=connection.import_calendar.organization_id,
        calendar_id=connection.import_calendar_id,
        external_id=external_id,
        is_deleted=False,
    ).first()

    if event:
        for k, v in defaults.items():
            setattr(event, k, v)
        event.save()
    else:
        event = Event(
            organization_id=connection.import_calendar.organization_id,
            calendar_id=connection.import_calendar_id,
            external_id=external_id,
            created_by=connection.user,
            **defaults,
        )
        event.save()


def reconcile_removed_events_after_google_import(
    connection: GoogleCalendarConnection,
    google_event_ids: set[str],
    window_start,
    window_end,
) -> None:
    """
    Soft-delete local copies for Google events that no longer appear in the synced window.

    Import uses a time-bounded events.list; we only reconcile rows that intersect that
    same window so events outside the fetch range are not mistaken for deleted remotely.
    """
    if not connection.import_calendar_id:
        return
    org_id = connection.import_calendar.organization_id
    imp_cal_id = connection.import_calendar_id
    now = timezone.now()

    base_imp = Event.objects.filter(
        organization_id=org_id,
        calendar_id=imp_cal_id,
        is_deleted=False,
        start_datetime__lt=window_end,
        end_datetime__gt=window_start,
    ).exclude(external_id__isnull=True).exclude(external_id="")
    base_imp = base_imp.filter(metadata__source=METADATA_SOURCE_GOOGLE_CALENDAR)

    stale_external_ids = [
        eid
        for eid in base_imp.values_list("external_id", flat=True).distinct()
        if eid and eid not in google_event_ids
    ]
    if stale_external_ids:
        Event.objects.filter(
            organization_id=org_id,
            calendar_id=imp_cal_id,
            external_id__in=stale_external_ids,
            is_deleted=False,
        ).update(is_deleted=True, status="cancelled", updated_at=now)

    primary_qs = Event.objects.filter(
        organization_id=org_id,
        calendar__owner=connection.user,
        calendar__is_primary=True,
        is_deleted=False,
        start_datetime__lt=window_end,
        end_datetime__gt=window_start,
    ).select_related("calendar")

    primary_ids = []
    for ev in primary_qs.iterator(chunk_size=200):
        gid = (ev.metadata or {}).get(METADATA_GOOGLE_EVENT_ID_KEY)
        if gid and gid not in google_event_ids:
            primary_ids.append(ev.pk)
    if primary_ids:
        Event.objects.filter(pk__in=primary_ids).update(
            is_deleted=True, status="cancelled", updated_at=now
        )


def import_events_for_connection(connection: GoogleCalendarConnection) -> None:
    if not connection.is_active or not connection.primary_calendar_id:
        return
    if not connection.import_calendar_id:
        return

    cal = Calendar.objects.filter(pk=connection.import_calendar_id, is_deleted=False).first()
    if cal:
        promote_google_import_calendar_to_primary(cal)

    now = timezone.now()
    window_start = now - timedelta(days=90)
    window_end = now + timedelta(days=365)
    time_min = window_start.isoformat()
    time_max = window_end.isoformat()

    def _list_page(token: str, page_token: str | None = None):
        params: dict[str, Any] = {
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 250,
            # Include cancelled/deleted so we can mark matching platform rows (see upsert_imported_event)
            "showDeleted": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        cid = connection.primary_calendar_id
        r = requests.get(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(cid, safe='')}/events",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    try:
        google_event_ids: set[str] = set()
        page_token = None
        while True:
            pt = page_token

            def _call(token: str, _pt: str | None = pt):
                return _list_page(token, _pt)

            data = run_google_calendar_api(connection, _call)
            for item in data.get("items", []):
                g_eid = item.get("id")
                if g_eid:
                    google_event_ids.add(g_eid)
                upsert_imported_event(connection, item)
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        reconcile_removed_events_after_google_import(
            connection, google_event_ids, window_start, window_end
        )
        connection.last_import_at = timezone.now()
        connection.needs_reconnect = False
        connection.last_error_message = None
        connection.save(
            update_fields=["last_import_at", "needs_reconnect", "last_error_message", "updated_at"]
        )
    except requests.HTTPError as exc:
        msg = "Google Calendar import failed."
        if exc.response is not None and exc.response.status_code in (401, 403):
            connection.needs_reconnect = True
            msg = "Google authorization expired or was revoked. Please reconnect."
        logger.warning("google_calendar import failed user=%s: %s", connection.user_id, exc)
        connection.last_error_message = msg
        connection.save(update_fields=["needs_reconnect", "last_error_message", "updated_at"])
        raise


def platform_event_to_google_body(event: Event) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": event.title,
        "description": event.description or "",
    }
    if event.location:
        body["location"] = event.location
    if event.is_all_day:
        body["start"] = {"date": event.start_datetime.date().isoformat()}
        end_exclusive = event.end_datetime.date() + timedelta(days=1)
        body["end"] = {"date": end_exclusive.isoformat()}
    else:
        body["start"] = {
            "dateTime": event.start_datetime.isoformat(),
            "timeZone": event.timezone or "UTC",
        }
        body["end"] = {
            "dateTime": event.end_datetime.isoformat(),
            "timeZone": event.timezone or "UTC",
        }
    return body


def should_export_event_to_google(event: Event) -> bool:
    if not event.calendar:
        logger.info("google export: skip event=%s: no calendar on event", event.id)
        return False
    if not event.calendar.is_primary:
        logger.info(
            "google export: skip event=%s: not primary calendar (calendar_id=%s is_primary=%s)",
            event.id,
            event.calendar_id,
            event.calendar.is_primary,
        )
        return False
    if event.is_recurring:
        logger.info("google export: skip event=%s: recurring events are not exported", event.id)
        return False
    meta = event.metadata or {}
    if meta.get(METADATA_SOURCE_KEY) == METADATA_SOURCE_GOOGLE_CALENDAR:
        logger.info("google export: skip event=%s: metadata source is google_calendar import copy", event.id)
        return False
    return True


def export_event_to_google(event: Event) -> None:
    if not should_export_event_to_google(event):
        return
    owner = event.calendar.owner
    if not owner:
        logger.info("google export: skip event=%s: calendar has no owner", event.id)
        return
    try:
        connection = GoogleCalendarConnection.objects.get(user=owner, is_active=True)
    except GoogleCalendarConnection.DoesNotExist:
        logger.info(
            "google export: skip event=%s: no active GoogleCalendarConnection for owner=%s",
            event.id,
            owner.id,
        )
        return
    if connection.needs_reconnect or not connection.primary_calendar_id:
        logger.info(
            "google export: skip event=%s: connection needs_reconnect=%s primary_calendar_id=%s",
            event.id,
            connection.needs_reconnect,
            connection.primary_calendar_id,
        )
        return

    cid = connection.primary_calendar_id
    body = platform_event_to_google_body(event)
    meta = dict(event.metadata or {})
    google_event_id = meta.get(METADATA_GOOGLE_EVENT_ID_KEY)
    google_etag = meta.get(METADATA_GOOGLE_ETAG_KEY)
    # Google accepts caller-supplied base32hex IDs. Stable IDs make an insert
    # retry safe even when Google accepted it but its response was lost.
    insert_id = hashlib.sha256(
        f"{event.organization_id}:{event.pk}".encode()
    ).hexdigest()

    def _insert(token: str):
        r = requests.post(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(cid, safe='')}/events",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={**body, "id": insert_id},
            timeout=30,
        )
        if r.status_code == 409:
            # The previous attempt may have succeeded. Update that same event
            # so any local edits made while waiting for retry are applied too.
            return _patch(token)
        r.raise_for_status()
        return r.json()

    def _patch(token: str):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if google_etag:
            headers["If-Match"] = google_etag
        r = requests.patch(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(cid, safe='')}/events/{quote(google_event_id, safe='')}",
            headers=headers,
            json=body,
            timeout=30,
        )
        if r.status_code == 412 and google_etag:
            r2 = requests.get(
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(cid, safe='')}/events/{quote(google_event_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            r2.raise_for_status()
            latest = r2.json()
            etag = latest.get("etag")
            r = requests.patch(
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(cid, safe='')}/events/{quote(google_event_id, safe='')}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    **({"If-Match": etag} if etag else {}),
                },
                json=body,
                timeout=30,
            )
        r.raise_for_status()
        return r.json()

    def _delete(token: str):
        r = requests.delete(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(cid, safe='')}/events/{quote(google_event_id, safe='')}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if r.status_code in (404, 410):
            return
        r.raise_for_status()

    try:
        if event.is_deleted:
            # Also remove an insert whose response was lost before its remote
            # ID could be saved locally. A missing remote event is harmless.
            google_event_id = google_event_id or insert_id
            run_google_calendar_api(connection, _delete)
            return

        if not google_event_id:
            google_event_id = insert_id
            created = run_google_calendar_api(connection, _insert)
            meta[METADATA_GOOGLE_EVENT_ID_KEY] = created.get("id")
            meta[METADATA_GOOGLE_ETAG_KEY] = created.get("etag", "")
            event.metadata = meta
            event.save(update_fields=["metadata", "updated_at"])
        else:
            updated = run_google_calendar_api(connection, _patch)
            meta[METADATA_GOOGLE_ETAG_KEY] = updated.get("etag", google_etag)
            event.metadata = meta
            event.save(update_fields=["metadata", "updated_at"])

        connection.last_export_at = timezone.now()
        connection.last_error_message = None
        connection.needs_reconnect = False
        connection.save(
            update_fields=["last_export_at", "last_error_message", "needs_reconnect", "updated_at"]
        )
    except requests.RequestException as exc:
        msg = "Could not sync event to Google Calendar."
        if exc.response is not None and exc.response.status_code in (401, 403) and not is_retryable_google_error(exc):
            connection.needs_reconnect = True
            msg = "Google authorization expired. Reconnect in Settings."
        logger.warning("google_calendar export failed event=%s: %s", event.id, exc)
        connection.last_error_message = msg
        connection.save(update_fields=["needs_reconnect", "last_error_message", "updated_at"])
        raise


def export_primary_calendar_events_to_google(connection: GoogleCalendarConnection) -> None:
    """Push eligible primary-calendar platform events to Google (same rules as export_event_to_google)."""
    org = get_user_organization(connection.user)
    if not org:
        logger.info(
            "google export: no organization for user=%s; skipping export",
            connection.user_id,
        )
        return
    primary = (
        Calendar.objects.filter(
            organization=org,
            owner=connection.user,
            is_primary=True,
            is_deleted=False,
        )
        .first()
    )
    logger.info(
        "google export: found primary calendar=%s for user=%s",
        primary.id if primary else None,
        connection.user_id,
    )
    if not primary:
        logger.info(
            "google export: no primary calendar row for org=%s user=%s; skipping export",
            org.id,
            connection.user_id,
        )
        return
    for event in (
        Event.objects.filter(calendar=primary)
        .select_related("calendar", "calendar__owner")
        .iterator(chunk_size=50)
    ):
        logger.info("google export: checking event=%s title=%s", event.id, event.title)
        export_event_to_google(event)


@transaction.atomic
def disconnect_user_calendar(user) -> None:
    conn = GoogleCalendarConnection.objects.filter(user=user).first()
    if not conn:
        return
    cal = conn.import_calendar
    conn.import_calendar = None
    conn.set_access_token(None)
    conn.set_refresh_token(None)
    conn.token_expiry = None
    conn.primary_calendar_id = None
    conn.is_active = False
    conn.needs_reconnect = False
    conn.last_error_message = None
    conn.google_email = None
    conn.save()
    if cal:
        cal.delete()


# ── Availability for booking links ──────────────────────────────


def fetch_google_busy_intervals(
    connection: GoogleCalendarConnection,
    time_min: datetime,
    time_max: datetime,
    calendar_ids: list[str] | None = None,
    *,
    strict: bool = False,
) -> list[tuple[datetime, datetime]]:
    """
    Busy intervals from Google Calendar for one connection, via the freeBusy API.

    Returns timezone-aware UTC (start, end) tuples, unmerged and unsorted —
    callers merge them together with the platform's own busy intervals.

    Returns an empty list when the connection is unusable (disconnected, no
    token, needs reconnect). A booking page must still render availability from
    the in-app calendar when Google is unavailable; treating that as fatal would
    take the whole page down for an integration problem. Callers that need to
    distinguish "no busy time" from "could not reach Google" should check
    `connection.is_active` / `needs_reconnect` themselves.
    """
    if connection is None or not connection.is_active:
        return []
    if connection.needs_reconnect or not connection.get_access_token():
        if strict:
            raise GoogleAvailabilityUnavailable()
        return []

    ids = calendar_ids or [connection.primary_calendar_id or "primary"]
    body = {
        "timeMin": _google_rfc3339(time_min),
        "timeMax": _google_rfc3339(time_max),
        "items": [{"id": cid} for cid in ids if cid],
    }
    if not body["items"]:
        return []

    def _query(token: str):
        r = requests.post(
            f"{GOOGLE_CALENDAR_API_BASE}/freeBusy",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    try:
        payload = run_google_calendar_api(connection, _query)
    except (requests.RequestException, ValueError) as exc:
        if strict:
            raise GoogleAvailabilityUnavailable() from exc
        logger.warning(
            "Google freeBusy lookup failed for connection %s; "
            "falling back to platform availability only.",
            connection.pk,
            exc_info=True,
        )
        return []

    if strict and not all(
        item["id"] in (payload.get("calendars") or {}) for item in body["items"]
    ):
        raise GoogleAvailabilityUnavailable()
    intervals: list[tuple[datetime, datetime]] = []
    for calendar_id, entry in (payload.get("calendars") or {}).items():
        # Google reports per-calendar errors inline rather than failing the call
        # (e.g. notFound for a calendar the user lost access to).
        errors = entry.get("errors")
        if errors:
            if strict:
                raise GoogleAvailabilityUnavailable()
            logger.warning(
                "Google freeBusy returned errors for calendar %s: %s", calendar_id, errors
            )
            continue
        for period in entry.get("busy") or []:
            start = parse_datetime(period.get("start") or "")
            end = parse_datetime(period.get("end") or "")
            if not start or not end:
                if strict:
                    raise GoogleAvailabilityUnavailable()
                continue
            if timezone.is_naive(start):
                start = timezone.make_aware(start, timezone.utc)
            if timezone.is_naive(end):
                end = timezone.make_aware(end, timezone.utc)
            if end > start:
                intervals.append((start.astimezone(timezone.utc), end.astimezone(timezone.utc)))
    return intervals


def _google_rfc3339(value: datetime) -> str:
    """Format a datetime for the Google API, which requires an explicit offset."""
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def get_merged_busy_intervals(
    calendars,
    google_connection: GoogleCalendarConnection | None,
    time_min: datetime,
    time_max: datetime,
    *,
    strict_google: bool = False,
) -> list[tuple[datetime, datetime]]:
    """
    One busy timeline for a person, combining the in-app calendar and Google.

    Lives here rather than in `calendars` because this direction of dependency
    already exists: google_calendar_integration imports calendars, not the
    reverse.

    Google contributes nothing when it is unavailable — see
    fetch_google_busy_intervals — so the result degrades to platform-only
    rather than failing.
    """
    from calendars.services import get_busy_intervals, merge_busy_intervals

    intervals = list(get_busy_intervals(calendars, time_min, time_max))
    if google_connection is not None:
        intervals.extend(
            fetch_google_busy_intervals(google_connection, time_min, time_max, strict=strict_google)
        )
    return merge_busy_intervals(intervals)


def get_merged_availability(
    *,
    calendars,
    google_connection: GoogleCalendarConnection | None,
    rules,
    windows,
    tz_name: str,
    range_start: datetime,
    range_end: datetime,
    now: datetime | None = None,
    strict_google: bool = False,
) -> list[tuple[datetime, datetime]]:
    """
    Bookable slots for a booking link, as UTC intervals.

    `rules` is a calendars.availability.BookingRules and `windows` a list of
    WeeklyWindow. Both are passed in rather than read off a model, so this stays
    usable regardless of how booking links end up being stored.

    Note for callers: this makes a live Google API call, so a public endpoint
    should cache per (link, range) rather than hitting it on every keystroke.
    """
    from calendars.availability import compute_available_slots

    busy = get_merged_busy_intervals(
        calendars,
        google_connection,
        range_start - timedelta(minutes=rules.buffer_before_minutes),
        range_end + timedelta(minutes=rules.buffer_after_minutes),
        strict_google=strict_google,
    )
    return compute_available_slots(
        windows=windows,
        busy=busy,
        rules=rules,
        tz_name=tz_name,
        range_start=range_start,
        range_end=range_end,
        now=now or timezone.now(),
    )


def is_slot_still_available(
    *,
    calendars,
    google_connection: GoogleCalendarConnection | None,
    rules,
    windows,
    tz_name: str,
    slot_start: datetime,
    now: datetime | None = None,
) -> bool:
    """
    Re-check one slot at booking time.

    Availability is shown from a snapshot, so a prospect can submit a slot that
    was taken while the page sat open. The booking endpoint must call this
    before writing the event, or two people can book the same time.
    """
    now = now or timezone.now()
    if not (now + timedelta(minutes=rules.min_notice_minutes) <= slot_start
            <= now + timedelta(days=rules.max_advance_days)):
        return False
    slot_end = slot_start + timedelta(minutes=rules.duration_minutes)
    slots = get_merged_availability(
        calendars=calendars,
        google_connection=google_connection,
        rules=rules,
        windows=windows,
        tz_name=tz_name,
        range_start=slot_start,
        range_end=slot_end,
        now=now,
        strict_google=True,
    )
    return (slot_start, slot_end) in slots
