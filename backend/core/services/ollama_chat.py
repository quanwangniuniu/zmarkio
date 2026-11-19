"""Ollama chat client for project-RAG rewrite and grounded answers.

Calls POST /api/chat on the same server as the embedding client
(settings.RAG_OLLAMA_BASE_URL). This is not the ad-copy client: that one
sends an ad JSON schema and is owned by ad_copy_variation.

qwen3 thinks by default. think=false is always sent so the reply is the
answer text, not a reasoning trace. A rewrite that comes back as more than
one line is rejected by the caller and falls back to the original question.
"""
from __future__ import annotations

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_BUSY_STATUSES = frozenset({429, 503})
_RETRY_DELAYS_SECONDS = (1.0, 2.0)


class OllamaChatResponseError(RuntimeError):
    """Ollama chat returned a non-200 response or a body without message text."""


def _base_url() -> str:
    return str(getattr(settings, "RAG_OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")


def _timeout(timeout: int | None) -> int:
    if timeout:
        return int(timeout)
    return int(getattr(settings, "RAG_OLLAMA_TIMEOUT_SECONDS", 60))


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _post_chat(payload: dict, *, timeout: int | None) -> dict:
    url = f"{_base_url()}/api/chat"
    delays = _RETRY_DELAYS_SECONDS
    response = None
    for attempt in range(len(delays) + 1):
        response = requests.post(url, json=payload, timeout=_timeout(timeout))
        if response.status_code not in _BUSY_STATUSES or attempt == len(delays):
            break
        time.sleep(delays[attempt])
    if response is None or response.status_code != 200:
        status = getattr(response, "status_code", None)
        logger.error("Ollama chat call failed status=%s", status)
        raise OllamaChatResponseError(f"Ollama chat call failed with status {status}")
    try:
        data = response.json()
    except ValueError as exc:
        raise OllamaChatResponseError("Ollama chat response was not JSON") from exc
    if not isinstance(data, dict):
        raise OllamaChatResponseError("Ollama chat response was not a JSON object")
    return data


def chat(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_output_tokens: int,
    timeout: int | None = None,
    format: dict | None = None,
) -> dict:
    """One non-streaming chat completion.

    Returns text plus prompt_eval_count / eval_count when Ollama sent them.
    Either count is None when the field is missing or not a non-negative int.

    `format` is Ollama's structured-output schema. It is sent only when the
    caller passes one, so a free-text answer does not ask for JSON.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_output_tokens,
        },
    }
    if format is not None:
        payload["format"] = format
    data = _post_chat(payload, timeout=timeout)
    message = data.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise OllamaChatResponseError("Ollama chat response missing message content")
    return {
        "text": message["content"],
        "prompt_eval_count": _count(data.get("prompt_eval_count")),
        "eval_count": _count(data.get("eval_count")),
    }
