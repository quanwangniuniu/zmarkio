"""Boot tests for secret settings — backend/settings.py wired to the validators.

test_secret_settings.py covers the validators in isolation. These tests load
the real settings module in a fresh interpreter with controlled environment
variables, so they fail if settings.py stops calling a validator, reads the
wrong variable, reads a key through python-decouple (which also searches parent
directories for a .env file), or regains a fallback (MED-393, MED-394, PR #836).
"""

import os
import subprocess
import sys

from cryptography.fernet import Fernet
from django.conf import settings
from django.test import SimpleTestCase

LOAD_SETTINGS = "import django; django.setup()"
SECRET_NAMES = (
    "SECRET_KEY",
    "ORGANIZATION_ACCESS_TOKEN_SECRET_KEY",
    "ORGANIZATION_ACCESS_TOKEN_ENCRYPTION_KEY",
)


def _fresh_keys():
    return {
        "SECRET_KEY": "boot-test-secret-key",
        "ORGANIZATION_ACCESS_TOKEN_SECRET_KEY": "boot-test-org-token-key",
        "ORGANIZATION_ACCESS_TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }


def _load_settings(debug, unset=(), code=LOAD_SETTINGS, **overrides):
    env = dict(os.environ)
    env.pop("ALLOW_LEGACY_LOCAL_KEYS", None)
    env.update(_fresh_keys())
    env.update(overrides)
    for name in unset:
        env.pop(name, None)
    env["DEBUG"] = "True" if debug else "False"
    env["DJANGO_SETTINGS_MODULE"] = "backend.settings"
    return subprocess.run(
        [sys.executable, "-c", code],
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
        for name in SECRET_NAMES:
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

    def test_keys_are_not_read_through_decouple(self):
        """decouple may pick up a .env from a parent directory; secrets must bypass it."""
        spy = (
            "import decouple; seen = []; original = decouple.AutoConfig.__call__\n"
            "def record(self, option, *args, **kwargs):\n"
            "    seen.append(option)\n"
            "    return original(self, option, *args, **kwargs)\n"
            "decouple.AutoConfig.__call__ = record\n"
            "import django; django.setup()\n"
            "print(sorted(set(seen) & %r))\n"
        ) % ({*SECRET_NAMES, "ALLOW_LEGACY_LOCAL_KEYS"},)
        result = _load_settings(debug=False, code=spy)
        self.assertBoots(result)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "[]")

    def test_legacy_switch_needs_both_debug_and_the_flag(self):
        """ALLOW_LEGACY_LOCAL_KEYS is honoured only together with DEBUG=True."""
        code = LOAD_SETTINGS + "; from django.conf import settings as s; print(s.ALLOW_LEGACY_LOCAL_KEYS)"
        cases = [
            (False, None, "False"),
            (False, "true", "False"),
            (True, None, "False"),
            (True, "false", "False"),
            (True, "true", "True"),
        ]
        for debug, flag, expected in cases:
            with self.subTest(debug=debug, flag=flag):
                overrides = {} if flag is None else {"ALLOW_LEGACY_LOCAL_KEYS": flag}
                result = _load_settings(debug=debug, code=code, **overrides)
                self.assertBoots(result)
                self.assertEqual(result.stdout.strip().splitlines()[-1], expected)
