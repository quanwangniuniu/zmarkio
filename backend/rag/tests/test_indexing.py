"""Tests for rag.indexing.index_source_document — the full incremental
indexing state machine (MED-264).

All DB access runs under pytest-django's normal per-test transaction; no
test needs `transaction=True`. The "concurrent winner" race is proven by
calling the locked helpers (`_enter_pending` / `_reconcile_success` /
`_reconcile_failure`) directly in sequence within one test, not by spinning
up real threads -- the invariant is about what those helpers do when they
observe a given DocumentIndexState, not about wall-clock timing.

`embed_document_chunks` is always mocked via the `mock_embed` fixture
(conftest.py) -- nothing here ever calls Gemini. Any vector list actually
written to a DocumentChunk row must be sized to
settings.RAG_EMBEDDING_DIMENSIONS -- pgvector's VectorField enforces that
width at the column level, so an under/oversized literal list would raise
for the wrong reason (a dimension error, not the invariant under test).
"""
import datetime

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from rag.chunking import chunk_text
from rag.extraction import compute_content_hash
from rag.indexing import (
    IndexOutcome,
    _enter_pending,
    _reconcile_failure,
    _reconcile_success,
    clear_source_index,
    current_pipeline_hash,
    index_source_document,
    mark_source_dirty,
)
from rag.models import DocumentChunk, DocumentIndexState, DocumentIndexStatus, DocumentSourceType

pytestmark = pytest.mark.django_db

# chunk_size=10, overlap=3 -> step=7 (matches test_chunking.py's worked example).
WORKED_TEXT = 'ABCDEFGHIJKLMNOPQRSTUVWXY'  # 25 chars -> 4 chunks
WORKED_CHUNKS = ['ABCDEFGHIJ', 'HIJKLMNOPQ', 'OPQRSTUVWX', 'VWXY']
SHRUNK_TEXT = 'ABCDEFGHIJ'  # exactly chunk_size=10 -> 1 chunk
REPLACEMENT_TEXT = 'ZYXWVUTSRQPONMLKJIHGFEDCBA'  # 26 chars, entirely different content


def _bare_meeting(make_meeting, make_meeting_document, text=WORKED_TEXT, **kwargs):
    """A meeting whose extracted text is exactly `text` -- empty
    title/objective/summary so `_join_nonempty` leaves only the document
    content, making chunk boundaries predictable by hand.
    """
    meeting = make_meeting(title='', objective='', summary='', **kwargs)
    make_meeting_document(meeting, content=text)
    return meeting


def _linked_draft(make_draft, make_draft_link, make_content_block, project):
    draft = make_draft(title='')
    make_draft_link(draft, project)
    make_content_block(draft, text=WORKED_TEXT, order=0)
    return draft


def _vector(dims=None):
    return [0.0] * (dims or settings.RAG_EMBEDDING_DIMENSIONS)


def _assert_state_cleared(project, source_type, source_id):
    """Full assertion set for the EXCLUDED/MOVED "nothing indexed" state:
    all chunks gone, and every hash/observability field explicitly reset --
    not merely "some chunks are missing."
    """
    assert not DocumentChunk.objects.filter(
        project=project, source_type=source_type, source_id=source_id,
    ).exists()
    state = DocumentIndexState.objects.get(
        project=project, source_type=source_type, source_id=source_id,
    )
    assert state.status == DocumentIndexStatus.COMPLETE
    assert state.indexed_content_hash is None
    assert state.indexed_pipeline_hash is None
    assert state.indexed_source_updated_at is None
    assert state.last_error == ''
    # MED-264 Problem 7: confirmed-exclusion is exactly when a dirty marker
    # (if any) is allowed to clear -- see _clear_locked's docstring.
    assert state.dirty_since is None


