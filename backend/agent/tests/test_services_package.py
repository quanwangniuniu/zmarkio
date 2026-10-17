"""Guards for the layout of the agent/services/ package."""
from pathlib import Path

from django.test import SimpleTestCase

import agent.services
from agent.services import AgentOrchestrator

PACKAGE_DIR = Path(agent.services.__file__).parent

# Names callers may import from ``agent.services``. Removing or renaming one is
# an API change: update this list deliberately in the same commit.
PUBLIC_NAMES = frozenset({
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
})


class ServicesPackageShimTests(SimpleTestCase):
    def test_public_names_are_exported(self):
        self.assertEqual(set(agent.services.__all__), set(PUBLIC_NAMES))
        for name in sorted(PUBLIC_NAMES):
            with self.subTest(name=name):
                self.assertTrue(hasattr(agent.services, name))


class OrchestratorMixinTests(SimpleTestCase):
    def test_no_method_is_defined_in_two_mixins(self):
        """A method defined in two mixins is silently resolved by MRO order."""
        owners = {}
        for cls in AgentOrchestrator.__mro__:
            if cls is object:
                continue
            for name, value in vars(cls).items():
                if callable(value) and not name.startswith("__"):
                    owners.setdefault(name, []).append(cls.__name__)
        duplicates = {name: cls for name, cls in owners.items() if len(cls) > 1}
        self.assertEqual(duplicates, {})


class ServicesPackageSizeTests(SimpleTestCase):
    MAX_BYTES = 25 * 1000

    def test_no_module_exceeds_25kb(self):
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertLessEqual(path.stat().st_size, self.MAX_BYTES)
