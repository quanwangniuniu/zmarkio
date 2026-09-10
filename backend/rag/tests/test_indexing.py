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
    current_pipeline_hash,
    index_source_document,
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
    def _linked_draft(self, make_draft, make_draft_link, make_content_block, project):
        draft = make_draft(title='')
        make_draft_link(draft, project)
        make_content_block(draft, text=WORKED_TEXT, order=0)
        return draft

    def test_move_old_project_cleanup_then_new_project_index(
        self, make_draft, make_draft_link, make_content_block, project, project_b, mock_embed,
    ):
        draft = self._linked_draft(make_draft, make_draft_link, make_content_block, project)
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

    def test_move_new_project_index_then_old_project_cleanup_same_end_state(
        self, make_draft, make_draft_link, make_content_block, project, project_b, mock_embed,
    ):
        draft = self._linked_draft(make_draft, make_draft_link, make_content_block, project)
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
