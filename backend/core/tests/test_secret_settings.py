"""Tests for backend.secret_settings — secret validation at settings load.

Covers MED-393 and MED-394 and the PR #836 review:
- A missing or empty secret stops the boot
- A value committed to the repository is refused unless legacy keys are
  explicitly allowed (DEBUG and ALLOW_LEGACY_LOCAL_KEYS, decided in settings.py)
- General weak-value rules: django-insecure- prefix, placeholder words, minimum
  length, minimum distinct characters, and the three keys being distinct
- A Fernet key that Fernet cannot use is refused at boot (MED-394)
- Secrets are read from the process environment only
"""

import hashlib
import os
import secrets
import string
import warnings
from unittest import mock

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from backend.secret_settings import (
    COMMITTED_ORG_TOKEN_ENCRYPTION_KEY_DIGESTS,
    COMMITTED_ORG_TOKEN_SECRET_KEY_DIGESTS,
    COMMITTED_SECRET_KEY_DIGESTS,
    FERNET_GENERATE_HINT,
    MIN_DISTINCT_CHARACTERS,
    MIN_SIGNING_KEY_LENGTH,
    PLACEHOLDER_WORDS,
    TOKEN_GENERATE_HINT,
    read_bool_env,
    read_secret_env,
    validate_distinct_secrets,
    validate_fernet_key_setting,
    validate_secret_setting,
)

# Stands in for a committed value, so the tests never contain a real one. It is
# deliberately short: an allowed legacy value must not be held to the new rules.
COMMITTED_VALUE = "value-that-was-committed-to-the-repo"
COMMITTED_DIGESTS = frozenset({hashlib.sha256(COMMITTED_VALUE.encode()).hexdigest()})
# A well-formed Fernet key standing in for a committed one.
COMMITTED_FERNET_KEY = Fernet.generate_key().decode()
COMMITTED_FERNET_DIGESTS = frozenset({hashlib.sha256(COMMITTED_FERNET_KEY.encode()).hexdigest()})


def fresh_key() -> str:
    """A key made the way TOKEN_GENERATE_HINT tells people to make one."""
    return secrets.token_urlsafe(50)


def validate(value, *, allow_committed=False, digests=COMMITTED_DIGESTS, name="SECRET_KEY"):
    return validate_secret_setting(
        name, value, allow_committed=allow_committed, committed_digests=digests
    )


