"""Tests for backend.secret_settings — secret validation at settings load.

Covers the acceptance criteria from MED-393 and MED-394:
- A missing or empty secret stops the boot regardless of DEBUG
- A value committed to the repository is refused when DEBUG is off
- The same value only warns when DEBUG is on (local Docker development)
- Any other value is accepted unchanged
- A Fernet key that Fernet cannot use is refused at boot (MED-394)
- Surrounding whitespace or quotes do not bypass either check
"""

import hashlib
import warnings

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from backend.secret_settings import (
    COMMITTED_ORG_TOKEN_ENCRYPTION_KEY_DIGESTS,
    COMMITTED_ORG_TOKEN_SECRET_KEY_DIGESTS,
    COMMITTED_SECRET_KEY_DIGESTS,
    FERNET_GENERATE_HINT,
    TOKEN_GENERATE_HINT,
    validate_fernet_key_setting,
    validate_secret_setting,
)

# Stands in for a committed value, so the tests never contain a real one.
COMMITTED_VALUE = "value-that-was-committed-to-the-repo"
COMMITTED_DIGESTS = frozenset({hashlib.sha256(COMMITTED_VALUE.encode()).hexdigest()})
# A well-formed Fernet key standing in for a committed one.
COMMITTED_FERNET_KEY = Fernet.generate_key().decode()
COMMITTED_FERNET_DIGESTS = frozenset({hashlib.sha256(COMMITTED_FERNET_KEY.encode()).hexdigest()})


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


class NormalizedSecretTest(SimpleTestCase):
    """Surrounding whitespace and quotes must not bypass either check."""

    def test_whitespace_only_counts_as_not_set(self):
        for value in ("   ", "\t", "\n", " \t\n "):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
                    validate_secret_setting("SECRET_KEY", value, debug=False)

    def test_empty_quotes_count_as_not_set(self):
        for value in ('""', "''", '" "'):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
                    validate_secret_setting("SECRET_KEY", value, debug=False)

    def test_padded_committed_value_is_still_rejected(self):
        for value in (
            f" {COMMITTED_VALUE}",
            f"{COMMITTED_VALUE} ",
            f"{COMMITTED_VALUE}\n",
            f"\t{COMMITTED_VALUE}\t",
        ):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
                    validate_secret_setting(
                        "SECRET_KEY", value, debug=False, committed_digests=COMMITTED_DIGESTS
                    )

    def test_quoted_committed_value_is_still_rejected(self):
        for value in (f'"{COMMITTED_VALUE}"', f"'{COMMITTED_VALUE}'", f' "{COMMITTED_VALUE}" '):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
                    validate_secret_setting(
                        "SECRET_KEY", value, debug=False, committed_digests=COMMITTED_DIGESTS
                    )

    def test_mismatched_quotes_are_not_stripped(self):
        """Only a matching pair is treated as quoting; anything else is part of the key."""
        value = f'"{COMMITTED_VALUE}' + "'"
        result = validate_secret_setting(
            "SECRET_KEY", value, debug=False, committed_digests=COMMITTED_DIGESTS
        )
        self.assertEqual(result, value)

    def test_value_is_returned_unchanged(self):
        value = "  a-unique-key-with-padding  "
        result = validate_secret_setting("SECRET_KEY", value, debug=False)
        self.assertEqual(result, value)

    def test_padded_committed_fernet_key_is_still_rejected(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                f"{COMMITTED_FERNET_KEY} ",
                debug=False,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )


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


class WarningLocationTest(SimpleTestCase):
    """The warning should point at the caller (settings.py in production use)."""

    def test_secret_setting_warning_points_at_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_secret_setting(
                "SECRET_KEY", COMMITTED_VALUE, debug=True, committed_digests=COMMITTED_DIGESTS
            )

        self.assertEqual(caught[0].filename, __file__)

    def test_fernet_key_warning_points_at_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                COMMITTED_FERNET_KEY,
                debug=True,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

        self.assertEqual(caught[0].filename, __file__)


class FernetKeySettingTest(SimpleTestCase):
    def test_empty_raises_with_the_fernet_hint(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "ENCRYPTION_KEY is not set"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "", debug=True)

        with self.assertRaisesMessage(ImproperlyConfigured, "urlsafe_b64encode"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "", debug=True)

    def test_malformed_key_raises_when_debug_on(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "is not a valid Fernet key"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "not-a-fernet-key", debug=True)

    def test_malformed_key_raises_when_debug_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "is not a valid Fernet key"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "not-a-fernet-key", debug=False)

    def test_token_style_key_is_rejected(self):
        """A key made with the SECRET_KEY command is the most likely mix-up."""
        with self.assertRaisesMessage(ImproperlyConfigured, "is not a valid Fernet key"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "a" * 67, debug=False)

    def test_committed_key_raises_when_debug_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                COMMITTED_FERNET_KEY,
                debug=False,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

    def test_committed_key_warns_when_debug_on(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                COMMITTED_FERNET_KEY,
                debug=True,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

        self.assertEqual(result, COMMITTED_FERNET_KEY)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, RuntimeWarning)

    def test_valid_key_is_returned_without_warning(self):
        key = Fernet.generate_key().decode()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_fernet_key_setting("ENCRYPTION_KEY", key, debug=False)

        self.assertEqual(result, key)
        self.assertEqual(caught, [])


class KnownDigestListTest(SimpleTestCase):
    def test_all_entries_are_sha256_hex_digests(self):
        for digests in (
            COMMITTED_SECRET_KEY_DIGESTS,
            COMMITTED_ORG_TOKEN_SECRET_KEY_DIGESTS,
            COMMITTED_ORG_TOKEN_ENCRYPTION_KEY_DIGESTS,
        ):
            for digest in digests:
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_fernet_hint_generates_a_fernet_key(self):
        self.assertIn("urlsafe_b64encode(os.urandom(32))", FERNET_GENERATE_HINT)

    def test_default_hint_is_the_stdlib_command(self):
        self.assertIn("secrets.token_urlsafe", TOKEN_GENERATE_HINT)
