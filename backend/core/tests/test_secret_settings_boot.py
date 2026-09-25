"""Boot tests for secret settings — backend/settings.py wired to the validators.

test_secret_settings.py covers the validators in isolation. These tests load
the real settings module in a fresh interpreter with controlled environment
variables, so they fail if settings.py stops calling a validator, reads the
wrong variable, or regains a fallback (MED-393, MED-394).
"""

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.test import SimpleTestCase

LOAD_SETTINGS = "import django; django.setup()"


def _fresh_keys():
    return {
        "SECRET_KEY": "boot-test-secret-key",
        "ORGANIZATION_ACCESS_TOKEN_SECRET_KEY": "boot-test-org-token-key",
        "ORGANIZATION_ACCESS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }


def _load_settings(debug, unset=(), **overrides):
    env = dict(os.environ)
    env.update(_fresh_keys())
    env.update(overrides)
    for name in unset:
        env.pop(name, None)
    env["DEBUG"] = "True" if debug else "False"
    env["DJANGO_SETTINGS_MODULE"] = "backend.settings"
    return subprocess.run(
        [sys.executable, "-c", LOAD_SETTINGS],
        cwd=settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class SecretSettingsBootTest(SimpleTestCase):
    def assertBoots(self, result):
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def assertRefusesToBoot(self, result, message):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImproperlyConfigured", result.stderr)
        self.assertIn(message, result.stderr)

    def test_boots_with_fresh_keys_when_debug_off(self):
        self.assertBoots(_load_settings(debug=False))

    def test_boots_with_fresh_keys_when_debug_on(self):
        self.assertBoots(_load_settings(debug=True))

    def test_each_key_is_required(self):
        """An unset variable must not fall back to a default value."""
        # python-decouple falls back to a .env or settings.ini found above the
        # settings module. Inside the Docker container (local and CI) there is
        # none; a native run next to the repository's .env would read it.
        base = Path(settings.BASE_DIR)
        for directory in (base, *base.parents):
            if (directory / ".env").exists() or (directory / "settings.ini").exists():
                self.skipTest(f"python-decouple would read {directory}")

        for name in _fresh_keys():
            with self.subTest(name=name):
                self.assertRefusesToBoot(
                    _load_settings(debug=True, unset=[name]), f"{name} is not set"
                )

    def test_each_key_must_be_non_empty(self):
        for name in _fresh_keys():
            with self.subTest(name=name):
                self.assertRefusesToBoot(
                    _load_settings(debug=True, **{name: ""}), f"{name} is not set"
                )

    def test_whitespace_only_key_is_treated_as_missing(self):
        self.assertRefusesToBoot(
            _load_settings(debug=False, SECRET_KEY="   "), "SECRET_KEY is not set"
        )

    def test_malformed_encryption_key_is_rejected(self):
        self.assertRefusesToBoot(
            _load_settings(debug=True, ORGANIZATION_ACCESS_TOKEN_ENCRYPTION_KEY="not-a-fernet-key"),
            "is not a valid Fernet key",
        )
