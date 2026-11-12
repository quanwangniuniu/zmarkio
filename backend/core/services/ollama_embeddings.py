"""Ollama embeddings client for local RAG.

Calls a local Ollama server's POST /api/embed. rag.embeddings selects this
module when settings.RAG_EMBEDDING_PROVIDER == 'ollama', which is the
production default.

Same two-function surface as local_embeddings
(embed_content / batch_embed_contents). Document/query framing is applied by
rag.embeddings before text arrives here, so this client sends the text as-is
and does not add an Ollama prompt template.

The returned width is checked against the caller's `dimensions` and never
padded or truncated. Ollama's optional request `dimensions` field is not
sent, because some models silently shorten the vector.
"""
from __future__ import annotations

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_BUSY_STATUSES = frozenset({429, 503})
_RETRY_DELAYS_SECONDS = (1.0, 2.0)


class OllamaEmbeddingResponseError(RuntimeError):
    """Ollama returned a non-embedding response or a vector of the wrong width."""


def _base_url() -> str:
    return str(getattr(settings, "RAG_OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")


def _timeout(timeout: int | None) -> int:
    if timeout:
        return int(timeout)
    return int(getattr(settings, "RAG_OLLAMA_TIMEOUT_SECONDS", 60))


def _validate(vector, *, expected_dimensions: int) -> list[float]:
    if not isinstance(vector, list):
        raise OllamaEmbeddingResponseError("Ollama embedding response item is not a vector")
    values = [float(v) for v in vector]
    if len(values) != expected_dimensions:
        raise OllamaEmbeddingResponseError(
            f"Ollama embedding model returned a {len(values)}-dim vector, expected {expected_dimensions}"
        )
    return values


def _post_embed(payload: dict, *, timeout: int | None) -> dict:
    url = f"{_base_url()}/api/embed"
    delays = _RETRY_DELAYS_SECONDS
    response = None
    for attempt in range(len(delays) + 1):
        response = requests.post(url, json=payload, timeout=_timeout(timeout))
        if response.status_code not in _BUSY_STATUSES or attempt == len(delays):
            break
        time.sleep(delays[attempt])
    if response is None or response.status_code != 200:
        status = getattr(response, "status_code", None)
        logger.error("Ollama embed call failed status=%s", status)
        raise OllamaEmbeddingResponseError(f"Ollama embed call failed with status {status}")
    try:
        data = response.json()
    except ValueError as exc:
        raise OllamaEmbeddingResponseError("Ollama embed response was not JSON") from exc
    if not isinstance(data, dict):
        raise OllamaEmbeddingResponseError("Ollama embed response was not a JSON object")
    return data


def _embeddings_from_response(data: dict, *, expected_count: int, expected_dimensions: int) -> list[list[float]]:
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list):
        raise OllamaEmbeddingResponseError("Ollama embed response missing 'embeddings'")
    if len(embeddings) != expected_count:
        raise OllamaEmbeddingResponseError(
            f"Ollama embed returned {len(embeddings)} vectors, expected {expected_count}"
        )
    return [_validate(vector, expected_dimensions=expected_dimensions) for vector in embeddings]


def embed_content(text: str, *, model: str, dimensions: int, timeout: int | None = None) -> list[float]:
    """Embed a single text. Returns one vector of exactly `dimensions` floats."""
    data = _post_embed({"model": model, "input": text}, timeout=timeout)
    vectors = _embeddings_from_response(data, expected_count=1, expected_dimensions=dimensions)
    return vectors[0]


def batch_embed_contents(
    texts: list[str], *, model: str, dimensions: int, timeout: int | None = None
) -> list[list[float]]:
    """Embed multiple texts in one call, in the same order as `texts`."""
    if not texts:
        return []
    data = _post_embed({"model": model, "input": texts}, timeout=timeout)
    return _embeddings_from_response(data, expected_count=len(texts), expected_dimensions=dimensions)