class MissingSecretTest(SimpleTestCase):
    def test_empty_raises_when_legacy_not_allowed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
            validate("")

    def test_empty_raises_when_legacy_allowed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
            validate("", allow_committed=True)

    def test_error_names_the_setting(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SOME_OTHER_KEY is not set"):
            validate("", name="SOME_OTHER_KEY")

    def test_error_explains_how_to_generate_a_key(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "secrets.token_urlsafe"):
            validate("")

    def test_error_uses_a_custom_hint(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "custom generation hint"):
            validate_secret_setting(
                "SECRET_KEY", "", allow_committed=False, hint="custom generation hint"
            )

    def test_whitespace_only_counts_as_not_set(self):
        for value in ("   ", "\t", "\n", " \t\n "):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
                    validate(value)

    def test_empty_quotes_count_as_not_set(self):
        for value in ('""', "''", '" "'):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
                    validate(value)


class CommittedSecretTest(SimpleTestCase):
    def test_committed_value_raises_when_legacy_not_allowed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate(COMMITTED_VALUE)

    def test_refusal_mentions_the_local_only_switch(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "ALLOW_LEGACY_LOCAL_KEYS=true"):
            validate(COMMITTED_VALUE)

    def test_committed_value_warns_when_legacy_allowed(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate(COMMITTED_VALUE, allow_committed=True)

        self.assertEqual(result, COMMITTED_VALUE)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, RuntimeWarning)
        self.assertIn("SECRET_KEY is a value that has been committed", str(caught[0].message))

    def test_allowed_legacy_value_skips_the_weak_value_rules(self):
        """COMMITTED_VALUE is shorter than the minimum; the switch must still let it boot."""
        self.assertLess(len(COMMITTED_VALUE), MIN_SIGNING_KEY_LENGTH)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.assertEqual(validate(COMMITTED_VALUE, allow_committed=True), COMMITTED_VALUE)

    def test_padded_committed_value_is_still_recognised(self):
        for value in (
            f" {COMMITTED_VALUE}",
            f"{COMMITTED_VALUE} ",
            f"{COMMITTED_VALUE}\n",
            f"\t{COMMITTED_VALUE}\t",
        ):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
                    validate(value)

    def test_quoted_committed_value_is_still_recognised(self):
        for value in (f'"{COMMITTED_VALUE}"', f"'{COMMITTED_VALUE}'", f' "{COMMITTED_VALUE}" '):
            with self.subTest(value=value):
                with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
                    validate(value)


class ReturnedValueTest(SimpleTestCase):
    """Checks see the value without surrounding whitespace and quotes; the value itself is returned unchanged."""

    def test_mismatched_quotes_are_part_of_the_key(self):
        """Only a matching pair is treated as quoting; anything else is part of the key."""
        value = f'"{fresh_key()}' + "'"
        self.assertEqual(validate(value), value)

    def test_value_is_returned_unchanged(self):
        value = f"  {fresh_key()}  "
        self.assertEqual(validate(value), value)

    def test_clean_key_is_returned_without_warning(self):
        key = fresh_key()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.assertEqual(validate(key), key)
        self.assertEqual(caught, [])


class WeakValueRulesTest(SimpleTestCase):
    def test_rule1_django_insecure_prefix_is_rejected(self):
        value = "django-insecure-" + fresh_key()
        with self.assertRaisesMessage(ImproperlyConfigured, "Django development key"):
            validate(value)

    def test_rule1_prefix_is_case_insensitive(self):
        value = "DJANGO-INSECURE-" + fresh_key()
        with self.assertRaisesMessage(ImproperlyConfigured, "Django development key"):
            validate(value)

    def test_rule2_placeholder_words_are_rejected(self):
        for word in PLACEHOLDER_WORDS:
            with self.subTest(word=word):
                value = fresh_key() + word.upper()
                with self.assertRaisesMessage(ImproperlyConfigured, "looks like a placeholder"):
                    validate(value)

    def test_rule3_too_short_is_rejected(self):
        value = (string.ascii_letters + string.digits)[: MIN_SIGNING_KEY_LENGTH - 1]
        with self.assertRaisesMessage(ImproperlyConfigured, "too short"):
            validate(value)

    def test_rule3_minimum_length_is_accepted(self):
        value = (string.ascii_letters + string.digits)[:MIN_SIGNING_KEY_LENGTH]
        self.assertEqual(validate(value), value)

    def test_rule4_repeated_characters_are_rejected(self):
        for value in ("a" * 60, "abc" * 20, "0123456789"[: MIN_DISTINCT_CHARACTERS - 1] * 10):
            with self.subTest(value=value[:12]):
                with self.assertRaisesMessage(ImproperlyConfigured, "too few distinct characters"):
                    validate(value)

    def test_generated_keys_pass_every_rule(self):
        for _ in range(20):
            key = fresh_key()
            self.assertEqual(validate(key), key)

    def test_signing_key_rules_do_not_apply_to_fernet_keys(self):
        """A valid Fernet key is 44 characters, below the signing-key minimum."""
        key = Fernet.generate_key().decode()
        self.assertLess(len(key), MIN_SIGNING_KEY_LENGTH)
        self.assertEqual(validate_fernet_key_setting("ENCRYPTION_KEY", key, allow_committed=False), key)

    def test_rule5_distinct_keys_are_accepted(self):
        validate_distinct_secrets(A=fresh_key(), B=fresh_key(), C=Fernet.generate_key().decode())

    def test_rule5_shared_value_is_rejected(self):
        shared = fresh_key()
        with self.assertRaisesMessage(ImproperlyConfigured, "A and C have the same value"):
            validate_distinct_secrets(A=shared, B=fresh_key(), C=shared)


class WarningLocationTest(SimpleTestCase):
    """The warning should point at the caller (settings.py in production use)."""

    def test_secret_setting_warning_points_at_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_secret_setting(
                "SECRET_KEY", COMMITTED_VALUE, allow_committed=True, committed_digests=COMMITTED_DIGESTS
            )

        self.assertEqual(caught[0].filename, __file__)

    def test_fernet_key_warning_points_at_caller(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                COMMITTED_FERNET_KEY,
                allow_committed=True,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

        self.assertEqual(caught[0].filename, __file__)


class FernetKeySettingTest(SimpleTestCase):
    def test_empty_raises_with_the_fernet_hint(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "ENCRYPTION_KEY is not set"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "", allow_committed=True)

        with self.assertRaisesMessage(ImproperlyConfigured, "urlsafe_b64encode"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "", allow_committed=True)

    def test_malformed_key_raises_even_when_legacy_allowed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "is not a valid Fernet key"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "not-a-fernet-key", allow_committed=True)

    def test_malformed_key_raises_when_legacy_not_allowed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "is not a valid Fernet key"):
            validate_fernet_key_setting("ENCRYPTION_KEY", "not-a-fernet-key", allow_committed=False)

    def test_token_style_key_is_rejected(self):
        """A key made with the SECRET_KEY command is the most likely mix-up."""
        with self.assertRaisesMessage(ImproperlyConfigured, "is not a valid Fernet key"):
            validate_fernet_key_setting("ENCRYPTION_KEY", fresh_key(), allow_committed=False)

    def test_padded_committed_fernet_key_is_still_recognised(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                f"{COMMITTED_FERNET_KEY} ",
                allow_committed=False,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

    def test_committed_key_raises_when_legacy_not_allowed(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                COMMITTED_FERNET_KEY,
                allow_committed=False,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

    def test_committed_key_warns_when_legacy_allowed(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_fernet_key_setting(
                "ENCRYPTION_KEY",
                COMMITTED_FERNET_KEY,
                allow_committed=True,
                committed_digests=COMMITTED_FERNET_DIGESTS,
            )

        self.assertEqual(result, COMMITTED_FERNET_KEY)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, RuntimeWarning)

    def test_valid_key_is_returned_without_warning(self):
        key = Fernet.generate_key().decode()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_fernet_key_setting("ENCRYPTION_KEY", key, allow_committed=False)

        self.assertEqual(result, key)
        self.assertEqual(caught, [])


class EnvironmentReaderTest(SimpleTestCase):
    """Secrets and the legacy switch come from os.environ only, never from a .env file."""

    def test_read_secret_env_reads_the_environment(self):
        with mock.patch.dict(os.environ, {"SOME_SECRET": "value"}):
            self.assertEqual(read_secret_env("SOME_SECRET"), "value")

    def test_read_secret_env_is_empty_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(read_secret_env("SOME_SECRET"), "")

    def test_read_bool_env_true_values(self):
        for value in ("1", "true", "TRUE", "yes", "on", " true "):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"FLAG": value}):
                    self.assertTrue(read_bool_env("FLAG"))

    def test_read_bool_env_defaults_to_false(self):
        for env in ({}, {"FLAG": ""}, {"FLAG": "0"}, {"FLAG": "false"}, {"FLAG": "no"}):
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertFalse(read_bool_env("FLAG"))


class KnownDigestListTest(SimpleTestCase):
    def test_all_entries_are_sha256_hex_digests(self):
        """Guards against a real key being pasted into the list."""
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
