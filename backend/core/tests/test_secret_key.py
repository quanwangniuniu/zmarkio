"""Tests for backend.secret_key — SECRET_KEY validation at settings load.

Covers the acceptance criteria from MED-393:
- A missing or empty SECRET_KEY stops the boot regardless of DEBUG
- A value committed to the repository is refused when DEBUG is off
- The same value only warns when DEBUG is on (local Docker development)
- Any other value is accepted unchanged
"""

import hashlib
import warnings
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from backend import secret_key
from backend.secret_key import validate_secret_key

# Stands in for a committed value, so the tests never contain a real one.
COMMITTED_VALUE = "value-that-was-committed-to-the-repo"
COMMITTED_DIGESTS = frozenset({hashlib.sha256(COMMITTED_VALUE.encode()).hexdigest()})


class MissingSecretKeyTest(SimpleTestCase):
    def test_empty_raises_when_debug_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
            validate_secret_key("", debug=False)

    def test_empty_raises_when_debug_on(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "SECRET_KEY is not set"):
            validate_secret_key("", debug=True)

    def test_error_explains_how_to_generate_a_key(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "get_random_secret_key"):
            validate_secret_key("", debug=False)


@patch.object(secret_key, "KNOWN_COMMITTED_KEY_DIGESTS", COMMITTED_DIGESTS)
class CommittedSecretKeyTest(SimpleTestCase):
    def test_committed_value_raises_when_debug_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "committed to this repository"):
            validate_secret_key(COMMITTED_VALUE, debug=False)

    def test_committed_value_warns_when_debug_on(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_secret_key(COMMITTED_VALUE, debug=True)

        self.assertEqual(result, COMMITTED_VALUE)
        self.assertEqual(len(caught), 1)
        self.assertIs(caught[0].category, RuntimeWarning)
        self.assertIn("committed to this repository", str(caught[0].message))


@patch.object(secret_key, "KNOWN_COMMITTED_KEY_DIGESTS", COMMITTED_DIGESTS)
class ValidSecretKeyTest(SimpleTestCase):
    def test_unique_value_is_returned_without_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = validate_secret_key("a-unique-key-for-this-environment", debug=False)

        self.assertEqual(result, "a-unique-key-for-this-environment")
        self.assertEqual(caught, [])


class KnownDigestListTest(SimpleTestCase):
    def test_entries_are_sha256_hex_digests(self):
        for digest in secret_key.KNOWN_COMMITTED_KEY_DIGESTS:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
