"""
Incremental source-document indexing service (MED-264).

The one reusable entry point both signals and the rebuild/backfill path
call: `index_source_document(project_id, source_type, source_id)`. Assumes
the correct tenant schema is *already* active on the current DB connection
(see core.tenant_context.tenant_schema_context) — this service never touches
search_path itself, which keeps it directly unit-testable without Celery or
schema plumbing.

Why `project_id` is always an explicit argument, never inferred from the
source row: a hard-deleted source can't tell you its own project anymore,
and a delete signal only knows the project at the moment of deletion — so
the caller must always supply it, not this service.

Locking discipline
-------------------
EVERY operation that mutates DocumentChunk or DocumentIndexState for a given
(project, source_type, source_id) goes through the same pattern: acquire
`select_for_update()` on the DocumentIndexState row first, re-extract the
source *fresh* while holding it, and only then decide what to write. There
is no unlocked extraction anywhere in this module that leads to a mutation.

`_enter_pending` transitions `state.status` to PENDING before releasing its
lock and returning control for the (slow, external) embedding call. This is
the fact that makes the final commit checks below correct: if a task gets
back to `_reconcile_success`/`_reconcile_failure` and finds `state.status`
is COMPLETE again, that can only mean some OTHER task ran a full
`_enter_pending -> embed -> reconcile` cycle to completion in the meantime —
never a normal solo re-index (in which `state` would still show this same
task's own PENDING marker, untouched, since nobody else was racing). That's
what lets `_already_finalized_for_content` below safely bail out on ANY
already-COMPLETE state for the same content — including one committed under
a *different* pipeline hash (a rolling-deploy race between old/new worker
code) — without also blocking the legitimate case of a lone task
deliberately re-indexing unchanged content under a retuned pipeline (e.g.
after an eval-driven chunk-size change): that task's own PENDING marker is
still sitting there when it reconciles, so the guard never fires for it.

The core invariant: a task may mutate the committed index only if no
newer/different successful commit has already superseded the version (and,
transitively, whatever pipeline it was produced with) that this task
attempted. Concretely, this fires for two same-content races: (1) two tasks
computed the exact same content_hash and pipeline_hash (redundant duplicate
work, harmless but must not double-write) and (2) two tasks computed the
same content_hash under *different* pipeline_hashes (old/new code
coexisting briefly) — whichever reconciles first wins for that content; the
loser aborts rather than flapping the committed pipeline back and forth.

Why the skip decision (in `_enter_pending`) never trusts a source's own
`updated_at` alone: a child row (MeetingDocument, ContentBlock, Insight) can
change the extracted text without touching its parent's `updated_at`. Two
independent guards on DocumentIndexState must both match instead:

  - `indexed_content_hash`: sha256 of the exact text embedded last time,
    compared against a fresh extraction's hash.
  - `indexed_pipeline_hash`: fingerprint of the indexing pipeline's own
    config (embedding model/dimensions, chunk size/overlap, retrieval
    framing version, and this module's own extraction/indexing logic
    version — see `current_pipeline_hash`).

Both must match, AND status must be 'complete' — 'pending'/'failed' never
count as "nothing to do", regardless of whether the hashes happen to match.

Citation metadata freshness is independent of embedding freshness
---------------------------------------------------------------------
`content_hash` deliberately covers only the text that gets embedded — a
citation-metadata-only change (e.g. a Meeting's `scheduled_date`) does not
change that text, so it must not force a re-embed, and it must not be
frozen to whatever snapshot was current when embedding *started* either.
The governing invariant: vectors correspond to the attempted content and
pipeline hash; citation metadata reflects the freshest source state at
commit time, independently. Concretely:

  - `_reconcile_success` writes `fresh.citation_metadata` — the metadata
    from its own final, locked re-extraction — never a value carried over
    from `_enter_pending`'s earlier, pre-embedding snapshot. Metadata that
    changed *while embedding was in flight* (content unchanged throughout)
    is picked up on this same commit, not left stale until some later run.
  - `_resolve_unchanged_state` (the "nothing to embed" path) separately
    compares freshly extracted `citation_metadata` against what's actually
    stored on EVERY existing chunk for the source (not a cached hash, which
    could itself drift from ground truth, and not just one chunk, which
    would silently miss one that fell out of sync) and bulk-updates just
    `citation_metadata`/`source_updated_at` if they differ — no chunking,
    no embedding call, no provider round-trip — reporting a distinct
    `METADATA_REFRESHED` outcome.
  - If DocumentIndexState claims COMPLETE with matching hashes but zero
    DocumentChunk rows actually exist, that's an inconsistent index state,
    not "nothing changed": it forces a full re-index rather than returning
    SKIPPED_UNCHANGED for a document that in fact has nothing indexed.

Excluded-source bookkeeping
----------------------------
A source that is excluded (soft-deleted/cancelled/unassigned/reduces to no
text) or has moved to a different project is represented as
`status=COMPLETE` with `indexed_content_hash`, `indexed_pipeline_hash`, and
`indexed_source_updated_at` all explicitly reset to None — deliberately not
a separate EXCLUDED status. "Complete, nothing to index" is semantically
accurate, and a null content_hash can never equal a real extraction's hash,
so a later re-inclusion is guaranteed to look "changed" and re-index from
scratch rather than being mistaken for already up to date.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from django.conf import settings
from django.db import transaction

from rag.chunking import chunk_text
from rag.embeddings import FRAMING_VERSION, embed_document_chunks
from rag.extraction import SourceDocument, extract_source
from rag.models import DocumentChunk, DocumentIndexState, DocumentIndexStatus

logger = logging.getLogger(__name__)

# Bump whenever extraction, chunking, or this module's own indexing/
# reconciliation algorithm changes in a way not already captured by the
# settings values below (e.g. changing how Meeting text is assembled) —
# folded into current_pipeline_hash() so such a change forces a re-index of
# otherwise byte-identical source text.
INDEXING_VERSION = "1"


def current_pipeline_hash() -> str:
    """Fingerprint of everything that affects what gets stored for a chunk,
    other than the source text itself.
    """
    parts = [
        settings.RAG_EMBEDDING_MODEL,
        str(settings.RAG_EMBEDDING_DIMENSIONS),
        str(settings.RAG_CHUNK_SIZE),
        str(settings.RAG_CHUNK_OVERLAP),
        FRAMING_VERSION,
        INDEXING_VERSION,
    ]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


class IndexOutcome(str, Enum):
    INDEXED = 'indexed'
    SKIPPED_UNCHANGED = 'skipped_unchanged'
    METADATA_REFRESHED = 'metadata_refreshed'
    EXCLUDED = 'excluded'
    MOVED = 'moved'
    SUPERSEDED = 'superseded'
    FAILED = 'failed'


@dataclass
class IndexResult:
    outcome: IndexOutcome
    detail: str = ''


def _exclusion_outcome(fresh) -> IndexOutcome:
    return IndexOutcome.EXCLUDED if fresh is None else IndexOutcome.MOVED


def _already_finalized_for_content(state: DocumentIndexState, attempted_content_hash: str) -> bool:
    """True if some run already completed a full cycle for this exact
    content while we were embedding — whether it used the same pipeline as
    us (redundant duplicate) or a different one (we lost a cross-pipeline
    race). Either way our result must not overwrite theirs.

    Does NOT fire for a lone task deliberately re-indexing unchanged content
    under a retuned pipeline: in that flow `state.status` is still PENDING
    (this same task's own marker from `_enter_pending`) when it reaches
    here, not COMPLETE-by-someone-else — see module docstring.
    """
    return state.status == DocumentIndexStatus.COMPLETE and state.indexed_content_hash == attempted_content_hash


def _clear_locked(state: DocumentIndexState, project_id: int, source_type: str, source_id: str) -> None:
    """Hard-delete all chunks and reset bookkeeping to "nothing indexed".

    Caller MUST already hold select_for_update() on `state` in an open
    transaction — this function only mutates, it does not itself lock or
    re-verify anything.
    """
    DocumentChunk.objects.filter(
        project_id=project_id, source_type=source_type, source_id=source_id,
    ).delete()
    state.status = DocumentIndexStatus.COMPLETE
    state.indexed_content_hash = None
    state.indexed_pipeline_hash = None
    state.indexed_source_updated_at = None
    state.last_error = ''
    state.save()


def _resolve_unchanged_state(
    state: DocumentIndexState, project_id: int, source_type: str, source_id: str, fresh: SourceDocument,
) -> Optional[IndexResult]:
    """Called only when DocumentIndexState already claims COMPLETE with
    content_hash and pipeline_hash both matching the fresh extraction.
    Verifies that claim is actually trustworthy before agreeing nothing
    needs to change, and repairs citation_metadata drift (which
    content_hash/pipeline_hash cannot detect — see module docstring)
    without touching embeddings. Caller must already hold the
    DocumentIndexState row lock.

    Returns an IndexResult (SKIPPED_UNCHANGED or METADATA_REFRESHED) if
    genuinely up to date, or None if the claimed state turns out to be
    inconsistent (no chunks actually exist) and a full re-index must run
    instead.
    """
    stored_metadata_values = list(
        DocumentChunk.objects.filter(
            project_id=project_id, source_type=source_type, source_id=source_id,
        )
        .order_by('chunk_index')
        .values_list('citation_metadata', flat=True)
    )

    if not stored_metadata_values:
        logger.warning(
            'RAG index state inconsistency: %s:%s (project=%s) is COMPLETE with matching '
            'content/pipeline hashes but has zero DocumentChunk rows -- forcing a full re-index',
            source_type, source_id, project_id,
        )
        return None

    if any(metadata != fresh.citation_metadata for metadata in stored_metadata_values):
        DocumentChunk.objects.filter(
            project_id=project_id, source_type=source_type, source_id=source_id,
        ).update(citation_metadata=fresh.citation_metadata, source_updated_at=fresh.updated_at)
        state.indexed_source_updated_at = fresh.updated_at
        state.save(update_fields=['indexed_source_updated_at', 'updated_at'])
        return IndexResult(IndexOutcome.METADATA_REFRESHED)

    return IndexResult(IndexOutcome.SKIPPED_UNCHANGED)


@dataclass
class _PendingGate:
    """Result of `_enter_pending`. If `resolved` is set, nothing further to
    do — return it directly. Otherwise the source is confirmed current and
    `state.status` is now PENDING; `text`/`content_hash`/`pipeline_hash` are
    the exact snapshot to chunk/embed outside the transaction.

    Deliberately carries no `citation_metadata`: that would be a
    pre-embedding snapshot, and citation metadata must reflect the source's
    state at COMMIT time, not at the moment embedding started (see module
    docstring) — `_reconcile_success` re-extracts it fresh itself instead.
    """
    resolved: Optional[IndexResult] = None
    text: str = ''
    content_hash: str = ''
    pipeline_hash: str = ''


def _enter_pending(project_id: int, source_type: str, source_id: str) -> _PendingGate:
    """Locked gate: re-extract fresh, decide skip/refresh/exclude/move/proceed.

    This is the ONLY place that reads the source for the purpose of
    deciding whether to start indexing — there is no separate unlocked
    pre-check, which is what closes the race where an unlocked decision
    could act on stale information.
    """
    DocumentIndexState.objects.get_or_create(
        project_id=project_id, source_type=source_type, source_id=source_id,
    )
    with transaction.atomic():
        state = DocumentIndexState.objects.select_for_update().get(
            project_id=project_id, source_type=source_type, source_id=source_id,
        )
        fresh = extract_source(source_type, source_id)

        if fresh is None or fresh.project_id != project_id:
            _clear_locked(state, project_id, source_type, source_id)
            return _PendingGate(resolved=IndexResult(_exclusion_outcome(fresh)))

        pipeline_hash = current_pipeline_hash()
        if (
            state.status == DocumentIndexStatus.COMPLETE
            and state.indexed_content_hash == fresh.content_hash
            and state.indexed_pipeline_hash == pipeline_hash
        ):
            unchanged_result = _resolve_unchanged_state(state, project_id, source_type, source_id, fresh)
            if unchanged_result is not None:
                return _PendingGate(resolved=unchanged_result)
            # else: claimed-complete state was inconsistent (no chunks
            # actually exist) -- fall through and do a full re-index.

        # Durable, standalone commit before the slow external embedding
        # call — a crash after this point leaves visible evidence of an
        # interrupted run rather than a state indistinguishable from
        # "never touched". Also the marker that makes the reconcile-time
        # "already finalized by someone else" check meaningful (see module
        # docstring).
        state.status = DocumentIndexStatus.PENDING
        state.save(update_fields=['status', 'updated_at'])

        return _PendingGate(
            text=fresh.text,
            content_hash=fresh.content_hash,
            pipeline_hash=pipeline_hash,
        )


def _reconcile_success(
    project_id: int,
    source_type: str,
    source_id: str,
    *,
    attempted_content_hash: str,
    attempted_pipeline_hash: str,
    chunks: list[str],
    vectors: list[list[float]],
) -> IndexResult:
    if len(chunks) != len(vectors):
        # Should be unreachable: the embeddings provider layer already
        # validates batch response length before returning. This is a
        # defense-in-depth backstop against `zip()` silently truncating and
        # committing a partial/misaligned reconcile — treated as a hard bug,
        # not a normal failure, so it's raised uncaught rather than folded
        # into the FAILED bookkeeping path.
        raise RuntimeError(
            f"Embedding count mismatch for {source_type}:{source_id} (project={project_id}): "
            f"{len(chunks)} chunks vs {len(vectors)} vectors — refusing to reconcile."
        )

    with transaction.atomic():
        state = DocumentIndexState.objects.select_for_update().get(
            project_id=project_id, source_type=source_type, source_id=source_id,
        )
        fresh = extract_source(source_type, source_id)

        if fresh is None or fresh.project_id != project_id:
            _clear_locked(state, project_id, source_type, source_id)
            return IndexResult(_exclusion_outcome(fresh), 'source excluded/moved while embedding was in flight')

        if fresh.content_hash != attempted_content_hash:
            # A newer version was (or is being) embedded elsewhere. Do not
            # write these now-stale vectors and do not touch `state` — leave
            # it exactly as the winning writer left (or will leave) it.
            return IndexResult(IndexOutcome.SUPERSEDED, 'source content changed while embedding was in flight')

        if _already_finalized_for_content(state, attempted_content_hash):
            # Another run's full cycle already committed for this exact
            # content (same-version duplicate, or a different pipeline that
            # won the race first) while we were embedding. Do not re-upsert
            # or overwrite it.
            return IndexResult(
                IndexOutcome.SUPERSEDED, 'another run already committed this content while embedding was in flight'
            )

        # citation_metadata comes from THIS fresh extraction, not whatever
        # `_enter_pending` saw before embedding started — content is
        # confirmed unchanged (the hash check above), but citation metadata
        # is independent of content_hash and may have changed while
        # embedding was in flight. Vectors correspond to the attempted
        # content/pipeline; citation metadata reflects the freshest source
        # state at commit time.
        for index, (content, vector) in enumerate(zip(chunks, vectors)):
            DocumentChunk.objects.update_or_create(
                project_id=project_id,
                source_type=source_type,
                source_id=source_id,
                chunk_index=index,
                defaults={
                    'content': content,
                    'embedding': vector,
                    'citation_metadata': fresh.citation_metadata,
                    'source_updated_at': fresh.updated_at,
                    'is_deleted': False,
                },
            )
        DocumentChunk.objects.filter(
            project_id=project_id,
            source_type=source_type,
            source_id=source_id,
            chunk_index__gte=len(chunks),
        ).delete()

        state.status = DocumentIndexStatus.COMPLETE
        state.indexed_content_hash = attempted_content_hash
        state.indexed_pipeline_hash = attempted_pipeline_hash
        state.indexed_source_updated_at = fresh.updated_at
        state.last_error = ''
        state.save()

    return IndexResult(IndexOutcome.INDEXED)


def _reconcile_failure(
    project_id: int,
    source_type: str,
    source_id: str,
    *,
    attempted_content_hash: str,
    attempted_pipeline_hash: str,
    error: str,
) -> IndexResult:
    """Record a failed embedding attempt — but only if it's still relevant.

    A failure for a version/pipeline that's no longer current must never
    downgrade a newer COMPLETE state (same-version duplicate that already
    won, or a different pipeline that already won), and must never fire if
    the source became excluded/moved in the meantime.
    """
    with transaction.atomic():
        state = DocumentIndexState.objects.select_for_update().get(
            project_id=project_id, source_type=source_type, source_id=source_id,
        )
        fresh = extract_source(source_type, source_id)

        if fresh is None or fresh.project_id != project_id:
            _clear_locked(state, project_id, source_type, source_id)
            return IndexResult(_exclusion_outcome(fresh), 'source excluded/moved after a failed embedding attempt')

        if fresh.content_hash != attempted_content_hash:
            return IndexResult(
                IndexOutcome.SUPERSEDED, 'source content changed before this failure could be recorded'
            )

        if _already_finalized_for_content(state, attempted_content_hash):
            # Someone else's full cycle already committed for this exact
            # content while we were failing — our failure is moot, do not
            # downgrade their successful commit.
            return IndexResult(
                IndexOutcome.SUPERSEDED,
                'another run already committed this content before this failure could be recorded',
            )

        # Still the same content this attempt was trying to embed, and
        # nothing has superseded it — safe to record. Deliberately NOT
        # touching indexed_content_hash / indexed_pipeline_hash /
        # indexed_source_updated_at: if this is a failed *re*-index attempt,
        # those still correctly describe the last successful index, and its
        # DocumentChunk rows are untouched and still retrievable (retrieval
        # never consults DocumentIndexState.status). `attempted_pipeline_hash`
        # isn't needed by the guard above (content_hash + status is
        # sufficient and also covers the cross-pipeline case), but is kept
        # as a parameter for symmetry with `_reconcile_success` and so it's
        # available for diagnostics if `last_error` is later extended to
        # include it.
        state.status = DocumentIndexStatus.FAILED
        state.last_error = error[:2000]
        state.save(update_fields=['status', 'last_error', 'updated_at'])

    return IndexResult(IndexOutcome.FAILED, error)


def index_source_document(project_id: int, source_type: str, source_id: str) -> IndexResult:
    """Incrementally index one source document into DocumentChunk rows.

    Must be called with the correct tenant schema already active.
    """
    gate = _enter_pending(project_id, source_type, source_id)
    if gate.resolved is not None:
        return gate.resolved

    chunks = chunk_text(gate.text)
    try:
        vectors = embed_document_chunks(chunks)
    except Exception as exc:
        logger.exception(
            'RAG indexing: embedding call failed for source_type=%s source_id=%s project_id=%s',
            source_type, source_id, project_id,
        )
        return _reconcile_failure(
            project_id, source_type, source_id,
            attempted_content_hash=gate.content_hash,
            attempted_pipeline_hash=gate.pipeline_hash,
            error=str(exc),
        )

    return _reconcile_success(
        project_id, source_type, source_id,
        attempted_content_hash=gate.content_hash,
        attempted_pipeline_hash=gate.pipeline_hash,
        chunks=chunks,
        vectors=vectors,
    )
