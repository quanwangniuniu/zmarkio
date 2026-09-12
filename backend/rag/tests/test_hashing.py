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
import hashlib

import pytest
from django.conf import settings
from django.test import override_settings

import rag.indexing as rag_indexing
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
        """RAG_EMBEDDING_MODEL is a Gemini-specific setting -- it only feeds
        active_embedding_identity() when RAG_EMBEDDING_PROVIDER is 'gemini',
        so the provider must be pinned here for this override to have any
        effect on the hash (see test_switching_provider_gemini_to_local_changes_hash
        for the 'local' provider reading RAG_LOCAL_EMBEDDING_MODEL instead).
        """
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='gemini-embedding-2'):
            baseline = current_pipeline_hash()
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='some-other-model'):
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

    def test_gemini_provider_preserves_legacy_hash_formula(self):
        """Byte-for-byte compatibility check: for provider == 'gemini',
        current_pipeline_hash() must still equal the pre-fix formula that
        hashed settings.RAG_EMBEDDING_MODEL directly (unprefixed) -- so
        deploying this fix while production stays on the same Gemini model
        does not invalidate any existing DocumentIndexState row.
        """
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='gemini-embedding-2'):
            legacy_parts = [
                'gemini-embedding-2',  # bare model name, no 'gemini:' prefix
                str(settings.RAG_EMBEDDING_DIMENSIONS),
                str(settings.RAG_CHUNK_SIZE),
                str(settings.RAG_CHUNK_OVERLAP),
                rag_indexing.FRAMING_VERSION,
                rag_indexing.INDEXING_VERSION,
            ]
            legacy_hash = hashlib.sha256('|'.join(legacy_parts).encode('utf-8')).hexdigest()
            assert current_pipeline_hash() == legacy_hash

    def test_changing_gemini_model_changes_hash(self):
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='gemini-embedding-2'):
            baseline = current_pipeline_hash()
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='gemini-embedding-3'):
            assert current_pipeline_hash() != baseline

    def test_switching_provider_gemini_to_local_changes_hash(self):
        """The Problem 3 regression: a provider switch used to be invisible
        to the pipeline hash whenever RAG_EMBEDDING_MODEL itself didn't
        change (it never does when flipping to 'local', since that provider
        reads RAG_LOCAL_EMBEDDING_MODEL instead).
        """
        with override_settings(RAG_EMBEDDING_PROVIDER='gemini', RAG_EMBEDDING_MODEL='gemini-embedding-2'):
            gemini_hash = current_pipeline_hash()
        with override_settings(
            RAG_EMBEDDING_PROVIDER='local', RAG_LOCAL_EMBEDDING_MODEL='BAAI/bge-base-en-v1.5',
        ):
            local_hash = current_pipeline_hash()
        assert gemini_hash != local_hash

    def test_changing_local_model_changes_hash(self):
        with override_settings(RAG_EMBEDDING_PROVIDER='local', RAG_LOCAL_EMBEDDING_MODEL='BAAI/bge-base-en-v1.5'):
            baseline = current_pipeline_hash()
        with override_settings(RAG_EMBEDDING_PROVIDER='local', RAG_LOCAL_EMBEDDING_MODEL='BAAI/bge-small-en-v1.5'):
            assert current_pipeline_hash() != baseline
