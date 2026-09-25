"""Tests for backend.secret_settings — secret validation at settings load.

Covers the acceptance criteria from MED-393:
- A missing or empty secret stops the boot regardless of DEBUG
- A value committed to the repository is refused when DEBUG is off
- The same value only warns when DEBUG is on (local Docker development)
- Any other value is accepted unchanged
"""

import hashlib
import warnings

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from backend.secret_settings import (
    COMMITTED_SECRET_KEY_DIGESTS,
    TOKEN_GENERATE_HINT,
    validate_secret_setting,
)

# Stands in for a committed value, so the tests never contain a real one.
COMMITTED_VALUE = "value-that-was-committed-to-the-repo"
COMMITTED_DIGESTS = frozenset({hashlib.sha256(COMMITTED_VALUE.encode()).hexdigest()})


class MissingSecretTest(SimpleTestCase):
    def test_empty_raises_when_debug_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
            validate_secret_setting("SECRET_KEY", "", debug=False)

    def test_empty_raises_when_debug_on(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
            validate_secret_setting("SECRET_KEY", "", debug=True)

    def test_error_names_the_setting(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SOME_OTHER_KEY is not set"):
            validate_secret_setting("SOME_OTHER_KEY", "", debug=False)

    def test_error_explains_how_to_generate_a_key(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "secrets.token_urlsafe"):
            validate_secret_setting("SECRET_KEY", "", debug=False)

    def test_error_uses_a_custom_hint(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "custom generation hint"):
            validate_secret_setting("SECRET_KEY", "", debug=False, hint="custom generation hint")


class CommittedSecretTest(SimpleTestCase):
    def test_committed_value_raises_when_debug_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate_secret_setting(
                "SECRET_KEY", COMMITTED_VALUE, debug=False, committed_digests=COMMITTED_DIGESTS
            )

    def test_committed_value_warns_when_debug_on(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_secret_setting(
                "SECRET_KEY", COMMITTED_VALUE, debug=True, committed_digests=COMMITTED_DIGESTS
            )

        self.assertEqual(result, COMMITTED_VALUE)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, RuntimeWarning)
        self.assertIn("SECRET_KEY is a value that has been committed", str(caught[0].message))


class ValidSecretTest(SimpleTestCase):
    def test_unique_value_is_returned_without_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_secret_setting(
                "SECRET_KEY",
                "a-unique-key-for-this-environment",
                debug=False,
                committed_digests=COMMITTED_DIGESTS,
            )

        self.assertEqual(result, "a-unique-key-for-this-environment")
        self.assertEqual(caught, [])


class KnownDigestListTest(SimpleTestCase):
    def test_secret_key_entries_are_sha256_hex_digests(self):
        for digest in COMMITTED_SECRET_KEY_DIGESTS:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_default_hint_is_the_stdlib_command(self):
        self.assertIn("secrets.token_urlsafe", TOKEN_GENERATE_HINT)
