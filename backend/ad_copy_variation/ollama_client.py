"""Ollama client — used by ad_copy_variation only.

Separate from core/services/gemini_client.py (which targets Vertex AI for the agent
pipeline). This client calls a local Ollama server (/api/chat), configured by the
same OLLAMA_BASE_URL / OLLAMA_MODEL / OLLAMA_REQUEST_TIMEOUT_MS variables that the
variations-studio-api Ollama provider reads.
"""

import json
import logging
import os
import time
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT_MS = 60000
DEFAULT_RETRY_DELAYS_MS = "2000,4000"
DEFAULT_KEEP_ALIVE = "30m"
# 429 is a rate limit; Ollama answers 503 when its request queue is full.
BUSY_STATUSES = (429, 503)

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string"},
        "headline": {"type": "string"},
        "description": {"type": "string"},
        "cta": {"type": "string"},
    },
    "required": ["hook", "headline", "description", "cta"],
    "additionalProperties": False,
}


def _setting(name: str, default: str) -> str:
    value = getattr(settings, name, "") or os.environ.get(name, "")
    return str(value).strip() or default


def ollama_model() -> str:
    return _setting("OLLAMA_MODEL", DEFAULT_MODEL)


def _timeout_seconds() -> float:
    return float(_setting("OLLAMA_REQUEST_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS))) / 1000


def _retry_delays_seconds() -> list:
    delays = _setting("OLLAMA_RETRY_DELAYS_MS", DEFAULT_RETRY_DELAYS_MS)
    return [float(delay) / 1000 for delay in delays.split(",")]


def _post(payload: dict, timeout: Optional[float]) -> dict:
    url = f"{_setting('OLLAMA_BASE_URL', DEFAULT_BASE_URL).rstrip('/')}/api/chat"
    timeout = timeout or _timeout_seconds()
    delays = _retry_delays_seconds()
    for attempt in range(len(delays) + 1):
        response = requests.post(url, json=payload, timeout=timeout)
        if response.status_code not in BUSY_STATUSES or attempt == len(delays):
            break
        time.sleep(delays[attempt])
    if response.status_code != 200:
        logger.error(
            "Ollama call failed status=%s body=%s",
            response.status_code,
            response.text[:300],
        )
        response.raise_for_status()
    return response.json()


def _message_text(data: dict) -> str:
    message = data.get("message") or {}
    if not isinstance(message.get("content"), str):
        raise RuntimeError(f"Ollama returned no message: {data}")
    return message["content"]


def call_ollama(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    # 0.7 chosen for diversity: at 0.3 successive calls produced near-duplicate
    # variations; mediabuyers want fresh angles on regenerate.
    temperature: float = 0.7,
    timeout: Optional[float] = None,
) -> str:
    """Plain-text completion against Ollama."""
    model = model or ollama_model()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "think": False,
        "keep_alive": _setting("OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE),
        "options": {"temperature": temperature},
    }
    logger.info(
        "Calling Ollama model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )
    return _message_text(_post(payload, timeout))


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def call_ollama_json(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    # 0.7 chosen for diversity: at 0.3 successive calls produced near-duplicate
    # variations; mediabuyers want fresh angles on regenerate.
    temperature: float = 0.7,
    timeout: Optional[float] = None,
    _retry: bool = True,
) -> dict:
    """Ad copy JSON completion against Ollama with retry on parse failure."""
    model = model or ollama_model()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "think": False,
        "keep_alive": _setting("OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE),
        "format": COPY_SCHEMA,
        "options": {"temperature": temperature},
    }
    logger.info(
        "Calling Ollama (json) model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )
    text = strip_json_fences(_message_text(_post(payload, timeout)))
    try:
        copy = json.loads(text)
        if not isinstance(copy, dict):
            raise RuntimeError("Ollama returned non-object JSON")
        return copy
    except (json.JSONDecodeError, RuntimeError) as exc:
        if _retry:
            logger.warning(
                "Ollama returned invalid JSON; retrying once. err=%s body=%s",
                exc,
                text[:200],
            )
            return call_ollama_json(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                timeout=timeout,
                _retry=False,
            )
        raise
