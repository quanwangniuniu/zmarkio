"""Guards for the agent/services/ package split (MED-301)."""
import os
import pkgutil
import re
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


class ServicesPackageSizeTests(SimpleTestCase):
    MAX_BYTES = 25 * 1000

    def test_no_module_exceeds_25kb(self):
        for path in sorted(PACKAGE_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                self.assertLessEqual(path.stat().st_size, self.MAX_BYTES)
