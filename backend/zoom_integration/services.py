import logging
import base64

import requests
from requests.exceptions import HTTPError, RequestException, Timeout
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import quote, urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .crypto import encrypt_token
from .models import ZoomCredential, ZoomMeetingData


logger = logging.getLogger(__name__)

ZOOM_AUTH_URL = "https://zoom.us/oauth/authorize"
ZOOM_TOKEN_URL = "https://zoom.us/oauth/token"
ZOOM_API_BASE  = "https://api.zoom.us/v2"


def get_authorization_url(state: str) -> str:
    """
    Generate URL for user to redirect to Zoom for authorization.
    ``state`` must be a server-issued signed payload (see views) so the callback can verify it
    without relying on session cookies.
    """
    params = {
        "response_type": "code",
        "client_id": settings.ZOOM_CLIENT_ID,
        "redirect_uri": settings.ZOOM_REDIRECT_URI,
        "state": state,
    }
    return f"{ZOOM_AUTH_URL}?{urlencode(params)}"


def _basic_auth_header() -> str:
    """
    Zoom token API requires Basic Auth with client_id:client_secret
    Format: base64("client_id:client_secret")
    """
    credentials = f"{settings.ZOOM_CLIENT_ID}:{settings.ZOOM_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def exchange_code_for_token(code: str) -> dict:
    """
    Exchange authorization code for access_token
    This is the standard Authorization Code Flow for OAuth
    """
    response = requests.post(
        ZOOM_TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.ZOOM_REDIRECT_URI,
        },
    )
    response.raise_for_status()  # if HTTP status code is 4xx/5xx, raise an exception
    return response.json()
    # return format: {"access_token": "...", "refresh_token": "...", "expires_in": 3600}


def refresh_access_token(credential: ZoomCredential) -> ZoomCredential:
    """
    access_token is valid for only 1 hour, refresh it with refresh_token after expiration
    """
    response = requests.post(
        ZOOM_TOKEN_URL,
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": credential.get_refresh_token(),
        },
    )
    response.raise_for_status()
    data = response.json()

    credential.set_tokens(data["access_token"], data["refresh_token"])
    credential.token_expires_at = timezone.now() + timedelta(seconds=data["expires_in"])
    credential.save(
        update_fields=[
            "encrypted_access_token",
            "encrypted_refresh_token",
            "token_expires_at",
            "updated_at",
        ],
    )
    return credential


def get_valid_credential(user) -> ZoomCredential:
    """
    Get valid credential: if token is about to expire, refresh it automatically
    This way the caller doesn't need to worry about token expiration
    """
    try:
        credential = user.zoom_credential
    except ZoomCredential.DoesNotExist:
        raise ValueError("User has not connected to Zoom account")

    # if token is about to expire, refresh it automatically
    if credential.token_expires_at <= timezone.now() + timedelta(minutes=5):
        try:
            credential = refresh_access_token(credential)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in (400, 401):
                credential.delete()
                raise PermissionError(
                    "Zoom connection has expired or been revoked. Please reconnect your Zoom account."
                ) from exc
            raise

    return credential


def save_token_for_user(user, token_data: dict) -> ZoomCredential:
    """
    Save tokens from Zoom to database
    update_or_create: if exists, update, if not, create
    """
    expires_at = timezone.now() + timedelta(seconds=token_data["expires_in"])

    credential, _ = ZoomCredential.objects.update_or_create(
        user=user,
        defaults={
            "encrypted_access_token": encrypt_token(token_data["access_token"]) or "",
            "encrypted_refresh_token": encrypt_token(token_data["refresh_token"]) or "",
            "token_expires_at": expires_at,
        },
    )
    return credential


