"""
Tests for core.services.local_embeddings (MED-264 temporary local embedding
provider). Mocks fastembed.TextEmbedding throughout -- these tests exercise
this module's own caching/validation/dispatch logic, not fastembed's ONNX
runtime itself (that's covered by the manual runtime probe against the real
BAAI/bge-base-en-v1.5 model, run separately since it needs a model download).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.services import local_embeddings

MODEL_NAME = 'BAAI/bge-base-en-v1.5'


@pytest.fixture(autouse=True)
def _clear_model_cache():
    """The module-level _MODEL_CACHE persists across calls by design (that's
    the feature under test), but must not leak a mock instance from one test
    into the next.
    """
    local_embeddings._MODEL_CACHE.clear()
    yield
    local_embeddings._MODEL_CACHE.clear()


def _fake_text_embedding_class(embed_side_effect):
    """Builds a stand-in for fastembed.TextEmbedding: constructing it returns
    a MagicMock instance whose .embed() delegates to `embed_side_effect`, and
    records how many times the class itself was constructed.
    """
    instances = []

    def _construct(model_name):
        instance = MagicMock()
        instance.embed.side_effect = embed_side_effect
        instance._model_name = model_name
        instances.append(instance)
        return instance

    factory = MagicMock(side_effect=_construct)
    factory.instances = instances
    return factory


def test_embed_content_returns_768_floats(monkeypatch):
    monkeypatch.setattr(
        local_embeddings, 'TextEmbedding',
        _fake_text_embedding_class(lambda docs: iter([[0.1] * 768])),
    )

    vector = local_embeddings.embed_content('hello world', model=MODEL_NAME, dimensions=768)

    assert len(vector) == 768
    assert all(isinstance(v, float) for v in vector)


def test_batch_preserves_count_and_order(monkeypatch):
    def _embed(docs):
        # Distinct, order-identifiable vectors per input.
        return iter([[float(i)] * 768 for i, _ in enumerate(docs)])

    monkeypatch.setattr(local_embeddings, 'TextEmbedding', _fake_text_embedding_class(_embed))

    texts = ['first chunk', 'second chunk', 'third chunk']
    vectors = local_embeddings.batch_embed_contents(texts, model=MODEL_NAME, dimensions=768)

    assert len(vectors) == 3
    assert [v[0] for v in vectors] == [0.0, 1.0, 2.0]
    assert all(len(v) == 768 for v in vectors)


def test_empty_batch_returns_empty_list_without_constructing_model(monkeypatch):
    fake_class = _fake_text_embedding_class(lambda docs: iter([]))
    monkeypatch.setattr(local_embeddings, 'TextEmbedding', fake_class)

    result = local_embeddings.batch_embed_contents([], model=MODEL_NAME, dimensions=768)

    assert result == []
    fake_class.assert_not_called()


def test_model_instance_is_cached_across_calls(monkeypatch):
    fake_class = _fake_text_embedding_class(lambda docs: iter([[0.1] * 768 for _ in docs]))
    monkeypatch.setattr(local_embeddings, 'TextEmbedding', fake_class)

    local_embeddings.embed_content('first call', model=MODEL_NAME, dimensions=768)
    local_embeddings.embed_content('second call', model=MODEL_NAME, dimensions=768)
    local_embeddings.batch_embed_contents(['third call'], model=MODEL_NAME, dimensions=768)

    fake_class.assert_called_once_with(model_name=MODEL_NAME)
    assert len(local_embeddings._MODEL_CACHE) == 1
    assert local_embeddings._get_model(MODEL_NAME) is fake_class.instances[0]


def test_dimension_mismatch_raises_local_embedding_response_error(monkeypatch):
    monkeypatch.setattr(
        local_embeddings, 'TextEmbedding',
        _fake_text_embedding_class(lambda docs: iter([[0.1] * 384])),  # wrong width
    )

    with pytest.raises(local_embeddings.LocalEmbeddingResponseError):
        local_embeddings.embed_content('hello world', model=MODEL_NAME, dimensions=768)


def test_batch_dimension_mismatch_raises_for_offending_item(monkeypatch):
    def _embed(docs):
        return iter([[0.1] * 768, [0.1] * 384])  # second item wrong width

    monkeypatch.setattr(local_embeddings, 'TextEmbedding', _fake_text_embedding_class(_embed))

    with pytest.raises(local_embeddings.LocalEmbeddingResponseError):
        local_embeddings.batch_embed_contents(['ok', 'bad'], model=MODEL_NAME, dimensions=768)
