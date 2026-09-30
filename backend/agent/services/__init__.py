"""Agent services, split by domain modules (MED-301).

This package replaces the former ``agent/services.py``. Its only public name is
``AgentOrchestrator``. The private helpers that used to live in the old module
(``_run_analysis``, ``_coerce_json``, ...) are still reachable from here, but
each access emits a ``DeprecationWarning``: import them from the defining
submodule instead (e.g. ``from agent.services.analysis import _run_analysis``).

Patch helpers on the submodule that looks them up (e.g.
``agent.services.analysis._run_analysis``), never on this package: a patch here
replaces only the re-exported alias and does not reach the real call sites.
``agent/tests/test_services_package.py`` enforces this.
"""
import importlib
import warnings

from .orchestrator import AgentOrchestrator

__all__ = ["AgentOrchestrator"]

# Legacy name -> defining submodule. Resolved lazily by ``__getattr__`` below.
_DEPRECATED_REEXPORTS = {
    "_ANALYSIS_VALIDATION_MAX_ATTEMPTS": "analysis",
    "_assign_anomaly_ids": "analysis",
    "_build_criteria_text": "analysis",
    "_call_ollama_analysis": "analysis",
    "_coerce_llm_analysis_for_requested": "analysis",
    "_get_llm_client": "analysis",
    "_preprocess_spreadsheet": "analysis",
    "_resolve_analysis_columns": "analysis",
    "_run_analysis": "analysis",
    "_truncation_notice": "analysis",
    "_ANALYSIS_SYSTEM_PROMPT": "analysis_prompts",
    "_CONTEXT_BLOCK_TEMPLATE": "analysis_prompts",
    "_CRITERIA_WITH_BLOCK": "analysis_prompts",
    "_NO_CRITERIA_BLOCK": "analysis_prompts",
    "_call_ollama_calendar_from_analysis": "calendar",
    "_coerce_json": "common",
    "_create_agent_status_message": "common",
    "_FOLLOWUP_SYSTEM_PROMPT": "followup",
    "_call_ollama_chat": "followup",
    "_normalize_llm_chat_output": "followup",
    "_serialize_project_members": "followup",
    "_SPREADSHEET_INSIGHTS_SAMPLE_ROWS": "insights",
    "_SPREADSHEET_INSIGHTS_SYSTEM_PROMPT": "insights",
    "_call_ollama_spreadsheet_insights": "insights",
    "_normalize_spreadsheet_insights_result": "insights",
    "_preprocess_spreadsheet_insights": "insights",
    "_run_spreadsheet_insights": "insights",
    "_spreadsheet_insights_sample_bounds": "insights",
    "_forward_to_users": "messaging",
    "_get_or_create_bot_private_chat": "messaging",
    "MIRO_LEGACY_BG_QUEUED_MESSAGE": "miro",
    "_enqueue_miro_generation_for_workflow_run": "miro",
    "_generate_miro_board_for_workflow_run": "miro",
}


def __getattr__(name):
    submodule = _DEPRECATED_REEXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    warnings.warn(
        f"Importing {name!r} from 'agent.services' is deprecated; "
        f"import it from 'agent.services.{submodule}' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(importlib.import_module(f".{submodule}", __name__), name)


def __dir__():
    return sorted(set(globals()) | set(_DEPRECATED_REEXPORTS))