def create_zoom_meeting(user, topic: str, start_time: str, duration: int = 60) -> dict:
    """
    Core functionality: create a Zoom meeting for the user
    
    Parameters:
        topic      meeting topic
        start_time ISO8601 format time, e.g. "2026-04-10T10:00:00Z"
        duration   meeting duration (minutes), default 60 minutes
    """
    credential = get_valid_credential(user)  # automatically refresh token

    response = requests.post(
        f"{ZOOM_API_BASE}/users/me/meetings",  # me represents the currently authorized user
        headers={
            "Authorization": f"Bearer {credential.get_access_token()}",
            "Content-Type": "application/json",
        },
        json={
            "topic": topic,
            "type": 2,              # 2 = scheduled meeting (different from instant meeting type=1)
            "start_time": start_time,
            "duration": duration,
            "settings": {
                "host_video": True,
                "participant_video": True,
                "waiting_room": True,   # enable waiting room
            },
        },
    )

    if response.status_code == 401:
        # 401 means token is invalid (possibly user revoked authorization), prompt user to reconnect
        raise PermissionError("Your Zoom authorization has expired. Please reconnect your Zoom account.")

    response.raise_for_status()
    return response.json()
    # return format: {"join_url": "...", "start_url": "...", "id": "...", "uuid": "..."}


def upsert_zoom_meeting_identity(
    meeting,
    *,
    zoom_meeting_id: str,
    zoom_uuid: str,
    zoom_host_user=None,
) -> ZoomMeetingData:
    """
    Persist Zoom identifiers for a MediaJira meeting. Uses get_or_create so OneToOne is safe to call repeatedly.
    ``zoom_host_user`` is the credential owner for webhook/Celery sync (user who linked the meeting).
    """
    zu = zoom_uuid or ""
    defaults = {
        "zoom_meeting_id": zoom_meeting_id,
        "zoom_uuid": zu,
        "sync_state": ZoomMeetingData.SyncState.NEVER,
    }
    if zoom_host_user is not None:
        defaults["zoom_host_user"] = zoom_host_user
    row, created = ZoomMeetingData.objects.get_or_create(
        meeting=meeting,
        defaults=defaults,
    )
    if not created:
        update_fields: list[str] = []
        if row.zoom_meeting_id != zoom_meeting_id:
            row.zoom_meeting_id = zoom_meeting_id
            update_fields.append("zoom_meeting_id")
        if row.zoom_uuid != zu:
            row.zoom_uuid = zu
            update_fields.append("zoom_uuid")
        if zoom_host_user is not None and row.zoom_host_user_id != zoom_host_user.id:
            row.zoom_host_user = zoom_host_user
            update_fields.append("zoom_host_user")
        if update_fields:
            row.save(update_fields=update_fields)
    return row


def find_zoom_meeting_data_for_webhook(
    zoom_meeting_id: str,
    zoom_uuid: str | None,
) -> ZoomMeetingData | None:
    """
    Resolve ZoomMeetingData from webhook identifiers: prefer (meeting_id + uuid) when uuid is non-empty;
    otherwise match meeting_id only. Multiple matches: log warning and return None.
    """
    logger = logging.getLogger(__name__)
    mid = str(zoom_meeting_id or "").strip()
    if not mid:
        return None
    zu = zoom_uuid or ""

    if zu:
        qs = ZoomMeetingData.objects.filter(zoom_meeting_id=mid, zoom_uuid=zu)
        n = qs.count()
        if n == 1:
            return qs.first()
        if n > 1:
            logger.warning(
                "Zoom webhook: multiple ZoomMeetingData for zoom_meeting_id=%s zoom_uuid=%s",
                mid,
                zu[:32] + ("..." if len(zu) > 32 else ""),
            )
            return None
        # n == 0: fall through to meeting_id-only match (uuid may not be stored yet)

    qs = ZoomMeetingData.objects.filter(zoom_meeting_id=mid)
    n = qs.count()
    if n == 1:
        return qs.first()
    if n > 1:
        logger.warning(
            "Zoom webhook: multiple ZoomMeetingData for zoom_meeting_id=%s (uuid fallback)",
            mid,
        )
    return None


# --- Step D: post-meeting sync (Zoom REST + ZoomMeetingData) ---
#
# Implementation (sequential):
# - Layer 1: meeting.ended -> GET /past_meetings/{uuid}, /participants -> meeting_status, actual_*,
#   duration_minutes, actual_participants_count.
# - Layer 2: recording.completed -> GET /meetings/{id}/recordings -> recording_* fields.
# - Layer 3: transcript/summary events -> participants pages + GET /meetings/{id}/meeting_summary.
#
# REST runs outside DB locks; updates use a short transaction.atomic + select_for_update.

EVENT_MEETING_ENDED = "meeting.ended"
EVENT_RECORDING_COMPLETED = "recording.completed"
EVENT_SUMMARY_COMPLETED = "meeting.summary_completed"
EVENT_TRANSCRIPT_COMPLETED = "recording.transcript_completed"

