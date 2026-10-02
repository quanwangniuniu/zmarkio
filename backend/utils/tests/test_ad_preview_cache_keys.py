"""preview cache keys must be namespaced by ad account.

Two ad accounts can reuse the same creative/ad numeric id. A key that only
includes that id will leak account A's cached preview into account B.
"""

from django.core.cache import cache
from django.test import TestCase, override_settings

from utils.ad_preview_cache_keys import creative_preview_cache_key

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "med-248-preview-cache-keys",
    }
}

# Shared platform-local creative id (the collision surface).
CREATIVE_ID = 12345
ACCOUNT_A = 101
ACCOUNT_B = 202


def _buggy_unscoped_key(*, platform: str, creative_id: int | str) -> str:
    """Pre-fix key shape: no account_id → cross-account collision."""
    return f"ad_creative_preview:{platform}:{creative_id}"


@override_settings(CACHES=TEST_CACHES)
class CreativePreviewCacheKeyIsolationTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_unscoped_keys_collide_across_accounts(self):
        """Same creative id, different accounts → identical buggy keys."""
        key_a = _buggy_unscoped_key(platform="meta", creative_id=CREATIVE_ID)
        key_b = _buggy_unscoped_key(platform="meta", creative_id=CREATIVE_ID)
        self.assertEqual(key_a, key_b)

    def test_unscoped_cache_leaks_preview_across_accounts(self):
        """Reproduce the bug: account B reads account A's cached preview."""
        buggy_key = _buggy_unscoped_key(platform="meta", creative_id=CREATIVE_ID)

        cache.set(
            buggy_key,
            {
                "account_id": ACCOUNT_A,
                "iframe_src": "https://example.test/preview-account-a",
            },
            timeout=60,
        )

        # Account B looks up by creative id only — same key, wrong payload.
        leaked = cache.get(buggy_key)
        self.assertIsNotNone(leaked)
        self.assertEqual(leaked["account_id"], ACCOUNT_A)
        self.assertEqual(
            leaked["iframe_src"],
            "https://example.test/preview-account-a",
        )

    def test_scoped_keys_differ_across_accounts(self):
        key_a = creative_preview_cache_key(
            platform="meta",
            account_id=ACCOUNT_A,
            creative_id=CREATIVE_ID,
        )
        key_b = creative_preview_cache_key(
            platform="meta",
            account_id=ACCOUNT_B,
            creative_id=CREATIVE_ID,
        )
        self.assertNotEqual(key_a, key_b)
        self.assertTrue(key_a.startswith(f"ad_creative_preview:meta:{ACCOUNT_A}:"))
        self.assertTrue(key_b.startswith(f"ad_creative_preview:meta:{ACCOUNT_B}:"))

    def test_scoped_cache_isolates_previews_across_accounts(self):
        """Fix verification: same creative id does not leak across accounts."""
        key_a = creative_preview_cache_key(
            platform="meta",
            account_id=ACCOUNT_A,
            creative_id=CREATIVE_ID,
        )
        key_b = creative_preview_cache_key(
            platform="meta",
            account_id=ACCOUNT_B,
            creative_id=CREATIVE_ID,
        )

        cache.set(
            key_a,
            {
                "account_id": ACCOUNT_A,
                "iframe_src": "https://example.test/preview-account-a",
            },
            timeout=60,
        )
        cache.set(
            key_b,
            {
                "account_id": ACCOUNT_B,
                "iframe_src": "https://example.test/preview-account-b",
            },
            timeout=60,
        )

        self.assertEqual(cache.get(key_a)["account_id"], ACCOUNT_A)
        self.assertEqual(
            cache.get(key_a)["iframe_src"],
            "https://example.test/preview-account-a",
        )
        self.assertEqual(cache.get(key_b)["account_id"], ACCOUNT_B)
        self.assertEqual(
            cache.get(key_b)["iframe_src"],
            "https://example.test/preview-account-b",
        )

    def test_scoped_key_requires_account_id(self):
        with self.assertRaises(ValueError):
            creative_preview_cache_key(
                platform="meta",
                account_id="",
                creative_id=CREATIVE_ID,
            )

    def test_google_platform_also_scopes_by_account(self):
        key_a = creative_preview_cache_key(
            platform="google",
            account_id=ACCOUNT_A,
            creative_id=CREATIVE_ID,
            variant="DESKTOP",
        )
        key_b = creative_preview_cache_key(
            platform="google",
            account_id=ACCOUNT_B,
            creative_id=CREATIVE_ID,
            variant="DESKTOP",
        )
        self.assertNotEqual(key_a, key_b)
        self.assertIn(":DESKTOP", key_a)
