"""
Celery tasks for RAG indexing (MED-264).

Thin wrappers around rag.indexing.index_source_document: these own tenant
schema switching (core.tenant_context.tenant_schema_context) and Celery
retry policy. All actual indexing logic — extraction, chunking, embedding,
locking, reconciliation — lives in rag.indexing and is exercised directly
by tests without any Celery machinery.

Three retry layers, deliberately kept separate rather than merged:
  1. core.services.gemini_embeddings: short transient HTTP retries
     (2s/4s/8s) plus a circuit breaker, inside a single embedding call.
  2. index_document_task / SUPERSEDED: a short, bounded 5s retry (max 3).
     By the time this outcome fires, another run has almost always already
     committed the current version — retrying is a cheap self-healing check
     in case the signal for THAT version was somehow dropped, not a real
     backoff.
  3. index_document_task / FAILED: a coarse outer backoff (30s -> 60s ->
     120s -> 240s -> capped at 300s, via self.request.retries) for recovery
     across a longer provider outage — layer 1's retries are already
     exhausted by the time FAILED is returned, so this is a second, slower
     layer on top, not a duplicate of the same wait.
"""
from __future__ import annotations

import logging

from celery import shared_task

from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from meetings.models import Meeting
from notion_editor.models import DraftProjectLink
from rag.indexing import IndexOutcome, index_source_document
from rag.models import DocumentSourceType
from retrospective.models import RetrospectiveStatus, RetrospectiveTask

logger = logging.getLogger(__name__)

_SUPERSEDED_RETRY_COUNTDOWN_SECONDS = 5
_SUPERSEDED_MAX_RETRIES = 3

_FAILED_RETRY_BASE_SECONDS = 30
_FAILED_RETRY_MAX_SECONDS = 300


def _failed_retry_countdown(retries: int) -> int:
    """30 -> 60 -> 120 -> 240 -> capped at 300, keyed off the task's own
    retry count so it's a real exponential backoff, not a fixed delay.
    """
    return min(_FAILED_RETRY_BASE_SECONDS * (2 ** retries), _FAILED_RETRY_MAX_SECONDS)


@shared_task(bind=True, max_retries=5)
def index_document_task(self, tenant_schema: str, project_id: int, source_type: str, source_id: str):
    """Index one source document. The exact same task both signals and
    `rebuild_project_index_task` enqueue — there is no separate rebuild code
    path, per MED-264's "reuse the exact same source-level indexing service"
    requirement.

    MOVED/EXCLUDED/INDEXED/SKIPPED_UNCHANGED/METADATA_REFRESHED are all
    terminal, never retried — a MOVED result has already cleaned up
    everything this call owns; the new project's indexing is a separate
    enqueue from whatever detected the reassignment, not this task's job.
    """
    with tenant_schema_context(tenant_schema):
        result = index_source_document(project_id, source_type, source_id)

    logger.info(
        'RAG index_document_task outcome=%s source_type=%s source_id=%s project_id=%s tenant_schema=%s detail=%s',
        result.outcome.value, source_type, source_id, project_id, tenant_schema, result.detail,
    )

    if result.outcome == IndexOutcome.SUPERSEDED:
        raise self.retry(
            exc=RuntimeError(result.detail or 'superseded'),
            countdown=_SUPERSEDED_RETRY_COUNTDOWN_SECONDS,
            max_retries=_SUPERSEDED_MAX_RETRIES,
        )
    if result.outcome == IndexOutcome.FAILED:
        raise self.retry(
            exc=RuntimeError(result.detail or 'failed'),
            countdown=_failed_retry_countdown(self.request.retries),
        )

    return result.outcome.value


@shared_task(bind=True)
def rebuild_project_index_task(self, tenant_schema: str, project_id: int):
    """Enumerate every *currently indexable* source for `project_id` and fan
    out to `index_document_task` — cheap, read-only enumeration; all real
    work (chunking, embedding, locking) happens in the fanned-out tasks,
    each independently idempotent/retryable, so a partial failure only
    requires re-running the sources that actually failed, not the whole
    project.

    This is a backfill/re-index pass over live sources (existing production
    data after deployment, or a re-run after an eval-driven pipeline
    retune) — NOT an orphan-purge repair pass. It does not scan DocumentChunk
    for rows whose source no longer exists; a chunk left behind by a source
    that was hard-deleted without its delete signal ever firing (e.g. a
    dropped Celery task, a direct DB deletion bypassing the ORM) would not
    be found or cleaned up by this task. No orphan-repair pass is being
    added for MED-264 unless a concrete test/production case shows it's
    needed — normal deletes always go through `index_document_task`
    (enqueued by the relevant delete signal) and are cleaned up there.
    """
    with tenant_schema_context(tenant_schema):
        meeting_ids = list(
            Meeting.objects.filter(project_id=project_id, is_deleted=False).values_list('id', flat=True)
        )
        draft_ids = list(
            DraftProjectLink.objects.filter(project_id=project_id).values_list('draft_id', flat=True)
        )
        retrospective_ids = list(
            RetrospectiveTask.objects.filter(campaign_id=project_id)
            .exclude(status=RetrospectiveStatus.CANCELLED)
            .values_list('id', flat=True)
        )

    for meeting_id in meeting_ids:
        index_document_task.delay(tenant_schema, project_id, DocumentSourceType.MEETING, str(meeting_id))
    for draft_id in draft_ids:
        index_document_task.delay(tenant_schema, project_id, DocumentSourceType.NOTION_DRAFT, str(draft_id))
    for retrospective_id in retrospective_ids:
        index_document_task.delay(
            tenant_schema, project_id, DocumentSourceType.RETROSPECTIVE, str(retrospective_id)
        )

    logger.info(
        'RAG rebuild_project_index_task fanned out project_id=%s tenant_schema=%s meetings=%s drafts=%s '
        'retrospectives=%s',
        project_id, tenant_schema, len(meeting_ids), len(draft_ids), len(retrospective_ids),
    )


def rebuild_project_index(org_slug: str, project_id: int) -> None:
    """Public entry point for backfill/ops (management command, admin
    action). The only place `org_slug` appears — every internal task takes
    a pre-resolved `tenant_schema`, matching the repo's existing convention
    (agent.tasks, chat.tasks).
    """
    rebuild_project_index_task.delay(slug_to_schema_name(org_slug), project_id)
