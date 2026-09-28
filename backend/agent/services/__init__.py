"""Agent services package.

Transitional re-exports while ``orchestrator.py`` is split into domain modules
(MED-301). Patch targets must use the defining submodule, not this package.
"""
from .analysis import (
    _ANALYSIS_SYSTEM_PROMPT,
    _ANALYSIS_VALIDATION_MAX_ATTEMPTS,
    _assign_anomaly_ids,
    _build_criteria_text,
    _call_gemini_analysis,
    _get_llm_client,
    _preprocess_spreadsheet,
    _resolve_analysis_columns,
    _run_analysis,
)
from .common import _coerce_json, _create_agent_status_message
from .followup import (
    _call_gemini_chat,
    _normalize_llm_chat_output,
    _serialize_project_members,
)
from .insights import (
    _normalize_spreadsheet_insights_result,
    _run_spreadsheet_insights,
    _spreadsheet_insights_sample_bounds,
)
from .messaging import _forward_to_users, _get_or_create_bot_private_chat
from .miro import (
    MIRO_LEGACY_BG_QUEUED_MESSAGE,
    _enqueue_miro_generation_for_workflow_run,
    _generate_miro_board_for_workflow_run,
)
from .orchestrator import AgentOrchestrator

__all__ = [
    "MIRO_LEGACY_BG_QUEUED_MESSAGE",
    "AgentOrchestrator",
    "_ANALYSIS_SYSTEM_PROMPT",
    "_ANALYSIS_VALIDATION_MAX_ATTEMPTS",
    "_assign_anomaly_ids",
    "_build_criteria_text",
    "_call_gemini_analysis",
    "_call_gemini_chat",
    "_coerce_json",
    "_create_agent_status_message",
    "_enqueue_miro_generation_for_workflow_run",
    "_forward_to_users",
    "_generate_miro_board_for_workflow_run",
    "_get_llm_client",
    "_get_or_create_bot_private_chat",
    "_normalize_llm_chat_output",
    "_normalize_spreadsheet_insights_result",
    "_preprocess_spreadsheet",
    "_resolve_analysis_columns",
    "_run_analysis",
    "_run_spreadsheet_insights",
    "_serialize_project_members",
    "_spreadsheet_insights_sample_bounds",
]
