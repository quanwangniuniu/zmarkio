"""Guards for the agent/services/ package split (MED-301)."""
import ast
import importlib
import os
import pkgutil
import re
import warnings
from collections import defaultdict
from pathlib import Path

from django.test import SimpleTestCase

import agent.services

PACKAGE_DIR = Path(agent.services.__file__).parent
BACKEND_DIR = PACKAGE_DIR.parents[1]
SUBMODULES = {m.name for m in pkgutil.iter_modules([str(PACKAGE_DIR)])}
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", "migrations", "htmlcov"}

# A string literal naming something under the package, e.g. a mock.patch target.
TARGET_RE = re.compile(r"""['"]agent\.services\.([A-Za-z_]\w*)""")


def _python_files():
    for root, dirs, files in os.walk(BACKEND_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                yield Path(root) / name


class ServicesPackageShimTests(SimpleTestCase):
    def test_public_api_is_only_the_orchestrator(self):
        self.assertEqual(agent.services.__all__, ["AgentOrchestrator"])
        self.assertIs(
            agent.services.AgentOrchestrator,
            importlib.import_module("agent.services.orchestrator").AgentOrchestrator,
        )

    def test_deprecated_reexports_resolve_to_defining_submodule(self):
        for name, submodule in agent.services._DEPRECATED_REEXPORTS.items():
            with self.subTest(name=name):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    obj = getattr(agent.services, name)
                owner = importlib.import_module(f"agent.services.{submodule}")
                self.assertIs(getattr(owner, name), obj)

    def test_deprecated_reexport_warns_with_migration_hint(self):
        with self.assertWarnsRegex(DeprecationWarning, r"agent\.services\.analysis"):
            getattr(agent.services, "_run_analysis")

    def test_unknown_attribute_still_raises(self):
        with self.assertRaises(AttributeError):
            agent.services.does_not_exist  # noqa: B018

    def test_no_code_imports_deprecated_names_from_the_package(self):
        agent_dir = PACKAGE_DIR.parent
        offenders = []
        for path in _python_files():
            if PACKAGE_DIR in path.parents:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8", errors="ignore"))):
                if not isinstance(node, ast.ImportFrom):
                    continue
                absolute = node.level == 0 and node.module == "agent.services"
                relative = node.level > 0 and node.module == "services" and (
                    path.parents[node.level - 1] == agent_dir
                )
                if not (absolute or relative):
                    continue
                bad = {a.name for a in node.names} & agent.services._DEPRECATED_REEXPORTS.keys()
                if bad:
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}:{node.lineno}: {sorted(bad)}")
        self.assertEqual(offenders, [], "import from the defining submodule instead")

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


class ServicesPackageSizeTests(SimpleTestCase):
    MAX_BYTES = 25 * 1000

    def test_no_module_exceeds_25kb(self):
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertLessEqual(path.stat().st_size, self.MAX_BYTES)


def _intra_package_imports(path):
    """Submodule names that ``path`` imports from within agent/services/."""
    deps = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1:
            if node.module:
                deps.add(node.module.split(".")[0])
            else:
                deps.update(alias.name for alias in node.names)
                deps.add("__init__")
        elif node.level == 0 and node.module and node.module.startswith("agent.services"):
            parts = node.module.split(".")
            deps.add(parts[2] if len(parts) > 2 else "__init__")
    return deps


class ServicesMixinLayeringTests(SimpleTestCase):
    """The orchestrator composes many mixins. Keep that safe to grow:
    submodules form an acyclic import graph, only the orchestrator composes
    mixins, and no two mixins silently override each other's methods."""

    def setUp(self):
        self.graph = {
            path.stem: _intra_package_imports(path)
            for path in PACKAGE_DIR.glob("*.py")
            if path.stem != "__init__"
        }

    def test_submodules_never_import_the_package_shim(self):
        for module, deps in self.graph.items():
            with self.subTest(module=module):
                self.assertNotIn("__init__", deps)

    def test_only_the_orchestrator_imports_the_orchestrator_or_mixin_classes(self):
        for path in PACKAGE_DIR.glob("*.py"):
            if path.stem in {"__init__", "orchestrator"}:
                continue
            with self.subTest(module=path.stem):
                self.assertNotIn("orchestrator", self.graph[path.stem])
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.ImportFrom):
                        mixins = [a.name for a in node.names if a.name.endswith("Mixin")]
                        self.assertEqual(mixins, [], f"{path.stem} imports {mixins}")

    def test_submodule_import_graph_is_acyclic(self):
        visiting, done = set(), set()

        def visit(module, trail):
            if module in done or module not in self.graph:
                return
            if module in visiting:
                self.fail("import cycle: " + " -> ".join(trail + [module]))
            visiting.add(module)
            for dep in sorted(self.graph[module]):
                visit(dep, trail + [module])
            visiting.discard(module)
            done.add(module)

        for module in sorted(self.graph):
            visit(module, [])

    def test_mixins_do_not_define_overlapping_methods(self):
        from agent.services.orchestrator import AgentOrchestrator

        owners = defaultdict(list)
        for base in AgentOrchestrator.__mro__[1:]:
            if not base.__name__.endswith("Mixin"):
                continue
            for attr, value in vars(base).items():
                if callable(value) and not attr.startswith("__"):
                    owners[attr].append(base.__name__)
        clashes = {attr: names for attr, names in owners.items() if len(names) > 1}
        self.assertEqual(clashes, {}, "mixin methods shadow each other via the MRO")
