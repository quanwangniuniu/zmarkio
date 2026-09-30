"""Normalize Ollama JSON payloads into a single object for Quick Start."""

from __future__ import annotations

from typing import Any

from core.services.quick_start.exceptions import QuickStartLLMError
from core.services.quick_start.user_messages import (
    LLM_ERROR_MALFORMED_OUTPUT,
    USER_MESSAGE_MALFORMED_OUTPUT,
)


def coerce_ollama_json_object(
    raw: Any,
    *,
    stage: str,
    expect_plan: bool = False,
    expect_blueprint: bool = False,
) -> dict[str, Any]:
    """
    Accept a dict, or common wrapper / single-element array shapes from Ollama.
    """
    if isinstance(raw, dict):
        for key in ('campaign_plan', 'plan', 'project_plan'):
            inner = raw.get(key)
            if isinstance(inner, dict) and _looks_like_plan(inner):
                return inner
        for key in ('blueprint', 'project_blueprint'):
            inner = raw.get(key)
            if isinstance(inner, dict) and _looks_like_blueprint(inner):
                return inner
        if expect_plan and not _looks_like_plan(raw) and _looks_like_blueprint(raw):
            raise QuickStartLLMError(
                f'Ollama stage "{stage}" returned a blueprint object; expected CampaignPlan.'
            )
        if expect_blueprint and not _looks_like_blueprint(raw) and _looks_like_plan(raw):
            raise QuickStartLLMError(
                f'Ollama stage "{stage}" returned a plan object; expected ProjectBlueprint.'
            )
        return raw

    if isinstance(raw, list):
        dict_items = [item for item in raw if isinstance(item, dict)]
        if len(dict_items) == 1:
            return coerce_ollama_json_object(
                dict_items[0],
                stage=stage,
                expect_plan=expect_plan,
                expect_blueprint=expect_blueprint,
            )
        raise QuickStartLLMError(
            f'Ollama stage "{stage}" returned a JSON array ({len(raw)} items); expected one object.',
            error_code=LLM_ERROR_MALFORMED_OUTPUT,
            user_message=USER_MESSAGE_MALFORMED_OUTPUT,
        )

    raise QuickStartLLMError(
        f'Ollama stage "{stage}" returned {type(raw).__name__}; expected a JSON object.',
        error_code=LLM_ERROR_MALFORMED_OUTPUT,
        user_message=USER_MESSAGE_MALFORMED_OUTPUT,
    )


def _looks_like_plan(value: dict[str, Any]) -> bool:
    return value.get('version') == 1 and 'campaign_summary' in value


def _looks_like_blueprint(value: dict[str, Any]) -> bool:
    return value.get('version') == 1 and 'project' in value and 'tasks' in value
