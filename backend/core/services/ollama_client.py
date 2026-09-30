"""Ollama client — the LLM backend for agent, Quick Start and spreadsheet NL calls.

Every call goes to ``OLLAMA_MODEL`` via Ollama's non-streaming ``/api/chat``
endpoint at ``OLLAMA_BASE_URL``. HTTP guardrails (bounded retries, wall-clock
deadline, cache-backed circuit breaker) live in :func:`_ollama_request_with_retry`.
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

# Returned by _get_api_key() so "is the LLM configured?" gates pass — Ollama
# needs no key, only a reachable OLLAMA_BASE_URL.
OLLAMA_KEY_SENTINEL = "ollama-local"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

# Retry transient HTTP failures (rate limit + upstream 5xx) and connection drops.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
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
    """Pure HTTP-429 rate-limit retries exhausted.

    Deliberately NOT a ``RuntimeError``: several executors catch this separately
    (``except OllamaRetriesExhausted``) to *skip* a step rather than fail it, and
    that clause sits below an ``except RuntimeError`` that would otherwise
    swallow it.
    """


def get_ollama_model() -> str:
    return getattr(settings, "OLLAMA_MODEL", "qwen3:4b")


def _get_base_url() -> str:
    return str(getattr(settings, "OLLAMA_BASE_URL", "") or "").strip()


def _get_api_key() -> str:
    """Sentinel when Ollama is configured, else ``""``. Patched in tests."""
    return OLLAMA_KEY_SENTINEL if _get_base_url() else ""


def is_llm_configured() -> bool:
    return bool(_get_api_key())


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


def _ollama_request_with_retry(
    url: str,
    body: dict,
    timeout: int | None = None,
    deadline_seconds: int | None = None,
) -> requests.Response:
    """POST to Ollama with bounded retries on transient failures.

    Retries HTTP 429 / 5xx and connection/read errors with exponential backoff,
    capped by both an attempt count and a wall-clock deadline
    (``deadline_seconds``, default ``OLLAMA_TOTAL_DEADLINE_SECONDS``). A
    cache-backed circuit breaker short-circuits when Ollama has been failing.

    On exhaustion: pure 429s -> :class:`OllamaRetriesExhausted`; anything else
    -> :class:`OllamaUnavailable` (a ``RuntimeError``). Non-transient HTTP errors
    and other request errors raise ``RuntimeError`` immediately.
    """
    if _circuit_open():
        raise OllamaUnavailable("Ollama temporarily unavailable (circuit open).")

    base_timeout = int(timeout or getattr(settings, "OLLAMA_TIMEOUT_SECONDS", 300))
    deadline = time.monotonic() + int(
        deadline_seconds or getattr(settings, "OLLAMA_TOTAL_DEADLINE_SECONDS", 600)
    )
    last_exc: Exception | None = None
    saw_non_429 = False

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
            if status_code not in _TRANSIENT_STATUS:
                raise RuntimeError(
                    redact_string(
                        f"Ollama request failed with HTTP {status_code or 'unknown'}."
                    )
                ) from exc
            if status_code != 429:
                saw_non_429 = True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            saw_non_429 = True
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
    # Pure rate-limiting -> OllamaRetriesExhausted (executors skip the step);
    # anything else -> OllamaUnavailable (a RuntimeError, treated as a failure).
    if not saw_non_429:
        raise OllamaRetriesExhausted("Ollama rate limited (HTTP 429).") from last_exc
    raise OllamaUnavailable(
        redact_string("Ollama unavailable after retries.")
    ) from last_exc


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_output_tokens: int | None = None,
    json_mode: bool = False,
    deadline_seconds: int | None = None,
) -> dict:
    """Call Ollama ``/api/chat`` and return ``{'text': str, 'usage': {'input', 'output'}}``.

    ``deadline_seconds`` caps the whole call including retries; it defaults to
    ``OLLAMA_TOTAL_DEADLINE_SECONDS``.

    ``usage`` counts may be 0 — Ollama omits ``prompt_eval_count`` when the
    prompt is fully cached. Billing callers must fall back to an estimate.
    """
    model = model or get_ollama_model()
    base_url = _get_base_url() or "http://host.docker.internal:11434"
    options: dict = {"temperature": temperature}
    if max_output_tokens:
        options["num_predict"] = max_output_tokens
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        # Disable reasoning traces on thinking models (qwen3, deepseek-r1) —
        # they burn the output budget and break JSON parsing.
        "think": False,
        "options": options,
    }
    if json_mode:
        body["format"] = "json"

    logger.info(
        "Calling Ollama model=%s json=%s system_chars=%d user_chars=%d",
        model,
        json_mode,
        len(system_prompt),
        len(user_prompt),
    )

    response = _ollama_request_with_retry(
        f"{base_url.rstrip('/')}/api/chat", body, deadline_seconds=deadline_seconds
    )
    data = response.json()
    text = (data.get("message") or {}).get("content", "") or ""
    text = _THINK_BLOCK.sub("", text).strip()
    return {
        "text": text,
        "usage": {
            "input": int(data.get("prompt_eval_count") or 0),
            "output": int(data.get("eval_count") or 0),
        },
    }


def call_ollama_text(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int | None = None,
    json_mode: bool = False,
) -> str:
    """Call Ollama and return the response text only.

    ``timeout`` is the total budget in seconds for the call including retries
    (default ``OLLAMA_TOTAL_DEADLINE_SECONDS``). Pass it for request/response
    paths so the backend gives up before the browser does.
    """
    return call_ollama(
        system_prompt,
        user_prompt,
        temperature=temperature,
        json_mode=json_mode,
        deadline_seconds=timeout,
    )["text"]


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


def parse_json_text(text: str):
    """Parse LLM text as JSON, tolerating code fences and surrounding prose.

    Raises ``json.JSONDecodeError`` when no valid JSON can be recovered.
    """
    clean = strip_json_fences(text)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return json.loads(_extract_json_block(clean))


def call_ollama_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int | None = None,
    max_attempts: int = 3,
) -> dict:
    """Call Ollama in JSON mode and parse the response. Raises RuntimeError on failure.

    Retries malformed JSON up to ``max_attempts`` times. ``timeout`` is the total
    budget in seconds across all attempts; when set, no new attempt starts once
    it is spent.
    """
    budget_end = time.monotonic() + timeout if timeout else None
    for attempt in range(1, max_attempts + 1):
        remaining = None
        if budget_end is not None:
            remaining = int(budget_end - time.monotonic())
            if remaining <= 0:
                raise OllamaUnavailable("Ollama deadline exceeded.")
        text = call_ollama_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            timeout=remaining,
            json_mode=True,
        )
        try:
            return parse_json_text(text)
        except json.JSONDecodeError as exc:
            logger.error("Ollama returned non-JSON: %s...", strip_json_fences(text)[:300])
            wait_seconds = min(1.5 * attempt, 3.0)
            out_of_time = (
                budget_end is not None
                and time.monotonic() + wait_seconds >= budget_end
            )
            if attempt >= max_attempts or out_of_time:
                raise RuntimeError(
                    "Ollama returned malformed output. Please retry generation."
                ) from exc
            logger.warning(
                "Retrying Ollama call after JSON parse failure (attempt %s/%s, wait %.1fs)",
                attempt + 1,
                max_attempts,
                wait_seconds,
            )
            time.sleep(wait_seconds)