@pytest.mark.usefixtures('small_pipeline_settings')
class TestIndexSourceDocumentOutcomes:
    def test_first_index_returns_indexed(self, make_meeting, make_meeting_document, project, mock_embed):
        meeting = _bare_meeting(make_meeting, make_meeting_document)

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.INDEXED
        assert mock_embed.call_count == 1

        chunks = list(
            DocumentChunk.objects.filter(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            ).order_by('chunk_index')
        )
        assert [c.chunk_index for c in chunks] == [0, 1, 2, 3]
        assert [c.content for c in chunks] == WORKED_CHUNKS

        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.status == DocumentIndexStatus.COMPLETE
        assert state.indexed_content_hash == compute_content_hash(WORKED_TEXT)
        assert state.indexed_pipeline_hash == current_pipeline_hash()

    def test_second_identical_run_is_skipped_unchanged(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.SKIPPED_UNCHANGED
        assert mock_embed.call_count == 1  # not called again

    def test_content_change_triggers_reindex(self, make_meeting, make_meeting_document, project, mock_embed):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        document = meeting.document
        document.content = REPLACEMENT_TEXT
        document.save()

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.INDEXED
        assert mock_embed.call_count == 2
        contents = list(
            DocumentChunk.objects.filter(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            ).order_by('chunk_index').values_list('content', flat=True)
        )
        assert contents != WORKED_CHUNKS

    def test_pipeline_change_triggers_reindex_with_unchanged_content(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert mock_embed.call_count == 1

        with override_settings(RAG_CHUNK_SIZE=5, RAG_CHUNK_OVERLAP=1):
            # Computed and asserted INSIDE the override so this checks the
            # hash actually written under the temporary pipeline config, not
            # whatever current_pipeline_hash() would be after settings are
            # restored on context exit.
            expected_pipeline_hash = current_pipeline_hash()

            result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

            assert result.outcome == IndexOutcome.INDEXED
            assert mock_embed.call_count == 2
            chunk_lengths = list(
                DocumentChunk.objects.filter(
                    project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
                ).values_list('content', flat=True)
            )
            assert all(len(c) <= 5 for c in chunk_lengths)

            state = DocumentIndexState.objects.get(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            )
            assert state.indexed_pipeline_hash == expected_pipeline_hash

    def test_changed_provider_prevents_unchanged_source_from_being_skipped(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        """MED-264 Problem 3. Note what this test does and does NOT prove:
        switching RAG_EMBEDDING_PROVIDER does not itself enqueue anything --
        re-indexing still only happens when something calls
        index_source_document() (a source save/update signal, or an
        explicit rebuild). What it must prove is narrower: once indexing IS
        triggered again for a source whose content hasn't changed, a
        different embedding provider/model identity must stop
        `_enter_pending` from treating it as SKIPPED_UNCHANGED -- neither the
        source text nor RAG_EMBEDDING_MODEL itself changes when switching to
        'local', which was exactly the gap (see
        rag.embeddings.active_embedding_identity).

        `mock_embed` patches rag.indexing.embed_document_chunks -- the name
        imported from rag.embeddings, the shared entry point both providers
        dispatch through via _provider() -- so the call_count assertions
        below hold regardless of which provider is configured, and switching
        to 'local' here never touches the real fastembed/ONNX model.
        """
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='gemini-embedding-2'):
            meeting = _bare_meeting(make_meeting, make_meeting_document)
            index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert mock_embed.call_count == 1

        with override_settings(RAG_EMBEDDING_PROVIDER='local', RAG_LOCAL_EMBEDDING_MODEL='BAAI/bge-base-en-v1.5'):
            expected_pipeline_hash = current_pipeline_hash()

            result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

            assert result.outcome == IndexOutcome.INDEXED
            assert mock_embed.call_count == 2

            state = DocumentIndexState.objects.get(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            )
            assert state.indexed_pipeline_hash == expected_pipeline_hash

    def test_source_shrinking_removes_exact_stale_tail_chunks(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document, text=WORKED_TEXT)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert list(
            DocumentChunk.objects.filter(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            ).order_by('chunk_index').values_list('chunk_index', flat=True)
        ) == [0, 1, 2, 3]

        document = meeting.document
        document.content = SHRUNK_TEXT  # -> exactly 1 chunk now
        document.save()

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.INDEXED
        remaining = list(
            DocumentChunk.objects.filter(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            ).order_by('chunk_index').values_list('chunk_index', flat=True)
        )
        # Not just a count check: the exact surviving index set must be [0] --
        # chunk_index 1/2/3's rows must be gone, not merely outnumbered.
        assert remaining == [0]
        assert DocumentChunk.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id), chunk_index=0,
        ).content == SHRUNK_TEXT

    def test_excluded_source_removes_chunks_and_resets_state(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        meeting.is_deleted = True
        meeting.save()

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.EXCLUDED
        _assert_state_cleared(project, DocumentSourceType.MEETING, str(meeting.id))

    def test_metadata_only_change_refreshes_without_embedding_call(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert mock_embed.call_count == 1

        meeting.scheduled_date = datetime.date(2026, 6, 1)  # not part of extracted text
        meeting.save()

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.METADATA_REFRESHED
        assert mock_embed.call_count == 1  # no re-embed
        chunk = DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).order_by('chunk_index').first()
        assert chunk.content == WORKED_CHUNKS[0]  # content untouched
        assert chunk.citation_metadata['scheduled_date'] == '2026-06-01'

    def test_complete_state_with_zero_chunks_repairs_via_full_reindex(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert mock_embed.call_count == 1

        # Simulate an inconsistent index: state still claims COMPLETE with
        # matching hashes, but the chunk rows are gone (e.g. a direct DB
        # deletion bypassing index_source_document).
        DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).delete()

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.INDEXED
        assert mock_embed.call_count == 2
        assert DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).count() == 4

    def test_embedding_failure_returns_failed_and_preserves_prior_chunks(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        original_hash = compute_content_hash(WORKED_TEXT)

        document = meeting.document
        document.content = REPLACEMENT_TEXT
        document.save()
        mock_embed.side_effect = RuntimeError('gemini boom')

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.FAILED
        assert 'gemini boom' in result.detail

        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.status == DocumentIndexStatus.FAILED
        assert 'gemini boom' in state.last_error
        # Old chunks/hashes describing the last SUCCESSFUL index are untouched.
        assert state.indexed_content_hash == original_hash
        assert list(
            DocumentChunk.objects.filter(
                project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
            ).order_by('chunk_index').values_list('content', flat=True)
        ) == WORKED_CHUNKS

    def test_stale_content_during_reconcile_returns_superseded(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)

        def mutate_then_embed(texts):
            # Simulate another writer changing the source while THIS
            # embedding call is still in flight.
            from meetings.models import MeetingDocument
            MeetingDocument.objects.filter(meeting=meeting).update(content=REPLACEMENT_TEXT)
            return [_vector() for _ in texts]
        mock_embed.side_effect = mutate_then_embed

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.SUPERSEDED
        assert not DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).exists()
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        # _reconcile_success leaves state exactly as _enter_pending set it.
        assert state.status == DocumentIndexStatus.PENDING
        assert state.indexed_content_hash is None

    def test_vector_chunk_count_mismatch_refuses_partial_reconcile(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        # Correctly-DIMENSIONED vectors, but one SHORT on count -- this must
        # fail because len(chunks) != len(vectors), not because of a vector
        # width mismatch (which would raise for the wrong reason).
        mock_embed.side_effect = lambda texts: [_vector() for _ in texts[:-1]]

        with pytest.raises(RuntimeError):
            index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert not DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).exists()
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.status == DocumentIndexStatus.PENDING