ZOOM_SYNC_EVENT_TYPES = frozenset(
    {
        EVENT_MEETING_ENDED,
        EVENT_RECORDING_COMPLETED,
        EVENT_SUMMARY_COMPLETED,
        EVENT_TRANSCRIPT_COMPLETED,
    }
)


def _uuid_path_segment(instance_uuid: str) -> str:
    """URL-encode instance uuid for Zoom path segments (handles '/' in uuid)."""
    return quote(instance_uuid, safe="")


def _resolve_instance_uuid(row: ZoomMeetingData, webhook_uuid: str) -> str:
    u = (row.zoom_uuid or "").strip()
    if u:
        return u
    return (webhook_uuid or "").strip()


def _parse_zoom_datetime(value: str | None):
    if not value:
        return None
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = parse_datetime(normalized)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, dt_timezone.utc)
    return dt


def zoom_api_get(user, path: str, *, timeout: int = 90) -> dict:
    """
    GET ``path`` relative to ``ZOOM_API_BASE`` (e.g. ``/past_meetings/...``).
    """
    credential = get_valid_credential(user)
    url = f"{ZOOM_API_BASE}{path}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {credential.get_access_token()}"},
        timeout=timeout,
    )
    if response.status_code == 401:
        raise PermissionError(
            "Your Zoom authorization has expired. Please reconnect your Zoom account."
        )
    if response.status_code == 404:
        raise ValueError("Zoom API returned 404 for this resource.")
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def _apply_zoom_meeting_data_sync_ok(
    zoom_meeting_data_id: int,
    field_updates: dict,
) -> None:
    """Short transaction: merge field updates and mark sync ok (clears sync_error)."""
    now = timezone.now()
    with transaction.atomic():
        row = ZoomMeetingData.objects.select_for_update().get(pk=zoom_meeting_data_id)
        for key, val in field_updates.items():
            setattr(row, key, val)
        row.sync_state = ZoomMeetingData.SyncState.OK
        row.sync_error = ""
        row.last_sync_at = now
        row.save()


def _apply_zoom_meeting_data_partial(
    zoom_meeting_data_id: int,
    field_updates: dict,
    sync_error_note: str,
) -> None:
    """Merge field updates, mark PARTIAL, store a short curated sync_error (not raw tracebacks)."""
    safe = (sync_error_note or "")[:500]
    now = timezone.now()
    with transaction.atomic():
        row = ZoomMeetingData.objects.select_for_update().get(pk=zoom_meeting_data_id)
        for key, val in field_updates.items():
            setattr(row, key, val)
        row.sync_state = ZoomMeetingData.SyncState.PARTIAL
        row.sync_error = safe
        row.last_sync_at = now
        row.save()


def _participants_sync_error_message(exc: BaseException) -> str:
    """Curated short string for sync_error; do not persist raw exception text."""
    if isinstance(exc, PermissionError):
        return "Zoom authorization issue when fetching participant report."
    if isinstance(exc, ValueError):
        return "Participant report unavailable (resource not found)."
    if isinstance(exc, HTTPError):
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None) if resp is not None else None
        if code == 403:
            return "Participant report unavailable (access denied)."
        if code == 404:
            return "Participant report unavailable (not found)."
        if code is not None and code >= 500:
            return "Participant report temporarily unavailable (server error)."
        if code is not None:
            return f"Participant report unavailable (HTTP {code})."
        return "Participant report unavailable."
    if isinstance(exc, Timeout):
        return "Participant report timed out."
    if isinstance(exc, RequestException):
        return "Participant report unavailable (network error)."
    return "Participant report unavailable."


