"""
Tests for the RAG_EMBEDDING_PROVIDER dispatch in rag/embeddings.py.

No DB access needed -- embed_document_chunks/embed_query only route to
core.services.ollama_embeddings, they never touch the database themselves.
That module is mocked here; Ollama is never actually called.

@override_settings decorates each test function individually rather than a
test class -- Django only allows class-level override_settings on
SimpleTestCase subclasses, and these are plain pytest functions.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from rag import embeddings


@override_settings(
    RAG_EMBEDDING_PROVIDER='ollama',
    RAG_OLLAMA_EMBEDDING_MODEL='nomic-embed-text',
    RAG_EMBEDDING_DIMENSIONS=768,
)
def test_ollama_dispatch_embed_document_chunks_calls_ollama_with_configured_model():
    with patch('rag.embeddings.ollama_embeddings.batch_embed_contents') as mock_batch:
        mock_batch.return_value = [[0.1] * 768]

        result = embeddings.embed_document_chunks(['some chunk text'])

        assert result == [[0.1] * 768]
        mock_batch.assert_called_once_with(
            [embeddings._DOCUMENT_INSTRUCTION + 'some chunk text'],
            model='nomic-embed-text',
            dimensions=768,
        )


@override_settings(
    RAG_EMBEDDING_PROVIDER='ollama',
    RAG_OLLAMA_EMBEDDING_MODEL='nomic-embed-text',
    RAG_EMBEDDING_DIMENSIONS=768,
)
def test_ollama_dispatch_embed_query_calls_ollama_with_configured_model():
    with patch('rag.embeddings.ollama_embeddings.embed_content') as mock_embed:
        mock_embed.return_value = [0.2] * 768

        result = embeddings.embed_query('what is the budget?')

        assert result == [0.2] * 768
        mock_embed.assert_called_once_with(
            embeddings._QUERY_INSTRUCTION + 'what is the budget?',
            model='nomic-embed-text',
            dimensions=768,
        )


def test_document_and_query_framing_strings_are_stable():
    """The framing strings themselves must not depend on provider -- only
    the destination module does. Guards against a future change
    accidentally adding provider-specific framing in rag.embeddings.
    """
    assert embeddings._DOCUMENT_INSTRUCTION == "Represent this document for retrieval: "
    assert embeddings._QUERY_INSTRUCTION == "Represent this query for retrieving relevant documents: "


@override_settings(RAG_EMBEDDING_PROVIDER='gemini')
def test_gemini_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown RAG_EMBEDDING_PROVIDER 'gemini'"):
        embeddings.embed_query('some question')


@override_settings(RAG_EMBEDDING_PROVIDER='local')
def test_local_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown RAG_EMBEDDING_PROVIDER 'local'"):
        embeddings.embed_query('some question')


@override_settings(RAG_EMBEDDING_PROVIDER='bogus-provider')
def test_unsupported_provider_embed_document_chunks_fails_fast_with_clear_error():
    with pytest.raises(ValueError, match="Unknown RAG_EMBEDDING_PROVIDER 'bogus-provider'"):
        embeddings.embed_document_chunks(['some text'])


@override_settings(RAG_EMBEDDING_PROVIDER='bogus-provider')
def test_unsupported_provider_embed_query_fails_fast_with_clear_error():
    with pytest.raises(ValueError, match="Unknown RAG_EMBEDDING_PROVIDER 'bogus-provider'"):
        embeddings.embed_query('some question')


@override_settings(RAG_EMBEDDING_PROVIDER='bogus-provider')
def test_empty_chunk_list_short_circuits_before_provider_resolution():
    """embed_document_chunks([]) returns [] immediately -- even an invalid
    provider setting must not raise when there is nothing to embed, since no
    provider call would ever be made either way.
    """
    assert embeddings.embed_document_chunks([]) == []


@override_settings(RAG_EMBEDDING_PROVIDER='ollama', RAG_OLLAMA_EMBEDDING_MODEL='nomic-embed-text')
def test_active_embedding_identity_ollama_is_provider_qualified():
    assert embeddings.active_embedding_identity() == 'ollama:nomic-embed-text'


@override_settings(RAG_EMBEDDING_PROVIDER='ollama', RAG_OLLAMA_EMBEDDING_MODEL='nomic-embed-text')
def test_active_embedding_identity_changes_with_ollama_model():
    baseline = embeddings.active_embedding_identity()
    with override_settings(RAG_EMBEDDING_PROVIDER='ollama', RAG_OLLAMA_EMBEDDING_MODEL='other-embed'):
        assert embeddings.active_embedding_identity() != baseline


@override_settings(RAG_EMBEDDING_PROVIDER='bogus-provider')
def test_active_embedding_identity_unsupported_provider_fails_fast():
    with pytest.raises(ValueError, match="Unknown RAG_EMBEDDING_PROVIDER 'bogus-provider'"):
        embeddings.active_embedding_identity()
