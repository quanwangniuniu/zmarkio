"""
RAG indexing signals for the meetings app (MED-264).

Every handler here just says "this Meeting's content may have changed" and
enqueues the same per-document Celery task signals everywhere else use —
`rag.tasks.index_document_task`. It does not decide whether anything
actually needs re-embedding: `index_source_document`'s content_hash +
pipeline_hash check does that cheaply (a SKIPPED_UNCHANGED no-op) after one
locked transaction. This is deliberate: coupling this file to which fields
the extractor actually reads would risk a missed re-index whenever
`rag.extraction.extract_meeting` changes, for the sake of avoiding some
redundant lightweight Celery tasks.

Enqueue reliability (best-effort, not guaranteed)
--------------------------------------------------
`transaction.on_commit(...)` guarantees a worker never observes an
uncommitted source change, but the DB commit and the Celery `.delay()`
publish are two separate operations, not one atomic unit. The enqueue
helpers below catch and log exceptions from Celery `.delay()` rather than
propagating them, so a broker outage does not fail the user-facing request
that triggered the signal.

MED-264 Problem 7 reliability handling:

Each RAG-relevant mutation synchronously calls
`rag.indexing.mark_source_dirty` before registering its
`transaction.on_commit` enqueue callback. This records that the source has
not yet been confirmed reconciled independently of whether the subsequent
Celery publish succeeds.

The dirty marker is deliberately separate from
`DocumentIndexState.status`: status describes an indexing attempt's
lifecycle, while `dirty_since` records an unresolved source mutation.

`rag.tasks.reconcile_dirty_rag_states`, scheduled via Celery Beat, scans for
sources whose dirty marker has remained unresolved beyond
`settings.RAG_RECONCILE_STALE_SECONDS` and re-enqueues the existing
`index_document_task`. This provides automatic bounded recovery for lost
Celery publishes and indexing attempts that never successfully reconcile,
without introducing a transactional outbox.

The dirty-marker write is not claimed to be strictly atomic with every
Meeting/MeetingDocument mutation. Several existing mutation paths use
Django's default autocommit, leaving a narrow process-crash window between
the business write and the signal-side marker write. Closing that window
would require broader transaction changes across unrelated business paths
and is intentionally outside this fix.

For hard deletes and soft-delete/exclusion transitions, the signal also
attempts `rag.indexing.clear_source_index` synchronously. This is a DB-only
fast path that re-verifies the source's current state while holding the RAG
state lock before removing derived chunks, avoiding stale-cleanup races such
as exclude -> re-include or project A -> B -> A.

The synchronous clear is a latency optimization, not the reliability
mechanism itself: `dirty_since` plus the periodic reconciler remains the
backstop when immediate cleanup or the initial Celery publish does not
complete successfully.

`current_tenant_schema()` reads the schema already active on this
connection — inside a request, TenantSchemaMiddleware has already set it
correctly, so there's no need to re-derive it from the user/org.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from core.tenant_context import current_tenant_schema
from meetings.models import Meeting, MeetingDocument
from rag.models import DocumentSourceType

logger = logging.getLogger(__name__)


def _enqueue_meeting_index(project_id: int, meeting_id: int) -> None:
    from rag.indexing import mark_source_dirty

    try:
        mark_source_dirty(project_id, DocumentSourceType.MEETING, str(meeting_id))
    except Exception:
        logger.exception('Failed to mark RAG state dirty for meeting %s', meeting_id)

    tenant_schema = current_tenant_schema()

    def enqueue() -> None:
        try:
            from rag.tasks import index_document_task
            index_document_task.delay(
                tenant_schema=tenant_schema,
                project_id=project_id,
                source_type=DocumentSourceType.MEETING,
                source_id=str(meeting_id),
            )
        except Exception:
            logger.exception('Failed to enqueue RAG indexing for meeting %s', meeting_id)

    transaction.on_commit(enqueue)


def _synchronous_meeting_cleanup(project_id: int, meeting_id: int) -> None:
    """DB-only fast path for a hard delete or a soft-delete/exclusion
    transition -- see module docstring. Never lets a failure here propagate
    into the request that triggered it, matching the `.delay()` convention
    above.
    """
    from rag.indexing import clear_source_index

    try:
        clear_source_index(project_id, DocumentSourceType.MEETING, str(meeting_id))
    except Exception:
        logger.exception('Failed synchronous RAG cleanup for meeting %s', meeting_id)


@receiver(post_save, sender=Meeting)
def handle_meeting_saved(sender, instance: Meeting, **kwargs) -> None:
    _enqueue_meeting_index(instance.project_id, instance.id)
    if instance.is_deleted:
        # Soft-delete/exclusion transition -- extract_meeting() already
        # excludes is_deleted=True Meetings, so the async task would reach
        # the same EXCLUDED outcome; this just removes that latency window.
        _synchronous_meeting_cleanup(instance.project_id, instance.id)


@receiver(post_delete, sender=Meeting)
def handle_meeting_deleted(sender, instance: Meeting, **kwargs) -> None:
    # Meeting is gone; index_source_document will find the row missing and
    # take the EXCLUDED path (hard-delete any existing chunks). project_id
    # is a plain column already loaded on `instance` -- no relation query,
    # no cascade-ordering concern.
    _enqueue_meeting_index(instance.project_id, instance.id)
    _synchronous_meeting_cleanup(instance.project_id, instance.id)


@receiver(pre_delete, sender=MeetingDocument)
def capture_meeting_document_project_before_delete(sender, instance: MeetingDocument, **kwargs) -> None:
    """Capture project_id BEFORE deletion, not in post_delete.

    For a standalone MeetingDocument delete, the parent Meeting is
    untouched and this is a plain, safe relation query. During a Meeting
    cascade delete, we deliberately do not assume anything about reverse
    deletion ordering: if the parent can't be resolved at this point, we
    simply capture nothing here. That's fine — Meeting's own post_delete
    handler above independently enqueues the same cleanup for the whole
    source in that case (a harmless duplicate; indexing is idempotent).
    """
    try:
        instance._rag_project_id = instance.meeting.project_id
    except Meeting.DoesNotExist:
        instance._rag_project_id = None


@receiver(post_delete, sender=MeetingDocument)
def handle_meeting_document_deleted(sender, instance: MeetingDocument, **kwargs) -> None:
    project_id = getattr(instance, '_rag_project_id', None)
    if project_id is None:
        # Either the parent Meeting was already gone by pre_delete (a
        # cascade delete, which Meeting's own handler already covers), or
        # pre_delete somehow didn't run. Nothing safe to act on here.
        return
    # meeting_id is a plain column on `instance`, always available without
    # a query regardless of what happened to the parent row.
    _enqueue_meeting_index(project_id, instance.meeting_id)


@receiver(post_save, sender=MeetingDocument)
def handle_meeting_document_saved(sender, instance: MeetingDocument, **kwargs) -> None:
    _enqueue_meeting_index(instance.meeting.project_id, instance.meeting_id)
