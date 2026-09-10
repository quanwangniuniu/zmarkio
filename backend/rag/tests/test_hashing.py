"""Tests for content_hash (rag.extraction) and current_pipeline_hash
(rag.indexing) sensitivity (MED-264).

current_pipeline_hash() reads FRAMING_VERSION via `from rag.embeddings import
FRAMING_VERSION` inside rag/indexing.py -- that binds the name into
rag.indexing's own module namespace at import time. Patching
rag.embeddings.FRAMING_VERSION after that would not affect the already-bound
name rag.indexing.current_pipeline_hash() actually reads, so every patch
below targets `rag.indexing.FRAMING_VERSION` / `rag.indexing.INDEXING_VERSION`
directly, via `monkeypatch` (auto-restored at teardown).
"""
import pytest
from django.test import override_settings

from rag.extraction import compute_content_hash
from rag.indexing import current_pipeline_hash


@pytest.mark.unit
class TestComputeContentHash:
    def test_same_text_produces_same_hash(self):
        assert compute_content_hash('hello world') == compute_content_hash('hello world')

    def test_different_text_produces_different_hash(self):
        assert compute_content_hash('hello world') != compute_content_hash('hello there')

    def test_hash_is_a_sha256_hexdigest(self):
        digest = compute_content_hash('anything')
        assert len(digest) == 64
        assert all(c in '0123456789abcdef' for c in digest)


@pytest.mark.django_db
class TestContentHashViaExtraction:
    def test_content_hash_unaffected_by_non_semantic_field_change(self, make_meeting):
        """scheduled_date is in citation_metadata but never joined into the
        extracted text -- changing it must not change content_hash. The
        reverse direction (a semantic field change DOES change content_hash)
        is covered by test_extraction.py's
        test_meeting_document_content_change_changes_content_hash.
        """
        import datetime

        from rag.extraction import extract_meeting

        meeting = make_meeting(title='T', objective='O', summary='S')
        before = extract_meeting(meeting)

        meeting.scheduled_date = datetime.date(2026, 1, 1)
        meeting.save()
        meeting.refresh_from_db()
        after = extract_meeting(meeting)

        assert before.content_hash == after.content_hash


@pytest.mark.unit
class TestCurrentPipelineHash:
    def test_stable_across_repeated_calls_with_unchanged_settings(self):
        assert current_pipeline_hash() == current_pipeline_hash()

    def test_changing_embedding_model_changes_hash(self):
        baseline = current_pipeline_hash()
        with override_settings(RAG_EMBEDDING_MODEL='some-other-model'):
            assert current_pipeline_hash() != baseline

    def test_changing_embedding_dimensions_changes_hash(self):
        baseline = current_pipeline_hash()
        with override_settings(RAG_EMBEDDING_DIMENSIONS=1536):
            assert current_pipeline_hash() != baseline

    def test_changing_chunk_size_changes_hash(self):
        baseline = current_pipeline_hash()
        with override_settings(RAG_CHUNK_SIZE=42):
            assert current_pipeline_hash() != baseline

    def test_changing_chunk_overlap_changes_hash(self):
        baseline = current_pipeline_hash()
        with override_settings(RAG_CHUNK_OVERLAP=7):
            assert current_pipeline_hash() != baseline

    def test_changing_framing_version_changes_hash(self, monkeypatch):
        baseline = current_pipeline_hash()
        monkeypatch.setattr('rag.indexing.FRAMING_VERSION', '2')
        assert current_pipeline_hash() != baseline

    def test_changing_indexing_version_changes_hash(self, monkeypatch):
        baseline = current_pipeline_hash()
        monkeypatch.setattr('rag.indexing.INDEXING_VERSION', '2')
        assert current_pipeline_hash() != baseline

    def test_unrelated_setting_does_not_change_hash(self):
        """RAG_EMBED_BATCH_SIZE affects request batching only, not what gets
        stored per chunk -- current_pipeline_hash() deliberately excludes it.
        """
        baseline = current_pipeline_hash()
        with override_settings(RAG_EMBED_BATCH_SIZE=999):
            assert current_pipeline_hash() == baseline
