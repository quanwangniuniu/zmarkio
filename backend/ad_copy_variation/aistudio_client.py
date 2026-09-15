"""Gemini client for ad_copy_variation, on the project-scoped Vertex endpoint.

Originally targeted AI Studio (generativelanguage.googleapis.com) because the
GEMINI_API_KEY was then an AI Studio key (AIzaSy...). The key has since been
replaced with a Vertex API key (AQ. prefix), which AI Studio rejects with 403, so
this now calls the same Vertex base as core/services/gemini_client.py (MED-356).
The short /v1/publishers/... path without projects/{p}/locations/{l} 404s for this
key. Module and function names are kept to avoid churn in callers and test patches.
"""

import json
import logging
import os
from typing import Optional

import requests
from django.conf import settings

from core.services.gemini_client import _GEMINI_BASE

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash-lite"
DEFAULT_TIMEOUT = 60


def _api_key() -> str:
    key = getattr(settings, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return key


def _post(model: str, payload: dict, timeout: int) -> dict:
    url = f"{_GEMINI_BASE}/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        # Header rather than ?key= so the key cannot leak into HTTPError messages.
        "x-goog-api-key": _api_key(),
    }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code != 200:
        logger.error(
            "Vertex Gemini call failed status=%s body=%s",
            response.status_code,
            response.text[:300],
        )
        response.raise_for_status()
    return response.json()


def call_aistudio(
    system_prompt: str,
    user_prompt: str,
    model: str = GEMINI_MODEL,
    # 0.7 chosen for diversity: at 0.3 successive calls produced near-duplicate
    # variations; mediabuyers want fresh angles on regenerate.
    temperature: float = 0.7,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Plain-text completion against Vertex Gemini."""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    logger.info(
        "Calling Vertex Gemini model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )
    data = _post(model, payload, timeout)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Vertex Gemini returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts)


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def call_aistudio_json(
    system_prompt: str,
    user_prompt: str,
    model: str = GEMINI_MODEL,
    # 0.7 chosen for diversity: at 0.3 successive calls produced near-duplicate
    # variations; mediabuyers want fresh angles on regenerate.
    temperature: float = 0.7,
    timeout: int = DEFAULT_TIMEOUT,
    _retry: bool = True,
) -> dict:
    """JSON-mode completion against Vertex Gemini with retry on parse failure."""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    logger.info(
        "Calling Vertex Gemini (json) model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )
    data = _post(model, payload, timeout)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Vertex Gemini returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)
    text = strip_json_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if _retry:
            logger.warning(
                "Vertex Gemini returned non-JSON; retrying once. err=%s body=%s",
                exc,
                text[:200],
            )
            return call_aistudio_json(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                timeout=timeout,
                _retry=False,
            )
        raise