@pytest.mark.usefixtures('small_pipeline_settings')
class TestConcurrentWinnerInvariant:
    def test_same_content_winner_prevents_later_failure_from_downgrading_complete(
        self, make_meeting, make_meeting_document, project,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)

        gate = _enter_pending(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert gate.resolved is None
        chunks = chunk_text(gate.text)
        vectors = [_vector() for _ in chunks]

        winner = _reconcile_success(
            project.id, DocumentSourceType.MEETING, str(meeting.id),
            attempted_content_hash=gate.content_hash, attempted_pipeline_hash=gate.pipeline_hash,
            chunks=chunks, vectors=vectors,
        )
        assert winner.outcome == IndexOutcome.INDEXED

        # A duplicate concurrent worker that computed the SAME content_hash
        # but failed its own embed call, arriving here after the winner
        # already committed.
        loser = _reconcile_failure(
            project.id, DocumentSourceType.MEETING, str(meeting.id),
            attempted_content_hash=gate.content_hash, attempted_pipeline_hash=gate.pipeline_hash,
            error='late duplicate-worker failure',
        )

        assert loser.outcome == IndexOutcome.SUPERSEDED
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.status == DocumentIndexStatus.COMPLETE  # not downgraded to FAILED
        assert state.indexed_content_hash == gate.content_hash
        assert state.last_error == ''


@pytest.mark.usefixtures('small_pipeline_settings')
class TestProjectIsolation:
    def test_exclusion_cleanup_never_touches_another_projects_rows(self, project, project_b):
        """Seed identically-keyed DocumentChunk/DocumentIndexState rows under
        two different projects for a source_id that doesn't correspond to any
        real Meeting row (extract_source will report it as gone either way),
        then confirm excluding it under one project leaves the other's rows
        completely untouched. The logical identity is
        (project, source_type, source_id) -- every query below filters on
        all three, not just project + source_id.
        """
        fake_source_id = '999999'
        now = timezone.now()
        for proj in (project, project_b):
            DocumentChunk.objects.create(
                project=proj, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
                chunk_index=0, content='seed', embedding=None, source_updated_at=now,
                citation_metadata={'title': 'seed'},
            )
            DocumentIndexState.objects.create(
                project=proj, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
                status=DocumentIndexStatus.COMPLETE,
                indexed_content_hash='deadbeef', indexed_pipeline_hash='deadbeef',
            )

        result = index_source_document(project.id, DocumentSourceType.MEETING, fake_source_id)

        assert result.outcome == IndexOutcome.EXCLUDED
        _assert_state_cleared(project, DocumentSourceType.MEETING, fake_source_id)

        # project_b's identically-keyed rows are untouched.
        assert DocumentChunk.objects.filter(
            project=project_b, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
        ).count() == 1
        state_b = DocumentIndexState.objects.get(
            project=project_b, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
        )
        assert state_b.indexed_content_hash == 'deadbeef'


@pytest.mark.usefixtures('small_pipeline_settings')
class TestDraftOwnershipMovement:
    def test_move_old_project_cleanup_then_new_project_index(
        self, make_draft, make_draft_link, make_content_block, project, project_b, mock_embed,
    ):
        draft = _linked_draft(make_draft, make_draft_link, make_content_block, project)
        first = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert first.outcome == IndexOutcome.INDEXED

        link = draft.project_link
        link.project = project_b
        link.save()

        old_result = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        new_result = index_source_document(project_b.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))

        assert old_result.outcome == IndexOutcome.MOVED
        assert new_result.outcome == IndexOutcome.INDEXED
        _assert_state_cleared(project, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert DocumentChunk.objects.filter(
            project=project_b, source_type=DocumentSourceType.NOTION_DRAFT, source_id=str(draft.id),
        ).count() == 4


@pytest.mark.usefixtures('small_pipeline_settings')
class TestMarkSourceDirty:
    """rag.indexing.mark_source_dirty (MED-264 Problem 7)."""

    def test_marks_a_brand_new_row_dirty(self, project):
        assert not DocumentIndexState.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id='no-row-yet',
        ).exists()

        mark_source_dirty(project.id, DocumentSourceType.MEETING, 'no-row-yet')

        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id='no-row-yet',
        )
        assert state.dirty_since is not None
        # A freshly created row must not look like a completed/authoritative
        # index -- mark_source_dirty only ever adds information.
        assert state.status == DocumentIndexStatus.PENDING
        assert state.indexed_content_hash is None
        assert state.indexed_pipeline_hash is None

    def test_marks_an_existing_clean_row_dirty(self, make_meeting, make_meeting_document, project, mock_embed):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.dirty_since is None  # clean after a successful index

        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))

        state.refresh_from_db()
        assert state.dirty_since is not None
        # Marking dirty must never touch the hashes/status a successful
        # index just committed -- it only adds a "not yet reconciled" flag.
        assert state.status == DocumentIndexStatus.COMPLETE
        assert state.indexed_content_hash is not None

    def test_marking_dirty_twice_preserves_the_earliest_timestamp(self, project):
        mark_source_dirty(project.id, DocumentSourceType.MEETING, 'm1')
        first = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id='m1',
        ).dirty_since
        assert first is not None

        mark_source_dirty(project.id, DocumentSourceType.MEETING, 'm1')

        second = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id='m1',
        ).dirty_since
        # Not merely "still set" -- the EARLIEST unresolved timestamp must
        # survive a second, later mark, since that's what the reconciler's
        # staleness threshold measures against.
        assert second == first