def _sync_layer1_meeting_metrics(
    row: ZoomMeetingData,
    webhook_uuid: str,
) -> None:
    """Layer 1: ``meeting.ended`` — past meeting instance + participant total."""
    user = row.zoom_host_user
    if not user:
        raise ValueError("ZoomMeetingData has no zoom_host_user.")

    instance_uuid = _resolve_instance_uuid(row, webhook_uuid)
    if not instance_uuid:
        raise ValueError("Missing Zoom instance uuid (store zoom_uuid or pass webhook uuid).")

    seg = _uuid_path_segment(instance_uuid)
    past = zoom_api_get(user, f"/past_meetings/{seg}")

    start = _parse_zoom_datetime(past.get("start_time"))
    end = _parse_zoom_datetime(past.get("end_time"))
    duration = past.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            duration = None

    updates = {
        "meeting_status": ZoomMeetingData.MeetingStatus.ENDED,
        "actual_start_time": start,
        "actual_end_time": end,
        "duration_minutes": duration,
        "actual_participants_count": None,
    }

    total = None
    try:
        participants_data = zoom_api_get(
            user,
            f"/past_meetings/{seg}/participants?page_size=100",
        )
        raw_total = participants_data.get("total_records")
        if raw_total is not None:
            try:
                total = int(raw_total)
            except (TypeError, ValueError):
                total = None
    except Exception as exc:
        logger.warning(
            "Zoom layer1: participants sub-request failed for ZoomMeetingData id=%s",
            row.pk,
            exc_info=True,
        )
        updates["actual_participants_count"] = None
        _apply_zoom_meeting_data_partial(
            row.pk,
            updates,
            _participants_sync_error_message(exc),
        )
        return

    updates["actual_participants_count"] = total
    _apply_zoom_meeting_data_sync_ok(row.pk, updates)


def _sync_layer2_recordings(row: ZoomMeetingData, webhook_uuid: str) -> None:
    """Layer 2: ``recording.completed`` — cloud recording listing."""
    del webhook_uuid  # meeting id drives this endpoint
    user = row.zoom_host_user
    if not user:
        raise ValueError("ZoomMeetingData has no zoom_host_user.")
    mid = (row.zoom_meeting_id or "").strip()
    if not mid:
        raise ValueError("Missing zoom_meeting_id for recordings.")

    data = zoom_api_get(user, f"/meetings/{mid}/recordings")
    files = data.get("recording_files") or []
    urls: list[dict] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        urls.append(
            {
                "id": f.get("id"),
                "file_type": f.get("file_type"),
                "recording_type": f.get("recording_type"),
                "play_url": f.get("play_url"),
                "download_url": f.get("download_url"),
            }
        )
    recording_status = (
        ZoomMeetingData.RecordingStatus.AVAILABLE
        if files
        else ZoomMeetingData.RecordingStatus.NONE
    )
    meta = {
        "account_id": data.get("account_id"),
        "host_id": data.get("host_id"),
        "duration": data.get("duration"),
        "total_size": data.get("total_size"),
        "recording_count": data.get("recording_count"),
    }
    _apply_zoom_meeting_data_sync_ok(
        row.pk,
        {
            "recording_status": recording_status,
            "recording_urls_json": urls,
            "recording_metadata_json": meta,
        },
    )


def _participant_pages_and_structured(user, instance_uuid: str) -> tuple[list, list]:
    """Fetch all participant pages; return raw page payloads and a minimal structured list."""
    seg = _uuid_path_segment(instance_uuid)
    raw_pages: list[dict] = []
    structured: list[dict] = []
    page_token: str | None = None
    while True:
        path = f"/past_meetings/{seg}/participants?page_size=100"
        if page_token:
            path += f"&next_page_token={quote(page_token, safe='')}"
        page = zoom_api_get(user, path)
        raw_pages.append(page)
        for p in page.get("participants") or []:
            if not isinstance(p, dict):
                continue
            structured.append(
                {
                    "id": p.get("id"),
                    "user_id": p.get("user_id"),
                    "name": p.get("name"),
                    "user_email": p.get("user_email"),
                    "join_time": p.get("join_time"),
                    "leave_time": p.get("leave_time"),
                    "duration": p.get("duration"),
                }
            )
        page_token = page.get("next_page_token") or None
        if not page_token:
            break
    return raw_pages, structured


def _sync_layer3_participants(row: ZoomMeetingData, webhook_uuid: str) -> None:
    """Layer 3 (transcript event): participant lists."""
    user = row.zoom_host_user
    if not user:
        raise ValueError("ZoomMeetingData has no zoom_host_user.")
    instance_uuid = _resolve_instance_uuid(row, webhook_uuid)
    if not instance_uuid:
        raise ValueError("Missing Zoom instance uuid for participants.")

    raw_pages, structured = _participant_pages_and_structured(user, instance_uuid)
    _apply_zoom_meeting_data_sync_ok(
        row.pk,
        {
            "participant_raw_json": raw_pages,
            "participant_structured_json": structured,
        },
    )


