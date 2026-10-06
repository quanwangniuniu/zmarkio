import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import F
from django.utils import timezone
from django.utils.module_loading import import_string

from tracking.enums import EndReason, Source
from tracking.models import TrackingEvent, TrackingSession
from tracking.services import ingest_event

User = get_user_model()
logger = logging.getLogger(__name__)

_BATCH_SIZE = 5000


@shared_task
def emit_tracking_event(user_id, request_path, request_method, request_meta=None):
    if request_meta is None:
        request_meta = {}

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("emit_tracking_event: user %s not found, skipping", user_id)
        return

    for handler_path in settings.TRACKING_HANDLERS:
        try:
            handler = import_string(handler_path)
            for spec in handler(user_id, request_path, request_method, request_meta):
                ingest_event(
                    source=Source.MIDDLEWARE,
                    event_type=spec['event_type'],
                    user=user,
                    metadata=spec['metadata'],
                    target=spec.get('target'),
                )
        except Exception:
            logger.exception(
                "emit_tracking_event: handler %r raised, skipping", handler_path
            )


@shared_task
def expire_stale_sessions():
    cutoff = timezone.now() - timedelta(seconds=settings.TRACKING_SESSION_TIMEOUT_SECONDS)
    updated = TrackingSession.objects.filter(
        last_heartbeat_at__lt=cutoff,
        ended_at__isnull=True,
    ).update(
        ended_at=F('last_heartbeat_at'),
        end_reason=EndReason.TIMEOUT,
    )
    logger.info("expire_stale_sessions: marked %d sessions as TIMEOUT", updated)
    return updated


@shared_task
def purge_old_data():
    now = timezone.now()
    event_cutoff = now - timedelta(days=settings.TRACKING_EVENT_RETENTION_DAYS)
    session_cutoff = now - timedelta(days=settings.TRACKING_SESSION_RETENTION_DAYS)

    # Events first — avoids FK issues when sessions are deleted after
    event_total = 0
    while True:
        ids = list(
            TrackingEvent.objects.filter(created_at__lt=event_cutoff)
            .values_list('id', flat=True)[:_BATCH_SIZE]
        )
        if not ids:
            break
        deleted, _ = TrackingEvent.objects.filter(id__in=ids).delete()
        event_total += deleted

    # Sessions: only purge already-ended ones
    session_total = 0
    while True:
        ids = list(
            TrackingSession.objects.filter(
                started_at__lt=session_cutoff,
                ended_at__isnull=False,
            ).values_list('id', flat=True)[:_BATCH_SIZE]
        )
        if not ids:
            break
        deleted, _ = TrackingSession.objects.filter(id__in=ids).delete()
        session_total += deleted

    logger.info("purge_old_data: deleted %d events, %d sessions", event_total, session_total)
    return event_total, session_total
