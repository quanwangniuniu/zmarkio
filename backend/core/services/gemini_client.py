"""Gemini API client — replaces Dify workflow calls.

All LLM inference in the agent pipeline now goes through this module.
The endpoint and key are read from settings / environment variables.
"""
import json
import logging
import os
import re
import time

import requests
from django.conf import settings
from django.core.cache import cache

from core.services.log_redaction import redact_string

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash-lite"
_GEMINI_BASE = "https://aiplatform.googleapis.com/v1/projects/406201877905/locations/global/publishers/google/models"

# Retry transient HTTP failures (rate limit + upstream 5xx) and connection drops.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_MAX_ATTEMPTS = 4
_RATE_LIMIT_MAX_ATTEMPTS = _TRANSIENT_MAX_ATTEMPTS  # back-compat alias
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 4.0, 8.0)

# Cache-backed circuit breaker so a Gemini outage fails fast instead of tying up
# every worker for the full retry budget.
_CB_FAIL_KEY = "gemini:cb:failures"
_CB_OPEN_KEY = "gemini:cb:open_until"


class GeminiUnavailable(RuntimeError):
    """Gemini could not be reached: upstream 5xx retries exhausted, a connection
    failure, the wall-clock deadline, or an open circuit breaker.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` callers keep
    treating it as a normal LLM failure.
    """


class GeminiRetriesExhausted(Exception):
    """Pure HTTP-429 rate-limit retries exhausted.

    Deliberately NOT a ``RuntimeError``: several executors catch this separately
    (``except GeminiRetriesExhausted``) to *skip* a step rather than fail it, and
    that clause sits below an ``except RuntimeError`` that would otherwise
    swallow it.
    """


def _resolve_timeout(timeout):
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
            logger.warning("Gemini circuit breaker opened for %ss", cooldown)
    except Exception:
        logger.warning("Gemini circuit-breaker cache op failed", exc_info=True)


def _get_api_key() -> str:
    return (
        getattr(settings, "GEMINI_API_KEY", "")
        or os.environ.get("GEMINI_API_KEY", "")
    )


