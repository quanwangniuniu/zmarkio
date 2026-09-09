import logging

from celery import shared_task

from .models import GoogleCalendarConnection
from .services import import_events_for_connection

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True)
def import_for_connection_task(self, connection_id: int):
    conn = GoogleCalendarConnection.objects.filter(id=connection_id, is_active=True).first()
    if not conn:
        return
    try:
        import_events_for_connection(conn)
    except Exception:
        logger.exception("google_calendar import_for_connection failed id=%s", connection_id)


@shared_task(bind=True, ignore_result=True)
def sync_all_google_calendar_imports(self):
    qs = GoogleCalendarConnection.objects.filter(is_active=True)
    for conn in qs.iterator():
        try:
            import_events_for_connection(conn)
        except Exception:
            logger.exception("google_calendar beat sync failed user=%s", conn.user_id)


@shared_task(bind=True, ignore_result=True, max_retries=5)
def export_event_to_google_task(self, event_id: str, tenant_schema: str = 'public'):
    """
    Export one event to the owner's Google Calendar.

    `tenant_schema` is required for orgs with a provisioned schema: Event is
    tenant-scoped and a Celery worker never passes through
    TenantSchemaMiddleware, so without it the lookup runs against `public` and
    silently finds nothing. Defaults to 'public' so existing callers keep their
    current behaviour.

    Deleting is the same call: re-queue the task after a soft delete and
    export_event_to_google takes the `is_deleted` branch, which removes the
    Google copy.

    Transient Google failures retry with exponential backoff. Permanent errors
    remain failed tasks, with connection details available in Settings.
    """
    from calendars.models import Event

    from core.tenant_context import tenant_schema_context

    from .services import export_event_to_google, is_retryable_google_error

    with tenant_schema_context(tenant_schema):
        ev = Event.objects.filter(id=event_id).first()
        if not ev:
            return
        try:
            export_event_to_google(ev)
        except Exception as exc:
            logger.exception("google_calendar export failed event=%s", event_id)
            if is_retryable_google_error(exc):
                raise self.retry(exc=exc, countdown=min(30 * 2 ** self.request.retries, 600))
            raise
