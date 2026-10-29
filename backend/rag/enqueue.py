"""
Shared RAG indexing dispatch used by app signals.

Receivers stay in each app because exclusion rules differ (a deleted
meeting, a cancelled retrospective, an unlinked draft). What must not
drift is the reliability contract:

1. Mark the source dirty synchronously, before the Celery publish.
2. Enqueue ``index_document_task`` on commit, and swallow broker errors so
   a failed publish cannot fail the business request. ``dirty_since`` plus
   the periodic reconciler is the backstop.
3. Optional synchronous cleanup is the same: DB-only, exceptions logged,
   never propagated.

``tenant_schema`` is captured at call time (or passed in when one request
enqueues several sources and must keep a single schema). The worker cannot
inherit the request's search_path.
"""
from __future__ import annotations

import logging

from django.db import transaction

from core.tenant_context import current_tenant_schema

logger = logging.getLogger(__name__)


def enqueue_source_index(project_id: int, source_type: str, source_id, tenant_schema: str | None = None) -> None:
    """Mark one source dirty and enqueue its index task after commit."""
    from rag.indexing import mark_source_dirty

    source_key = str(source_id)
    try:
        mark_source_dirty(project_id, source_type, source_key)
    except Exception:
        logger.exception('Failed to mark RAG state dirty for %s %s', source_type, source_key)

    schema = tenant_schema or current_tenant_schema()

    def enqueue() -> None:
        try:
            from rag.tasks import index_document_task
            index_document_task.delay(
                tenant_schema=schema,
                project_id=project_id,
                source_type=source_type,
                source_id=source_key,
            )
        except Exception:
            logger.exception('Failed to enqueue RAG indexing for %s %s', source_type, source_key)

    transaction.on_commit(enqueue)


def clear_source_now(project_id: int, source_type: str, source_id) -> None:
    """DB-only fast path for a delete or exclusion. Failures stay here."""
    from rag.indexing import clear_source_index

    try:
        clear_source_index(project_id, source_type, str(source_id))
    except Exception:
        logger.exception('Failed synchronous RAG cleanup for %s %s', source_type, source_id)
