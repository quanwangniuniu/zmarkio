"""AI Studio Gemini client — used by ad_copy_variation only.

Separate from core/services/gemini_client.py (which targets Vertex AI for the agent
pipeline). This client targets generativelanguage.googleapis.com (AI Studio)
because the GEMINI_API_KEY in the dev/prod environment is an AI Studio API key
(AIzaSy... format), incompatible with the Vertex endpoint.
"""

import json
import logging
import os
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

AISTUDIO_MODEL = "gemini-2.5-flash-lite"
AISTUDIO_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_TIMEOUT = 60


def _api_key() -> str:
    key = getattr(settings, "GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    return key


def _post(model: str, payload: dict, timeout: int) -> dict:
    url = AISTUDIO_URL.format(model=model)
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": _api_key(),
    }
    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if response.status_code != 200:
        logger.error(
            "AI Studio call failed status=%s body=%s",
            response.status_code,
            response.text[:300],
        )
        response.raise_for_status()
    return response.json()


def call_aistudio(
    system_prompt: str,
    user_prompt: str,
    model: str = AISTUDIO_MODEL,
    # 0.7 chosen for diversity: at 0.3 successive calls produced near-duplicate
    # variations; mediabuyers want fresh angles on regenerate.
    temperature: float = 0.7,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Plain-text completion against AI Studio."""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    logger.info(
        "Calling AI Studio model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )
    data = _post(model, payload, timeout)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"AI Studio returned no candidates: {data}")
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
    model: str = AISTUDIO_MODEL,
    # 0.7 chosen for diversity: at 0.3 successive calls produced near-duplicate
    # variations; mediabuyers want fresh angles on regenerate.
    temperature: float = 0.7,
    timeout: int = DEFAULT_TIMEOUT,
    _retry: bool = True,
) -> dict:
    """JSON-mode completion against AI Studio with retry on parse failure."""
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    logger.info(
        "Calling AI Studio (json) model=%s system_chars=%d user_chars=%d",
        model,
        len(system_prompt),
        len(user_prompt),
    )
    data = _post(model, payload, timeout)
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"AI Studio returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts)
    text = strip_json_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        if _retry:
            logger.warning(
                "AI Studio returned non-JSON; retrying once. err=%s body=%s",
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
