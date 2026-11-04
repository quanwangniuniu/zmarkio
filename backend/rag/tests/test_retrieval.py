"""Tests for rag.retrieval.retrieve_chunks (MED-264 retrieval phase).

No network calls -- rag.retrieval.embed_query (the name imported into the
module under test) is monkeypatched to a fixed vector, matching the
mock_embed convention already used for indexing in conftest.py.

Vectors are fixed 768-dimensional (settings.RAG_EMBEDDING_DIMENSIONS) with
only the first couple of dimensions non-zero, so cosine distance/ordering is
hand-computable.
"""
import pytest
from django.conf import settings
from django.utils import timezone

from rag.indexing import current_pipeline_hash
from rag.models import DocumentChunk, DocumentIndexState, DocumentIndexStatus, DocumentSourceType
from rag.retrieval import retrieve_chunks

pytestmark = pytest.mark.django_db

DIMS = settings.RAG_EMBEDDING_DIMENSIONS


def _vec(*head: float) -> list[float]:
    """First len(head) dims set explicitly, rest zero-padded to DIMS."""
    return list(head) + [0.0] * (DIMS - len(head))


def _make_chunk(
    project,
    source_type=DocumentSourceType.MEETING,
    source_id='1',
    chunk_index=0,
    embedding=None,
    is_deleted=False,
    content='chunk text',
    citation_metadata=None,
):
    return DocumentChunk.objects.create(
        project=project,
        source_type=source_type,
        source_id=source_id,
        chunk_index=chunk_index,
        content=content,
        embedding=embedding,
        source_updated_at=timezone.now(),
        is_deleted=is_deleted,
        citation_metadata=citation_metadata or {},
    )


def _make_state(
    project,
    source_type=DocumentSourceType.MEETING,
    source_id='1',
    status=DocumentIndexStatus.COMPLETE,
    indexed_pipeline_hash=None,
):
    return DocumentIndexState.objects.create(
        project=project,
        source_type=source_type,
        source_id=source_id,
        status=status,
        indexed_pipeline_hash=indexed_pipeline_hash,
        indexed_content_hash='irrelevant-to-retrieval',
    )


@pytest.fixture
def mock_query_embed(monkeypatch):
    """Patches rag.retrieval.embed_query to return a fixed vector, bypassing
    Gemini entirely. Call with the vector the "query" should embed to.
    """
    def _set(vector):
        monkeypatch.setattr('rag.retrieval.embed_query', lambda text: vector)
    return _set


