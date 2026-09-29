"""Ollama backend — local drop-in replacement for Gemini.

Enabled with ``LLM_BACKEND=ollama``. When on, ``core.services.gemini_client.call_gemini``
and ``agent.llm_client.call_llm(provider='gemini')`` are served by ``OLLAMA_MODEL``
via Ollama's non-streaming ``/api/chat`` endpoint, so callers need no changes.
"""
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

# Sentinel returned by gemini_client._get_api_key() so "is the LLM configured?"
# gates pass when Ollama (which needs no key) is the backend.
OLLAMA_KEY_SENTINEL = "ollama-local"

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def is_ollama_backend() -> bool:
    return str(getattr(settings, "LLM_BACKEND", "gemini")).strip().lower() == "ollama"


def get_ollama_model() -> str:
    return getattr(settings, "OLLAMA_MODEL", "qwen3:4b")


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_output_tokens: int | None = None,
    json_mode: bool = False,
) -> dict:
    """Call Ollama ``/api/chat`` and return ``{'text': str, 'usage': {'input', 'output'}}``.

    Reuses the Gemini retry / deadline / circuit-breaker helper so failures raise
    the same exception types (``GeminiUnavailable``, ``GeminiRetriesExhausted``,
    ``RuntimeError``) that executors already handle. Caller-supplied Gemini
    timeouts are ignored: local models are slower, so ``OLLAMA_TIMEOUT_SECONDS``
    and ``OLLAMA_TOTAL_DEADLINE_SECONDS`` apply instead.

    ``usage`` counts may be 0 — Ollama omits ``prompt_eval_count`` when the
    prompt is fully cached. Billing callers must fall back to an estimate.
    """
    from core.services.gemini_client import _gemini_request_with_retry

    model = model or get_ollama_model()
    base_url = getattr(settings, "OLLAMA_BASE_URL", "http://host.docker.internal:11434")
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

    response = _gemini_request_with_retry(
        f"{base_url.rstrip('/')}/api/chat",
        body,
        timeout=int(getattr(settings, "OLLAMA_TIMEOUT_SECONDS", 300)),
        deadline_seconds=int(getattr(settings, "OLLAMA_TOTAL_DEADLINE_SECONDS", 600)),
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
