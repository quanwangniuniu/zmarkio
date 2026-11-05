"""Gemini embeddings API client (:embedContent / :batchEmbedContents).

Separate from core.services.gemini_client (which only calls
streamGenerateContent, for chat/generation) because the embeddings API is a
genuinely different wire contract:

- Auth attaches via the `x-goog-api-key` header, not a `?key=` query param.
- The host is generativelanguage.googleapis.com (Gemini API), not the
  aiplatform.googleapis.com (Vertex) host gemini_client.py calls.
- Responses are a single non-streaming JSON body, not SSE chunks — none of
  gemini_client.py's stream-parsing (`_extract_text`) applies here.

Retry/backoff/circuit-breaker *conventions* mirror gemini_client.py (same
transient-status set, same backoff schedule, same wall-clock deadline, same
exception types so callers treat "Gemini is down" consistently everywhere),
but circuit-breaker *state* is tracked independently under separate cache
keys — an embeddings outage must not trip the chat circuit breaker, and
vice versa, since they're different endpoints with different quotas.

gemini-embedding-2 does not support `task_type` (current Gemini API docs).
Retrieval framing (this is a document to be found vs. this is a query
looking for documents) must be encoded directly into the input text by the
caller instead — that framing lives in rag.embeddings, not here, so it stays
independently tunable through eval work without touching this module. This
client never sends a `task_type` field.
"""
from __future__ import annotations

import logging
import os
import time

import requests
from django.conf import settings
from django.core.cache import cache

from core.services.gemini_client import GeminiRetriesExhausted, GeminiUnavailable
from core.services.log_redaction import redact_string

logger = logging.getLogger(__name__)

_EMBED_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Same schedule as gemini_client.py, duplicated rather than imported: these
# are plain constants, and this module's retry loop parses a different
# (non-streaming) response shape, so there's no function body to share.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_MAX_ATTEMPTS = 4
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 4.0, 8.0)

_CB_FAIL_KEY = "gemini_embed:cb:failures"
_CB_OPEN_KEY = "gemini_embed:cb:open_until"


class GeminiEmbeddingResponseError(RuntimeError):
    """The Gemini embeddings API returned a response that doesn't match the
    expected shape (missing fields, or a vector of the wrong length).

    Raised instead of letting a KeyError/IndexError escape, and instead of
    silently persisting a malformed/wrong-dimensionality vector.
    """


def _get_api_key() -> str:
    return (
        getattr(settings, "GEMINI_API_KEY", "")
        or os.environ.get("GEMINI_API_KEY", "")
    )


def _resolve_timeout(timeout: int | None) -> int:
    if timeout:
        return int(timeout)
    return int(getattr(settings, "GEMINI_TIMEOUT_SECONDS", 75))


def _circuit_open() -> bool:
    try:
        until = cache.get(_CB_OPEN_KEY)
        return bool(until) and until > time.time()
    except Exception:  # cache backend itself down -> fail open
        return False


def _circuit_record(success: bool) -> None:
    try:
        if success:
            cache.delete_many([_CB_FAIL_KEY, _CB_OPEN_KEY])
            return
        window = int(getattr(settings, "GEMINI_CB_WINDOW_SECONDS", 60))
        cache.add(_CB_FAIL_KEY, 0, window)
        try:
            failures = cache.incr(_CB_FAIL_KEY)
        except ValueError:
            cache.set(_CB_FAIL_KEY, 1, window)
            failures = 1
        if failures >= int(getattr(settings, "GEMINI_CB_THRESHOLD", 5)):
            cooldown = int(getattr(settings, "GEMINI_CB_COOLDOWN_SECONDS", 30))
            cache.set(_CB_OPEN_KEY, time.time() + cooldown, cooldown)
            logger.warning("Gemini embeddings circuit breaker opened for %ss", cooldown)
    except Exception:
        logger.warning("Gemini embeddings circuit-breaker cache op failed", exc_info=True)


