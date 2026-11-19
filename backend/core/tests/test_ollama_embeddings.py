"""Tests for core.services.ollama_embeddings. HTTP is mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from core.services.ollama_embeddings import OllamaEmbeddingResponseError, embed_content, batch_embed_contents


def _response(status_code, payload):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434', RAG_OLLAMA_TIMEOUT_SECONDS=5)
def test_embed_content_posts_api_embed_and_returns_the_vector():
    vector = [0.1, 0.2, 0.3]
    with patch('core.services.ollama_embeddings.requests.post') as mock_post:
        mock_post.return_value = _response(200, {'embeddings': [vector]})
        result = embed_content('hello', model='nomic-embed-text', dimensions=3)

    assert result == vector
    args, kwargs = mock_post.call_args
    assert args[0] == 'http://ollama.test:11434/api/embed'
    assert kwargs['json'] == {'model': 'nomic-embed-text', 'input': 'hello'}
    assert kwargs['timeout'] == 5


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434')
def test_batch_embed_contents_preserves_order():
    vectors = [[0.1], [0.2]]
    with patch('core.services.ollama_embeddings.requests.post') as mock_post:
        mock_post.return_value = _response(200, {'embeddings': vectors})
        result = batch_embed_contents(['a', 'b'], model='nomic-embed-text', dimensions=1)

    assert result == vectors
    assert mock_post.call_args.kwargs['json']['input'] == ['a', 'b']


def test_batch_embed_contents_empty_input_does_not_call_ollama():
    with patch('core.services.ollama_embeddings.requests.post') as mock_post:
        assert batch_embed_contents([], model='nomic-embed-text', dimensions=1) == []
    mock_post.assert_not_called()


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434')
def test_wrong_width_is_rejected():
    with patch('core.services.ollama_embeddings.requests.post') as mock_post:
        mock_post.return_value = _response(200, {'embeddings': [[0.1, 0.2]]})
        with pytest.raises(OllamaEmbeddingResponseError, match='2-dim'):
            embed_content('hello', model='nomic-embed-text', dimensions=768)


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434')
def test_busy_status_is_retried_then_accepted():
    vector = [0.5]
    with patch('core.services.ollama_embeddings.time.sleep') as mock_sleep, \
         patch('core.services.ollama_embeddings.requests.post') as mock_post:
        mock_post.side_effect = [
            _response(503, {}),
            _response(200, {'embeddings': [vector]}),
        ]
        result = embed_content('hello', model='nomic-embed-text', dimensions=1)

    assert result == vector
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(1.0)