def _gemini_request_with_retry(
    url: str,
    body: dict,
    timeout: int | None = None,
    stream: bool = False,
) -> requests.Response:
    """POST to a Gemini endpoint with bounded retries on transient failures.

    Retries HTTP 429 / 5xx and connection/read errors with exponential backoff,
    capped by both an attempt count and a wall-clock deadline
    (``GEMINI_TOTAL_DEADLINE_SECONDS``). A cache-backed circuit breaker
    short-circuits when Gemini has been failing.

    On exhaustion: pure 429s -> :class:`GeminiRetriesExhausted`; anything else
    -> :class:`GeminiUnavailable` (a ``RuntimeError``). Non-transient HTTP errors
    and other request errors raise ``RuntimeError`` immediately.
    """
    if _circuit_open():
        raise GeminiUnavailable("Gemini temporarily unavailable (circuit open).")

    base_timeout = _resolve_timeout(timeout)
    deadline = time.monotonic() + int(
        getattr(settings, "GEMINI_TOTAL_DEADLINE_SECONDS", 150)
    )
    last_exc: Exception | None = None
    saw_non_429 = False

    for attempt in range(1, _TRANSIENT_MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _circuit_record(False)
            raise GeminiUnavailable("Gemini deadline exceeded.") from last_exc
        per_call = max(1, min(base_timeout, int(remaining)))
        try:
            response = requests.post(
                url, json=body, timeout=per_call, stream=stream
            )
            response.raise_for_status()
            _circuit_record(True)
            return response
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code not in _TRANSIENT_STATUS:
                raise RuntimeError(
                    redact_string(
                        f"Gemini request failed with HTTP {status_code or 'unknown'}."
                    )
                ) from exc
            if status_code != 429:
                saw_non_429 = True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            saw_non_429 = True
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(redact_string(f"Gemini network error: {exc}")) from exc

        if attempt >= _TRANSIENT_MAX_ATTEMPTS:
            break
        wait_seconds = _RATE_LIMIT_BACKOFF_SECONDS[
            min(attempt - 1, len(_RATE_LIMIT_BACKOFF_SECONDS) - 1)
        ]
        if time.monotonic() + wait_seconds > deadline:
            break
        logger.warning(
            "Gemini transient failure (%s); retrying in %.1fs (attempt %s/%s)",
            type(last_exc).__name__,
            wait_seconds,
            attempt + 1,
            _TRANSIENT_MAX_ATTEMPTS,
        )
        time.sleep(wait_seconds)

    _circuit_record(False)
    # Pure rate-limiting -> GeminiRetriesExhausted (executors skip the step);
    # anything else -> GeminiUnavailable (a RuntimeError, treated as a failure).
    if not saw_non_429:
        raise GeminiRetriesExhausted("Gemini rate limited (HTTP 429).") from last_exc
    raise GeminiUnavailable(
        redact_string("Gemini unavailable after retries.")
    ) from last_exc


def call_gemini(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int | None = None,
    response_mime_type: str | None = None,
) -> str:
    """Call Gemini via streamGenerateContent and return the full text response.

    ``timeout`` defaults to ``settings.GEMINI_TIMEOUT_SECONDS`` when unset.
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    url = f"{_GEMINI_BASE}/{GEMINI_MODEL}:streamGenerateContent?key={api_key}"
    generation_config: dict = {"temperature": temperature}
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": generation_config,
    }

    logger.info(
        "Calling Gemini model=%s system_chars=%d user_chars=%d",
        GEMINI_MODEL,
        len(system_prompt),
        len(user_prompt),
    )

    response = _gemini_request_with_retry(url, body, timeout=timeout, stream=True)
    buffer = b""
    for chunk in response.iter_content(chunk_size=None):
        if chunk:
            buffer += chunk
    return _extract_text(buffer.decode("utf-8", errors="replace"))


def _extract_text(raw: str) -> str:
    """Concatenate all text parts from a Gemini streamGenerateContent response."""
    raw = raw.strip()
    try:
        responses = json.loads(raw)
        if not isinstance(responses, list):
            responses = [responses]
        parts = []
        for resp in responses:
            for candidate in resp.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    text = part.get("text", "")
                    if text:
                        parts.append(text)
        return "".join(parts).strip()
    except (json.JSONDecodeError, KeyError, TypeError):
        # Fallback: try SSE line-by-line
        parts = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[5:].strip()
                try:
                    obj = json.loads(data)
                    for candidate in obj.get("candidates", []):
                        for part in candidate.get("content", {}).get("parts", []):
                            text = part.get("text", "")
                            if text:
                                parts.append(text)
                except json.JSONDecodeError:
                    pass
        return "".join(parts).strip() or raw


def strip_json_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _extract_json_block(text: str) -> str:
    """Extract the first complete {...} or [...] block from text."""
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text


def call_gemini_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int | None = None,
    _attempt: int = 1,
    _max_attempts: int = 3,
) -> dict:
    """Call Gemini and parse the response as JSON. Raises RuntimeError on failure."""
    text = call_gemini(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=temperature,
        timeout=timeout,
        response_mime_type="application/json",
    )
    clean = strip_json_fences(text)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        extracted = _extract_json_block(clean)
        try:
            return json.loads(extracted)
        except json.JSONDecodeError as exc:
            logger.error("Gemini returned non-JSON: %s...", clean[:300])
            if _attempt < _max_attempts:
                wait_seconds = min(1.5 * _attempt, 3.0)
                logger.warning(
                    "Retrying Gemini call after JSON parse failure (attempt %s/%s, wait %.1fs)",
                    _attempt + 1,
                    _max_attempts,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                return call_gemini_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    timeout=timeout,
                    _attempt=_attempt + 1,
                    _max_attempts=_max_attempts,
                )
            raise RuntimeError(
                "Gemini returned malformed output. Please retry generation."
            ) from exc
