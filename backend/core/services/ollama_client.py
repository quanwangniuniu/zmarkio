"""Ollama API client.

All LLM inference goes through a local Ollama server (``/api/chat``).
The server URL and model name are read from settings / environment variables.
"""
import json
import logging
import re
import time

import requests
from django.conf import settings
from django.core.cache import cache

from core.services.log_redaction import redact_string

logger = logging.getLogger(__name__)

_CHAT_PATH = "/api/chat"

# Retry transient HTTP failures (server busy + upstream 5xx) and connection drops.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
# Ollama answers 503 when its request queue is full; 429 can come from a proxy
# in front of it. Both mean "busy", not "broken".
_BUSY_STATUS = frozenset({429, 503})
_TRANSIENT_MAX_ATTEMPTS = 4
_RATE_LIMIT_BACKOFF_SECONDS = (2.0, 4.0, 8.0)

# Cache-backed circuit breaker so an Ollama outage fails fast instead of tying up
# every worker for the full retry budget.
_CB_FAIL_KEY = "ollama:cb:failures"
_CB_OPEN_KEY = "ollama:cb:open_until"


class OllamaUnavailable(RuntimeError):
    """Ollama could not be reached: upstream 5xx retries exhausted, a connection
    failure, the wall-clock deadline, or an open circuit breaker.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` callers keep
    treating it as a normal LLM failure.
    """


class OllamaRetriesExhausted(Exception):
    """Retries exhausted while Ollama only ever answered "busy" (HTTP 503/429).

    Deliberately NOT a ``RuntimeError``: several executors catch this separately
    (``except OllamaRetriesExhausted``) to end a step without another retry
    round, and that clause sits below an ``except RuntimeError`` that would
    otherwise swallow it.
    """


def _resolve_timeout(timeout):
    if timeout:
        return int(timeout)
    return int(getattr(settings, "OLLAMA_TIMEOUT_SECONDS", 75))


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
        window = int(getattr(settings, "OLLAMA_CB_WINDOW_SECONDS", 60))
        cache.add(_CB_FAIL_KEY, 0, window)
        try:
            failures = cache.incr(_CB_FAIL_KEY)
        except ValueError:
            cache.set(_CB_FAIL_KEY, 1, window)
            failures = 1
        if failures >= int(getattr(settings, "OLLAMA_CB_THRESHOLD", 5)):
            cooldown = int(getattr(settings, "OLLAMA_CB_COOLDOWN_SECONDS", 30))
            cache.set(_CB_OPEN_KEY, time.time() + cooldown, cooldown)
            logger.warning("Ollama circuit breaker opened for %ss", cooldown)
    except Exception:
        logger.warning("Ollama circuit-breaker cache op failed", exc_info=True)


def _get_base_url() -> str:
    """Return the Ollama server URL, or '' when the LLM is not configured."""
    return (getattr(settings, "OLLAMA_BASE_URL", "") or "").strip().rstrip("/")


def _get_model() -> str:
    return settings.OLLAMA_MODEL


def _chat_url() -> str:
    base_url = _get_base_url()
    if not base_url:
        raise RuntimeError("OLLAMA_BASE_URL is not configured")
    return f"{base_url}{_CHAT_PATH}"