def _sync_layer3_summary(row: ZoomMeetingData, webhook_uuid: str) -> None:
    """Layer 3 (summary event): meeting summary text when API allows."""
    del webhook_uuid
    user = row.zoom_host_user
    if not user:
        raise ValueError("ZoomMeetingData has no zoom_host_user.")
    mid = (row.zoom_meeting_id or "").strip()
    if not mid:
        raise ValueError("Missing zoom_meeting_id for meeting summary.")

    try:
        summary_payload = zoom_api_get(user, f"/meetings/{mid}/meeting_summary")
    except ValueError:
        _apply_zoom_meeting_data_sync_ok(
            row.pk,
            {
                "summary_status": ZoomMeetingData.SummaryStatus.NOT_APPLICABLE,
                "summary_text": "",
            },
        )
        return

    text = ""
    if isinstance(summary_payload, dict):
        text = (
            summary_payload.get("summary")
            or summary_payload.get("summary_content")
            or summary_payload.get("summary_text")
            or ""
        )
        if not text and isinstance(summary_payload.get("summary_details"), str):
            text = summary_payload["summary_details"]
    if not isinstance(text, str):
        text = str(text)

    _apply_zoom_meeting_data_sync_ok(
        row.pk,
        {
            "summary_status": ZoomMeetingData.SummaryStatus.AVAILABLE,
            "summary_text": text,
        },
    )


def _parse_vtt_to_text(vtt: str) -> str:
    """Strip VTT timestamps and metadata, return plain transcript text."""
    lines = []
    for line in vtt.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or "-->" in line or line.isdigit():
            continue
        lines.append(line)

    return " ".join(lines)

def _sync_layer3_transcript(row: ZoomMeetingData, webhook_uuid: str) -> None:
    """Layer 3 (transcript event): download VTT transcript and store on Meeting."""
    del webhook_uuid
    user = row.zoom_host_user
    if not user:
        raise ValueError("ZoomMeetingData has no zoom_host_user.")
    mid = (row.zoom_meeting_id or "").strip()
    if not mid:
        raise ValueError("Missing zoom_meeting_id for transcript.")

    credential = get_valid_credential(user=user)
    data = zoom_api_get(user=user, path=f"/meetings/{mid}/recordings")

    transcript_text = ""
    for f in data.get("recording_files") or []:
        if not isinstance(f, dict):
            continue
        if f.get("file_type") == "TRANSCRIPT":
            download_url = f.get("download_url")

            if not download_url:
                continue

            resp = requests.get(
                download_url,
                headers={"Authorization": f"Bearer {credential.get_access_token()}"},
                timeout=60
            )

            resp.raise_for_status()
            transcript_text = _parse_vtt_to_text(resp.text)
            break

    if transcript_text:
        with transaction.atomic():
            meeting = row.meeting
            meeting.transcript = transcript_text
            meeting.save(update_fields=["transcript"])

def sync_zoom_meeting_for_event(
    zoom_meeting_data_id: int,
    event_type: str,
    webhook_uuid: str,
) -> None:
    """
    Run event-scoped Zoom REST sync into ``ZoomMeetingData`` (REST outside transaction).
    """
    row = ZoomMeetingData.objects.select_related("zoom_host_user").get(pk=zoom_meeting_data_id)
    if not row.zoom_host_user_id:
        raise ValueError("ZoomMeetingData has no zoom_host_user.")

    et = (event_type or "").strip()
    if et == EVENT_MEETING_ENDED:
        _sync_layer1_meeting_metrics(row, webhook_uuid)
    elif et == EVENT_RECORDING_COMPLETED:
        _sync_layer2_recordings(row, webhook_uuid)
    elif et == EVENT_SUMMARY_COMPLETED:
        _sync_layer3_summary(row, webhook_uuid)
    elif et == EVENT_TRANSCRIPT_COMPLETED:
        _sync_layer3_participants(row, webhook_uuid)
        from zoom_integration.tasks import sync_meeting_transcript
        sync_meeting_transcript.delay(zoom_meeting_data_id) # type: ignore[operator]
    else:
        logging.getLogger(__name__).info(
            "zoom sync: ignored event_type=%s zoom_meeting_data_id=%s",
            et,
            zoom_meeting_data_id,
        )