def test_cosine_ordering_ranks_by_similarity(project, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    close = _make_chunk(project, source_id='close', embedding=_vec(0.9, 0.1))
    far = _make_chunk(project, source_id='far', embedding=_vec(0.1, 0.9))

    results = retrieve_chunks(project.id, 'q')

    assert [r.source_id for r in results] == ['close', 'far']
    assert results[0].distance < results[1].distance


def test_top_k_truncation(project, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    for i in range(5):
        _make_chunk(project, source_id=str(i), embedding=_vec(1.0 - i * 0.1, i * 0.1))

    results = retrieve_chunks(project.id, 'q', top_k=3)

    assert len(results) == 3
    assert [r.source_id for r in results] == ['0', '1', '2']


def test_top_k_must_be_positive(project, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    _make_chunk(project, source_id='only', embedding=_vec(1.0, 0.0))

    with pytest.raises(ValueError):
        retrieve_chunks(project.id, 'q', top_k=0)

    with pytest.raises(ValueError):
        retrieve_chunks(project.id, 'q', top_k=-1)


def test_project_isolation(project, project_b, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    _make_chunk(project, source_id='mine', embedding=_vec(0.5, 0.5))
    # Closer to the query vector than 'mine', but lives in a different project.
    _make_chunk(project_b, source_id='other', embedding=_vec(1.0, 0.0))

    results = retrieve_chunks(project.id, 'q')

    assert [r.source_id for r in results] == ['mine']


def test_deleted_and_null_embedding_chunks_excluded(project, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    _make_chunk(project, source_id='deleted', embedding=_vec(1.0, 0.0), is_deleted=True)
    _make_chunk(project, source_id='null_embedding', embedding=None)
    _make_chunk(project, source_id='visible', embedding=_vec(0.5, 0.5))

    results = retrieve_chunks(project.id, 'q')

    assert [r.source_id for r in results] == ['visible']


def test_deterministic_tie_break(project, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    identical = _vec(0.5, 0.5)
    _make_chunk(project, source_type=DocumentSourceType.RETROSPECTIVE, source_id='2', embedding=identical)
    _make_chunk(project, source_type=DocumentSourceType.MEETING, source_id='1', embedding=identical)
    _make_chunk(project, source_type=DocumentSourceType.MEETING, source_id='1', chunk_index=1, embedding=identical)

    results = retrieve_chunks(project.id, 'q')

    ordering = [(r.source_type, r.source_id, r.chunk_index) for r in results]
    assert ordering == [
        (DocumentSourceType.MEETING, '1', 0),
        (DocumentSourceType.MEETING, '1', 1),
        (DocumentSourceType.RETROSPECTIVE, '2', 0),
    ]


def test_min_similarity_filters_low_similarity_results(project, mock_query_embed):
    mock_query_embed(_vec(1.0, 0.0))
    # Identical to the query vector -> distance 0.0, similarity 1.0.
    _make_chunk(project, source_id='close', embedding=_vec(1.0, 0.0))
    # Orthogonal to the query vector -> distance 1.0, similarity 0.0.
    _make_chunk(project, source_id='far', embedding=_vec(0.0, 1.0))

    results = retrieve_chunks(project.id, 'q', min_similarity=0.5)

    assert [r.source_id for r in results] == ['close']


class TestStalePipelineGuard:
    """MED-264 Problem 3: retrieve_chunks() must not compare a fresh query
    vector against chunks embedded under a different pipeline (e.g. a
    provider switch mid-rebuild) -- see the module docstring in
    rag/retrieval.py.
    """

    def test_chunk_with_matching_current_pipeline_state_is_returned(self, project, mock_query_embed):
        mock_query_embed(_vec(1.0, 0.0))
        _make_state(project, source_id='1', indexed_pipeline_hash=current_pipeline_hash())
        _make_chunk(project, source_id='1', embedding=_vec(1.0, 0.0))

        results = retrieve_chunks(project.id, 'q')

        assert [r.source_id for r in results] == ['1']

    def test_chunk_with_stale_pipeline_state_is_excluded(self, project, mock_query_embed):
        mock_query_embed(_vec(1.0, 0.0))
        _make_state(project, source_id='stale', indexed_pipeline_hash='some-old-pipeline-hash')
        _make_chunk(project, source_id='stale', embedding=_vec(1.0, 0.0))

        results = retrieve_chunks(project.id, 'q')

        assert results == []

    def test_chunk_with_null_pipeline_hash_state_is_excluded(self, project, mock_query_embed):
        """Distinct from test_chunk_without_documentindexstate_remains_retrievable
        below: here a DocumentIndexState row DOES exist for this source, but
        its indexed_pipeline_hash is NULL (the excluded/moved bookkeeping
        shape left by rag.indexing._clear_locked). A NULL hash can never
        equal current_pipeline_hash() in SQL, so this must be excluded --
        the same as any other stale/mismatched hash -- not treated as the
        no-state-at-all compatibility case.
        """
        mock_query_embed(_vec(1.0, 0.0))
        _make_state(project, source_id='cleared', indexed_pipeline_hash=None)
        _make_chunk(project, source_id='cleared', embedding=_vec(1.0, 0.0))

        results = retrieve_chunks(project.id, 'q')

        assert results == []

    def test_chunk_without_documentindexstate_remains_retrievable(self, project, mock_query_embed):
        """Compatibility path: sources indexed only via bare test fixtures
        (no DocumentIndexState row at all) keep working exactly as before --
        the real indexing pipeline always creates DocumentIndexState first,
        so this shape never masks a genuine provider/pipeline mismatch in
        production data (see rag/retrieval.py's docstring).
        """
        mock_query_embed(_vec(1.0, 0.0))
        _make_chunk(project, source_id='no-state', embedding=_vec(1.0, 0.0))

        results = retrieve_chunks(project.id, 'q')

        assert [r.source_id for r in results] == ['no-state']

    @pytest.mark.parametrize('status', [DocumentIndexStatus.PENDING, DocumentIndexStatus.FAILED])
    def test_non_complete_status_with_matching_hash_does_not_exclude(self, project, mock_query_embed, status):
        """A later re-index attempt for this same source may be PENDING or
        FAILED while the chunks on disk still reflect the last successful,
        current-pipeline index -- status must never gate eligibility, only
        indexed_pipeline_hash does (see rag.indexing's module docstring:
        retrieval never consults DocumentIndexState.status).
        """
        mock_query_embed(_vec(1.0, 0.0))
        _make_state(project, source_id='1', status=status, indexed_pipeline_hash=current_pipeline_hash())
        _make_chunk(project, source_id='1', embedding=_vec(1.0, 0.0))

        results = retrieve_chunks(project.id, 'q')

        assert [r.source_id for r in results] == ['1']

    def test_mixed_old_and_new_pipeline_chunks_only_returns_current_pipeline_ones(self, project, mock_query_embed):
        """The partial-rebuild scenario: one source already re-indexed under
        the current pipeline, another still sitting on a stale one. Only the
        current-pipeline source's chunks may enter the cosine ranking.
        """
        mock_query_embed(_vec(1.0, 0.0))
        _make_state(project, source_id='fresh', indexed_pipeline_hash=current_pipeline_hash())
        _make_chunk(project, source_id='fresh', embedding=_vec(1.0, 0.0))

        _make_state(project, source_id='stale', indexed_pipeline_hash='some-old-pipeline-hash')
        # Even though this vector is numerically closer to the query vector
        # than 'fresh' is, it must never be allowed to win the ranking --
        # it's from an incompatible embedding space, not just a worse match.
        _make_chunk(project, source_id='stale', embedding=_vec(1.0, 0.0))

        results = retrieve_chunks(project.id, 'q')

        assert [r.source_id for r in results] == ['fresh']