def _chat_body(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    response_mime_type: str | None = None,
    max_output_tokens: int | None = None,
) -> dict:
    """Build a non-streaming ``/api/chat`` request body.

    ``think`` is false: instruct models ignore it and hybrid models skip the
    reasoning trace. Thinking-only models cannot disable reasoning; with
    think=false Ollama returns the trace (ending in ``</think>``) inside
    ``message.content``, so OLLAMA_MODEL must not be one of them.
    ``num_ctx`` is explicit because Ollama silently truncates prompts longer
    than its 4096-token default.
    """
    options: dict = {
        "temperature": temperature,
        "num_ctx": int(getattr(settings, "OLLAMA_NUM_CTX", 16384)),
    }
    if max_output_tokens:
        options["num_predict"] = max_output_tokens
    body: dict = {
        "model": _get_model(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "think": False,
        "keep_alive": getattr(settings, "OLLAMA_KEEP_ALIVE", "30m"),
        "options": options,
    }
    if response_mime_type == "application/json":
        body["format"] = "json"
    return body


def _ollama_request_with_retry(
    url: str,
    body: dict,
    timeout: int | None = None,
) -> requests.Response:
    """POST to an Ollama endpoint with bounded retries on transient failures.

    Retries HTTP 429 / 5xx and connection/read errors with exponential backoff,
    capped by both an attempt count and a wall-clock deadline
    (``OLLAMA_TOTAL_DEADLINE_SECONDS``). A cache-backed circuit breaker
    short-circuits when Ollama has been failing.

    On exhaustion: only "busy" answers -> :class:`OllamaRetriesExhausted`;
    anything else -> :class:`OllamaUnavailable` (a ``RuntimeError``).
    A missing model (HTTP 404) and other non-transient errors raise
    ``RuntimeError`` immediately.
    """
    if _circuit_open():
        raise OllamaUnavailable("Ollama temporarily unavailable (circuit open).")

    base_timeout = _resolve_timeout(timeout)
    deadline = time.monotonic() + int(
        getattr(settings, "OLLAMA_TOTAL_DEADLINE_SECONDS", 150)
    )
    last_exc: Exception | None = None
    saw_not_busy = False

    for attempt in range(1, _TRANSIENT_MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _circuit_record(False)
            raise OllamaUnavailable("Ollama deadline exceeded.") from last_exc
        per_call = max(1, min(base_timeout, int(remaining)))
        try:
            response = requests.post(url, json=body, timeout=per_call)
            response.raise_for_status()
            _circuit_record(True)
            return response
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 404:
                if _is_missing_model(exc.response):
                    model = body.get("model", "")
                    raise RuntimeError(
                        f"Ollama model {model!r} is not available. "
                        f"Run `ollama pull {model}` on the Ollama host."
                    ) from exc
                raise RuntimeError(
                    "Ollama endpoint not found (HTTP 404). Check OLLAMA_BASE_URL."
                ) from exc
            if status_code not in _TRANSIENT_STATUS:
                raise RuntimeError(
                    redact_string(
                        f"Ollama request failed with HTTP {status_code or 'unknown'}."
                    )
                ) from exc
            if status_code not in _BUSY_STATUS:
                saw_not_busy = True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            saw_not_busy = True
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(redact_string(f"Ollama network error: {exc}")) from exc

        if attempt >= _TRANSIENT_MAX_ATTEMPTS:
            break
        wait_seconds = _RATE_LIMIT_BACKOFF_SECONDS[
            min(attempt - 1, len(_RATE_LIMIT_BACKOFF_SECONDS) - 1)
        ]
        if time.monotonic() + wait_seconds > deadline:
            break
        logger.warning(
            "Ollama transient failure (%s); retrying in %.1fs (attempt %s/%s)",
            type(last_exc).__name__,
            wait_seconds,
            attempt + 1,
            _TRANSIENT_MAX_ATTEMPTS,
        )
        time.sleep(wait_seconds)

    _circuit_record(False)
    # Only "busy" answers -> OllamaRetriesExhausted (executors end the step);
    # anything else -> OllamaUnavailable (a RuntimeError, treated as a failure).
    if not saw_not_busy:
        raise OllamaRetriesExhausted("Ollama is busy (HTTP 503).") from last_exc
    raise OllamaUnavailable(
        redact_string("Ollama unavailable after retries.")
    ) from last_exc


def _is_missing_model(response) -> bool:
    """True when a 404 body is Ollama's "model 'x' not found" error.

    A wrong OLLAMA_BASE_URL path also answers 404, but with a plain-text
    "404 page not found" body instead of a JSON ``error``.
    """
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return False
    return isinstance(error, str) and "model" in error and "not found" in error


def _extract_text(data: dict) -> str:
    """Return the assistant text from a non-streaming ``/api/chat`` response."""
    message = data.get("message") or {}
    return (message.get("content") or "").strip()


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int | None = None,
    response_mime_type: str | None = None,
) -> str:
    """Call Ollama ``/api/chat`` and return the full text response.

    ``timeout`` defaults to ``settings.OLLAMA_TIMEOUT_SECONDS`` when unset.
    """
    url = _chat_url()
    body = _chat_body(system_prompt, user_prompt, temperature, response_mime_type)

    logger.info(
        "Calling Ollama model=%s system_chars=%d user_chars=%d",
        body["model"],
        len(system_prompt),
        len(user_prompt),
    )

    response = _ollama_request_with_retry(url, body, timeout=timeout)
    return _extract_text(response.json())


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


def call_ollama_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int | None = None,
    _attempt: int = 1,
    _max_attempts: int = 3,
) -> dict:
    """Call Ollama and parse the response as JSON. Raises RuntimeError on failure."""
    text = call_ollama(
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
            logger.error("Ollama returned non-JSON: %s...", clean[:300])
            if _attempt < _max_attempts:
                wait_seconds = min(1.5 * _attempt, 3.0)
                logger.warning(
                    "Retrying Ollama call after JSON parse failure (attempt %s/%s, wait %.1fs)",
                    _attempt + 1,
                    _max_attempts,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                return call_ollama_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    timeout=timeout,
                    _attempt=_attempt + 1,
                    _max_attempts=_max_attempts,
                )
            raise RuntimeError(
                "Ollama returned malformed output. Please retry generation."
            ) from exc
