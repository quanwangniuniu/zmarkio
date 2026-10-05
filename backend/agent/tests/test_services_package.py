"""Guards for the layout of the agent/services/ package."""
import os
import pkgutil
import re
from pathlib import Path

from django.test import SimpleTestCase

import agent.services
from agent.services import AgentOrchestrator

PACKAGE_DIR = Path(agent.services.__file__).parent
BACKEND_DIR = PACKAGE_DIR.parents[1]
SUBMODULES = {m.name for m in pkgutil.iter_modules([str(PACKAGE_DIR)])}
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", "migrations", "htmlcov"}

# A string literal naming something under the package, e.g. a mock.patch target.
TARGET_RE = re.compile(r"""['"]agent\.services\.([A-Za-z_]\w*)""")

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
    "_call_ollama_analysis",
    "_call_ollama_calendar_from_analysis",
    "_call_ollama_chat",
    "_call_ollama_spreadsheet_insights",
    "_coerce_json",
    "_create_agent_status_message",
    "_enqueue_miro_generation_for_workflow_run",
    "_forward_to_users",
    "_generate_miro_board_for_workflow_run",
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


def _python_files():
    for root, dirs, files in os.walk(BACKEND_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield Path(root) / name


class ServicesPackageShimTests(SimpleTestCase):
    def test_public_names_are_exported(self):
        self.assertEqual(set(agent.services.__all__), set(PUBLIC_NAMES))
        for name in sorted(PUBLIC_NAMES):
            with self.subTest(name=name):
                self.assertTrue(hasattr(agent.services, name))

    def test_shim_reexports_come_from_defining_submodules(self):
        for name in agent.services.__all__:
            with self.subTest(name=name):
                obj = getattr(agent.services, name)
                owner = getattr(obj, "__module__", None)
                if owner is None:
                    # Constants: the value must be the one bound in some submodule.
                    self.assertTrue(any(
                        getattr(getattr(agent.services, m, None), name, object()) is obj
                        for m in SUBMODULES
                    ))
                else:
                    self.assertTrue(owner.startswith("agent.services."), owner)
                    self.assertIs(getattr(__import__(owner, fromlist=[name]), name), obj)

    def test_no_string_target_points_at_the_package_shim(self):
        """Patching ``agent.services.<helper>`` only replaces the re-exported alias,
        so the real call sites keep using the unpatched function. Target the
        defining submodule instead (``agent.services.<module>.<helper>``)."""
        offenders = []
        for path in _python_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                for first in TARGET_RE.findall(line):
                    if first not in SUBMODULES:
                        offenders.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}: agent.services.{first}")
        self.assertEqual(offenders, [], "target the defining submodule, not the package shim")


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
