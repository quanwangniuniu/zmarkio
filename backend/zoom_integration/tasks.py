"""
Celery tasks for Zoom webhooks. REST calls and DB field updates live in services; this module orchestrates.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import ZoomMeetingData
from .services import ZOOM_SYNC_EVENT_TYPES, sync_zoom_meeting_for_event, _sync_layer3_transcript

logger = logging.getLogger(__name__)


def _mark_sync_failed(zoom_meeting_data_id: int, message: str) -> None:
    safe = (message or "")[:2000]
    now = timezone.now()
    with transaction.atomic():
        row = ZoomMeetingData.objects.select_for_update().get(pk=zoom_meeting_data_id)
        row.sync_state = ZoomMeetingData.SyncState.ERROR
        row.sync_error = safe
        row.last_sync_at = now
        row.save(update_fields=["sync_state", "sync_error", "last_sync_at"])


@shared_task(bind=True, ignore_result=True)
def process_zoom_webhook_event(
    self,
    event_type: str,
    zoom_meeting_data_id: int,
    webhook_uuid: str,
) -> None:
    """
    Run post-meeting sync for a known webhook event. Thin payload: PK + event + webhook uuid.
    """
    et = (event_type or "").strip()
    if et not in ZOOM_SYNC_EVENT_TYPES:
        logger.info(
            "zoom webhook task: skip unknown event_type=%s zoom_meeting_data_id=%s",
            et,
            zoom_meeting_data_id,
        )
        return

    if not ZoomMeetingData.objects.filter(pk=zoom_meeting_data_id).exists():
        logger.warning(
            "zoom webhook task: ZoomMeetingData missing id=%s",
            zoom_meeting_data_id,
        )
        return

    ZoomMeetingData.objects.filter(pk=zoom_meeting_data_id).update(
        sync_state=ZoomMeetingData.SyncState.IN_PROGRESS,
    )

    try:
        sync_zoom_meeting_for_event(
            zoom_meeting_data_id,
            event_type,
            webhook_uuid or "",
        )
    except Exception as exc:
        logger.exception(
            "zoom webhook task: sync failed zoom_meeting_data_id=%s event=%s",
            zoom_meeting_data_id,
            et,
        )
        try:
            _mark_sync_failed(zoom_meeting_data_id, str(exc))
        except Exception:
            logger.exception(
                "zoom webhook task: failed to persist sync error id=%s",
                zoom_meeting_data_id,
            )

@shared_task(bind=True, ignore_result=True)
def sync_meeting_transcript(self, zoom_meeting_data_id: int) -> None:
    """Download and store Zoom transcript for a meeting.

    The post_save signal on Meeting automatically updates search_vector after save.
    """
    if not ZoomMeetingData.objects.filter(pk=zoom_meeting_data_id).exists():
        logger.warning("sync_meeting_transcript: ZoomMeetingData missing id=%s", zoom_meeting_data_id)
        return

    row = ZoomMeetingData.objects.select_related("zoom_host_user").get(pk=zoom_meeting_data_id)

    try:
        _sync_layer3_transcript(row, "")
    except Exception:
        logger.exception(
            "sync_meeting_transcript: failed zoom_meeting_data_id=%s",
            zoom_meeting_data_id
        )