@pytest.mark.usefixtures('small_pipeline_settings')
class TestDirtyClearingIntegration:
    """Dirty-marker clearing must only ever happen at a genuinely finalized
    commit point (MED-264 Problem 7) -- never merely because SOME attempt
    finished.
    """

    def test_successful_reconcile_clears_dirty_since(self, make_meeting, make_meeting_document, project, mock_embed):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.INDEXED
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.dirty_since is None

    def test_failed_indexing_does_not_clear_dirty_since(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))  # first success

        document = meeting.document
        document.content = REPLACEMENT_TEXT
        document.save()
        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))
        mock_embed.side_effect = RuntimeError('boom')

        result = index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert result.outcome == IndexOutcome.FAILED
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.status == DocumentIndexStatus.FAILED
        assert state.dirty_since is not None  # still owed -- nothing reconciled it

    def test_superseded_failure_run_does_not_clear_a_newer_mutations_dirty_marker(
        self, make_meeting, make_meeting_document, project,
    ):
        """Mirrors TestConcurrentWinnerInvariant's winner/loser construction,
        but asserts on dirty_since instead of status/indexed_content_hash: a
        losing/obsolete FAILED reconcile call for OLD content must not clear
        a dirty marker that was set by a NEWER mutation after the winner
        already committed.
        """
        meeting = _bare_meeting(make_meeting, make_meeting_document)

        gate = _enter_pending(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert gate.resolved is None
        chunks = chunk_text(gate.text)
        vectors = [_vector() for _ in chunks]

        winner = _reconcile_success(
            project.id, DocumentSourceType.MEETING, str(meeting.id),
            attempted_content_hash=gate.content_hash, attempted_pipeline_hash=gate.pipeline_hash,
            chunks=chunks, vectors=vectors,
        )
        assert winner.outcome == IndexOutcome.INDEXED

        # A newer mutation happens AFTER the winner committed -- e.g. the
        # real signal-layer mark_source_dirty call for a subsequent edit
        # that hasn't been embedded yet.
        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))
        newer_dirty_since = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).dirty_since
        assert newer_dirty_since is not None

        # A late/obsolete duplicate worker for the OLD (already-superseded)
        # content_hash fails and arrives here after the fact.
        loser = _reconcile_failure(
            project.id, DocumentSourceType.MEETING, str(meeting.id),
            attempted_content_hash=gate.content_hash, attempted_pipeline_hash=gate.pipeline_hash,
            error='late duplicate-worker failure',
        )

        assert loser.outcome == IndexOutcome.SUPERSEDED
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.dirty_since == newer_dirty_since  # untouched, not cleared

    def test_superseded_success_run_does_not_clear_a_newer_mutations_dirty_marker(
        self, make_meeting, make_meeting_document, project,
    ):
        """Same shape as the SUPERSEDED-via-failure test above, but for the
        OTHER path that can return SUPERSEDED: a duplicate SUCCESSFUL
        reconcile for already-finalized OLD content (e.g. a retried/
        duplicate Celery delivery of the same original task) arriving after
        a newer mutation has already been marked dirty must not clear that
        newer dirty marker either -- _already_finalized_for_content must
        short-circuit before ever reaching the dirty_since = None line.
        """
        meeting = _bare_meeting(make_meeting, make_meeting_document)

        gate = _enter_pending(project.id, DocumentSourceType.MEETING, str(meeting.id))
        assert gate.resolved is None
        chunks = chunk_text(gate.text)
        vectors = [_vector() for _ in chunks]

        winner = _reconcile_success(
            project.id, DocumentSourceType.MEETING, str(meeting.id),
            attempted_content_hash=gate.content_hash, attempted_pipeline_hash=gate.pipeline_hash,
            chunks=chunks, vectors=vectors,
        )
        assert winner.outcome == IndexOutcome.INDEXED

        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))
        newer_dirty_since = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).dirty_since
        assert newer_dirty_since is not None

        # Late duplicate SUCCESSFUL reconcile for the same (now stale,
        # already-committed) content_hash and vectors.
        loser = _reconcile_success(
            project.id, DocumentSourceType.MEETING, str(meeting.id),
            attempted_content_hash=gate.content_hash, attempted_pipeline_hash=gate.pipeline_hash,
            chunks=chunks, vectors=vectors,
        )

        assert loser.outcome == IndexOutcome.SUPERSEDED
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.dirty_since == newer_dirty_since  # untouched, not cleared