def _request_with_retry(url: str, body: dict, timeout: int | None = None) -> dict:
    """POST to a Gemini embeddings endpoint with bounded retries. Returns the parsed JSON body."""
    if _circuit_open():
        raise GeminiUnavailable("Gemini embeddings temporarily unavailable (circuit open).")

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    base_timeout = _resolve_timeout(timeout)
    deadline = time.monotonic() + int(getattr(settings, "GEMINI_TOTAL_DEADLINE_SECONDS", 150))
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    last_exc: Exception | None = None
    saw_non_429 = False

    for attempt in range(1, _TRANSIENT_MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _circuit_record(False)
            raise GeminiUnavailable("Gemini embeddings deadline exceeded.") from last_exc
        per_call = max(1, min(base_timeout, int(remaining)))
        try:
            response = requests.post(url, json=body, headers=headers, timeout=per_call)
            response.raise_for_status()
            _circuit_record(True)
            return response.json()
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in _TRANSIENT_STATUS:
                raise RuntimeError(
                    redact_string(f"Gemini embeddings request failed with HTTP {status_code or 'unknown'}.")
                ) from exc
            if status_code != 429:
                saw_non_429 = True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            saw_non_429 = True
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(redact_string(f"Gemini embeddings network error: {exc}")) from exc

        if attempt >= _TRANSIENT_MAX_ATTEMPTS:
            break
        wait_seconds = _RATE_LIMIT_BACKOFF_SECONDS[min(attempt - 1, len(_RATE_LIMIT_BACKOFF_SECONDS) - 1)]
        if time.monotonic() + wait_seconds > deadline:
            break
        logger.warning(
            "Gemini embeddings transient failure (%s); retrying in %.1fs (attempt %s/%s)",
            type(last_exc).__name__, wait_seconds, attempt + 1, _TRANSIENT_MAX_ATTEMPTS,
        )
        time.sleep(wait_seconds)

    _circuit_record(False)
    if not saw_non_429:
        raise GeminiRetriesExhausted("Gemini embeddings rate limited (HTTP 429).") from last_exc
    raise GeminiUnavailable(redact_string("Gemini embeddings unavailable after retries.")) from last_exc


def _validate_single_embedding(data: dict, *, expected_dimensions: int) -> list[float]:
    embedding = data.get("embedding") if isinstance(data, dict) else None
    values = embedding.get("values") if isinstance(embedding, dict) else None
    if not isinstance(values, list):
        keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise GeminiEmbeddingResponseError(
            f"Gemini embeddings response missing embedding.values (top-level keys: {keys})"
        )
    if len(values) != expected_dimensions:
        raise GeminiEmbeddingResponseError(
            f"Gemini embeddings returned a {len(values)}-dim vector, expected {expected_dimensions}"
        )
    return values


def _validate_batch_embeddings(
    data: dict, *, expected_count: int, expected_dimensions: int
) -> list[list[float]]:
    embeddings = data.get("embeddings") if isinstance(data, dict) else None
    if not isinstance(embeddings, list):
        keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        raise GeminiEmbeddingResponseError(
            f"Gemini embeddings batch response missing 'embeddings' (top-level keys: {keys})"
        )
    if len(embeddings) != expected_count:
        raise GeminiEmbeddingResponseError(
            f"Gemini embeddings batch returned {len(embeddings)} vectors, expected {expected_count}"
        )
    result: list[list[float]] = []
    for i, item in enumerate(embeddings):
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list):
            raise GeminiEmbeddingResponseError(
                f"Gemini embeddings batch item {i} is missing 'values'"
            )
        if len(values) != expected_dimensions:
            raise GeminiEmbeddingResponseError(
                f"Gemini embeddings batch item {i} returned a {len(values)}-dim vector, "
                f"expected {expected_dimensions}"
            )
        result.append(values)
    return result


def embed_content(
    text: str,
    *,
    model: str,
    dimensions: int,
    timeout: int | None = None,
) -> list[float]:
    """Embed a single text via :embedContent.

    Raises GeminiEmbeddingResponseError if the response doesn't contain a
    vector of exactly `dimensions` length. No `task_type` is sent —
    gemini-embedding-2 doesn't support it; retrieval framing belongs in the
    input text, applied by the caller (see rag.embeddings).
    """
    url = f"{_EMBED_BASE}/{model}:embedContent"
    body = {
        "content": {"parts": [{"text": text}]},
        "output_dimensionality": dimensions,
    }
    data = _request_with_retry(url, body, timeout=timeout)
    return _validate_single_embedding(data, expected_dimensions=dimensions)


def batch_embed_contents(
    texts: list[str],
    *,
    model: str,
    dimensions: int,
    timeout: int | None = None,
) -> list[list[float]]:
    """Embed multiple texts in one call via :batchEmbedContents.

    Returns one vector per input, in the same order as `texts`. `model` is a
    path parameter here (`.../models/{model}:batchEmbedContents`), not a
    top-level request-body field — per the current API reference
    (ai.google.dev/api/embeddings), the documented request body contains
    only `requests[]`, and each item repeats its own `model` (required to
    match the path). A previous draft of this function incorrectly added a
    duplicate top-level `model` body key from a conflated read of the docs;
    removed after a targeted re-check separating "Path parameters" from
    "Request Body Structure" on that page.

    Raises GeminiEmbeddingResponseError if the response doesn't contain
    exactly `len(texts)` vectors, each of exactly `dimensions` length.
    """
    if not texts:
        return []
    url = f"{_EMBED_BASE}/{model}:batchEmbedContents"
    body = {
        "requests": [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
                "output_dimensionality": dimensions,
            }
            for text in texts
        ],
    }
    data = _request_with_retry(url, body, timeout=timeout)
    return _validate_batch_embeddings(data, expected_count=len(texts), expected_dimensions=dimensions)
