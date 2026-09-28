"""Agent services, split by domain modules (MED-301).

This package replaces the former ``agent/services.py``. The imports below are a
thin re-export shim so every name that was importable from ``agent.services``
still is. New code should import from the defining submodule instead.

Patch helpers on the submodule that looks them up (e.g.
``agent.services.analysis._run_analysis``), never on this package: a patch here
replaces only the re-exported alias and does not reach the real call sites.
``agent/tests/test_services_package.py`` enforces this.
"""
from .analysis import (
    _ANALYSIS_VALIDATION_MAX_ATTEMPTS,
    _assign_anomaly_ids,
    _build_criteria_text,
    _call_gemini_analysis,
    _call_llm,
    _coerce_llm_analysis_for_requested,
    _get_llm_client,
    _preprocess_spreadsheet,
    _resolve_analysis_columns,
    _run_analysis,
    _truncation_notice,
)
from .analysis_prompts import (
    _ANALYSIS_SYSTEM_PROMPT,
    _CONTEXT_BLOCK_TEMPLATE,
    _CRITERIA_WITH_BLOCK,
    _NO_CRITERIA_BLOCK,
)
from .calendar import _call_gemini_calendar_from_analysis
from .common import _coerce_json, _create_agent_status_message
from .followup import (
    _FOLLOWUP_SYSTEM_PROMPT,
    _call_gemini_chat,
    _normalize_llm_chat_output,
    _serialize_project_members,
)
from .insights import (
    _SPREADSHEET_INSIGHTS_SAMPLE_ROWS,
    _SPREADSHEET_INSIGHTS_SYSTEM_PROMPT,
    _call_gemini_spreadsheet_insights,
    _normalize_spreadsheet_insights_result,
    _preprocess_spreadsheet_insights,
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
    "AgentOrchestrator",
    "MIRO_LEGACY_BG_QUEUED_MESSAGE",
    "_ANALYSIS_SYSTEM_PROMPT",
    "_ANALYSIS_VALIDATION_MAX_ATTEMPTS",
    "_CONTEXT_BLOCK_TEMPLATE",
    "_CRITERIA_WITH_BLOCK",
    "_FOLLOWUP_SYSTEM_PROMPT",
    "_NO_CRITERIA_BLOCK",
    "_SPREADSHEET_INSIGHTS_SAMPLE_ROWS",
    "_SPREADSHEET_INSIGHTS_SYSTEM_PROMPT",
    "_assign_anomaly_ids",
    "_build_criteria_text",
    "_call_gemini_analysis",
    "_call_gemini_calendar_from_analysis",
    "_call_gemini_chat",
    "_call_gemini_spreadsheet_insights",
    "_call_llm",
    "_coerce_json",
    "_coerce_llm_analysis_for_requested",
    "_create_agent_status_message",
    "_enqueue_miro_generation_for_workflow_run",
    "_forward_to_users",
    "_generate_miro_board_for_workflow_run",
    "_get_llm_client",
    "_get_or_create_bot_private_chat",
    "_normalize_llm_chat_output",
    "_normalize_spreadsheet_insights_result",
    "_preprocess_spreadsheet",
    "_preprocess_spreadsheet_insights",
    "_resolve_analysis_columns",
    "_run_analysis",
    "_run_spreadsheet_insights",
    "_serialize_project_members",
    "_spreadsheet_insights_sample_bounds",
    "_truncation_notice",
]