@pytest.mark.usefixtures('small_pipeline_settings')
class TestClearSourceIndex:
    """rag.indexing.clear_source_index -- the synchronous, DB-only fast path
    signal handlers call for delete/exclusion/move-away events (MED-264
    Problem 7). See its own docstring for why it re-verifies via a fresh
    extract_source() call while holding the lock rather than trusting the
    caller's belief.
    """

    def test_genuine_exclusion_removes_chunks_and_clears_dirty_since(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        meeting.is_deleted = True
        meeting.save()
        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))

        clear_source_index(project.id, DocumentSourceType.MEETING, str(meeting.id))

        _assert_state_cleared(project, DocumentSourceType.MEETING, str(meeting.id))

    def test_no_prior_row_is_safe_and_creates_a_clean_state(self, project):
        # No DocumentChunk/DocumentIndexState exists at all for this source
        # -- e.g. a delete signal firing for a source that was never
        # actually indexed. Not a literal no-op: get_or_create still creates
        # a DocumentIndexState row (via the same lock-acquisition path every
        # other rag.indexing mutator uses) -- but nothing is deleted and the
        # resulting row is in a clean, cleared shape, not a dirty one.
        # A numeric string -- Meeting's PK is an integer AutoField, and
        # production source_ids are always str(real_pk), never arbitrary text.
        never_indexed_id = '888888'
        clear_source_index(project.id, DocumentSourceType.MEETING, never_indexed_id)

        assert not DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=never_indexed_id,
        ).exists()
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=never_indexed_id,
        )
        assert state.status == DocumentIndexStatus.COMPLETE
        assert state.indexed_content_hash is None
        assert state.dirty_since is None

    def test_stale_clear_does_not_delete_a_currently_valid_index(
        self, make_meeting, make_meeting_document, project, mock_embed,
    ):
        """Regression test for the race identified before implementation:
        a delayed, stale clear_source_index call for a condition that no
        longer holds (e.g. queued when a source looked excluded/moved-away,
        but the lock wasn't granted until AFTER a newer mutation already
        re-established it) must be a no-op, not a destructive clear.

        Here: a Meeting is currently valid, indexed, and has a pending
        (newer) dirty marker -- exactly the state a stale caller could
        observe an OLD exclusion belief against. clear_source_index must
        re-verify fresh under the lock and refuse to touch it.
        """
        meeting = _bare_meeting(make_meeting, make_meeting_document)
        index_source_document(project.id, DocumentSourceType.MEETING, str(meeting.id))
        mark_source_dirty(project.id, DocumentSourceType.MEETING, str(meeting.id))
        dirty_since_before = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).dirty_since

        clear_source_index(project.id, DocumentSourceType.MEETING, str(meeting.id))

        assert DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        ).count() == len(WORKED_CHUNKS)
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.MEETING, source_id=str(meeting.id),
        )
        assert state.status == DocumentIndexStatus.COMPLETE
        assert state.indexed_content_hash is not None
        assert state.dirty_since == dirty_since_before  # NOT cleared

    def test_draft_moved_a_to_b_to_a_stale_old_project_clear_does_not_delete_reestablished_index(
        self, make_draft, make_draft_link, make_content_block, project, project_b, mock_embed,
    ):
        """The exact DraftProjectLink A -> B -> A scenario from the design
        review: a delayed clear_source_index(A, ...) call from the FIRST
        move (A -> B) must not destroy chunks that a SECOND, later move
        (B -> A) has since legitimately re-established for project A.
        """
        draft = make_draft(title='')
        link = make_draft_link(draft, project)
        make_content_block(draft, text=WORKED_TEXT, order=0)

        # Move 1: A -> B.
        first = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert first.outcome == IndexOutcome.INDEXED
        link.project = project_b
        link.save()
        old_side_of_move_1 = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert old_side_of_move_1.outcome == IndexOutcome.MOVED
        new_side_of_move_1 = index_source_document(project_b.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert new_side_of_move_1.outcome == IndexOutcome.INDEXED

        # Move 2: B -> A (the causally-later, legitimate re-establishment of
        # project A's index for this draft).
        link.project = project
        link.save()
        old_side_of_move_2 = index_source_document(project_b.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert old_side_of_move_2.outcome == IndexOutcome.MOVED
        new_side_of_move_2 = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert new_side_of_move_2.outcome == IndexOutcome.INDEXED
        assert DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.NOTION_DRAFT, source_id=str(draft.id),
        ).count() == 4

        # The delayed, now-stale synchronous cleanup call from move 1's OLD
        # side (project A) finally "acquires the lock" here -- project A is
        # valid again by this point, so this must no-op.
        clear_source_index(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))

        assert DocumentChunk.objects.filter(
            project=project, source_type=DocumentSourceType.NOTION_DRAFT, source_id=str(draft.id),
        ).count() == 4
        state = DocumentIndexState.objects.get(
            project=project, source_type=DocumentSourceType.NOTION_DRAFT, source_id=str(draft.id),
        )
        assert state.status == DocumentIndexStatus.COMPLETE
        assert state.indexed_content_hash is not None

    def test_never_touches_another_projects_rows(self, project, project_b):
        fake_source_id = '999999'
        now = timezone.now()
        for proj in (project, project_b):
            DocumentChunk.objects.create(
                project=proj, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
                chunk_index=0, content='seed', embedding=None, source_updated_at=now,
                citation_metadata={'title': 'seed'},
            )
            DocumentIndexState.objects.create(
                project=proj, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
                status=DocumentIndexStatus.COMPLETE,
                indexed_content_hash='deadbeef', indexed_pipeline_hash='deadbeef',
            )

        clear_source_index(project.id, DocumentSourceType.MEETING, fake_source_id)

        _assert_state_cleared(project, DocumentSourceType.MEETING, fake_source_id)
        assert DocumentChunk.objects.filter(
            project=project_b, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
        ).count() == 1
        state_b = DocumentIndexState.objects.get(
            project=project_b, source_type=DocumentSourceType.MEETING, source_id=fake_source_id,
        )
        assert state_b.indexed_content_hash == 'deadbeef'

    def test_move_new_project_index_then_old_project_cleanup_same_end_state(
        self, make_draft, make_draft_link, make_content_block, project, project_b, mock_embed,
    ):
        draft = _linked_draft(make_draft, make_draft_link, make_content_block, project)
        first = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert first.outcome == IndexOutcome.INDEXED

        link = draft.project_link
        link.project = project_b
        link.save()

        # Reverse order vs. the sibling test above.
        new_result = index_source_document(project_b.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        old_result = index_source_document(project.id, DocumentSourceType.NOTION_DRAFT, str(draft.id))

        assert new_result.outcome == IndexOutcome.INDEXED
        assert old_result.outcome == IndexOutcome.MOVED
        _assert_state_cleared(project, DocumentSourceType.NOTION_DRAFT, str(draft.id))
        assert DocumentChunk.objects.filter(
            project=project_b, source_type=DocumentSourceType.NOTION_DRAFT, source_id=str(draft.id),
        ).count() == 4
