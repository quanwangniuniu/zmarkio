""" Meta creative preview cache is namespaced by ad account."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from facebook_integration.models import FacebookConnection, MetaAdAccount
from meta_ads.models import MetaAd, MetaAdCreative, MetaAdSet, MetaCampaign
from meta_ads.services import CreativePreviewError, get_creative_preview
from utils.ad_preview_cache_keys import creative_preview_cache_key

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "meta-creative-preview-cache-tests",
    }
}

AD_FORMAT = "MOBILE_FEED_STANDARD"


def _iframe_body(src: str) -> str:
    return f'<iframe src="{src}"></iframe>'


@override_settings(CACHES=TEST_CACHES)
class GetCreativePreviewCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="preview_cache_user",
            email="preview_cache@example.com",
            password="x",
        )
        cls.connection = FacebookConnection.objects.create(
            user=cls.user, fb_user_id="fb-preview-1", is_active=True
        )
        cls.connection.set_access_token("token-a")
        cls.connection.save(update_fields=["encrypted_access_token"])

        cls.account_a = MetaAdAccount.objects.create(
            connection=cls.connection,
            meta_account_id="acct-a",
            name="Account A",
            currency="USD",
        )
        cls.account_b = MetaAdAccount.objects.create(
            connection=cls.connection,
            meta_account_id="acct-b",
            name="Account B",
            currency="USD",
        )

        cls.campaign_a = MetaCampaign.objects.create(
            ad_account=cls.account_a, meta_campaign_id="c-a", name="Camp A"
        )
        cls.adset_a = MetaAdSet.objects.create(
            campaign=cls.campaign_a, meta_adset_id="as-a", name="Adset A"
        )
        cls.creative_a = MetaAdCreative.objects.create(
            ad_account=cls.account_a,
            meta_creative_id="shared-meta-id",
            name="Creative A",
            thumbnail_url="https://example.test/a.jpg",
        )
        cls.ad_a = MetaAd.objects.create(
            adset=cls.adset_a,
            creative=cls.creative_a,
            meta_ad_id="ad-a",
            name="Ad A",
        )

        cls.campaign_b = MetaCampaign.objects.create(
            ad_account=cls.account_b, meta_campaign_id="c-b", name="Camp B"
        )
        cls.adset_b = MetaAdSet.objects.create(
            campaign=cls.campaign_b, meta_adset_id="as-b", name="Adset B"
        )
        cls.creative_b = MetaAdCreative.objects.create(
            ad_account=cls.account_b,
            meta_creative_id="shared-meta-id",
            name="Creative B",
            thumbnail_url="https://example.test/b.jpg",
        )
        cls.ad_b = MetaAd.objects.create(
            adset=cls.adset_b,
            creative=cls.creative_b,
            meta_ad_id="ad-b",
            name="Ad B",
        )

    def setUp(self):
        cache.clear()
        self.creative_a.refresh_from_db()
        self.creative_b.refresh_from_db()

    def tearDown(self):
        cache.clear()

    def test_cache_keys_include_account_id(self):
        shared = "shared-meta-id"
        self.assertEqual(self.creative_a.meta_creative_id, shared)
        self.assertEqual(self.creative_b.meta_creative_id, shared)

        key_a = creative_preview_cache_key(
            platform="meta",
            account_id=self.account_a.id,
            creative_id=shared,
            variant=AD_FORMAT,
        )
        key_b = creative_preview_cache_key(
            platform="meta",
            account_id=self.account_b.id,
            creative_id=shared,
            variant=AD_FORMAT,
        )
        self.assertIn(f":{self.account_a.id}:", key_a)
        self.assertIn(f":{self.account_b.id}:", key_b)
        self.assertNotEqual(key_a, key_b)
        self.assertTrue(key_a.endswith(f":{shared}:{AD_FORMAT}"))
        self.assertTrue(key_b.endswith(f":{shared}:{AD_FORMAT}"))

    @patch("meta_ads.services.graph_get")
    def test_second_call_uses_cache_without_graph(self, mock_graph_get):
        mock_graph_get.return_value = {
            "data": [{"body": _iframe_body("https://example.test/preview-a")}]
        }

        first = get_creative_preview(self.creative_a, AD_FORMAT)
        second = get_creative_preview(self.creative_a, AD_FORMAT)

        self.assertEqual(first["iframe_src"], "https://example.test/preview-a")
        self.assertEqual(second["iframe_src"], "https://example.test/preview-a")
        self.assertEqual(mock_graph_get.call_count, 1)

    @patch("meta_ads.services.graph_get")
    def test_previews_are_isolated_across_accounts(self, mock_graph_get):
        # Same platform creative id; only account_id differs in the cache key.
        self.assertEqual(
            self.creative_a.meta_creative_id, self.creative_b.meta_creative_id
        )

        def _side_effect(path, token, params=None):
            if path == "/ad-a/previews":
                return {
                    "data": [{"body": _iframe_body("https://example.test/preview-a")}]
                }
            if path == "/ad-b/previews":
                return {
                    "data": [{"body": _iframe_body("https://example.test/preview-b")}]
                }
            raise AssertionError(f"unexpected path {path}")

        mock_graph_get.side_effect = _side_effect

        payload_a = get_creative_preview(self.creative_a, AD_FORMAT)
        payload_b = get_creative_preview(self.creative_b, AD_FORMAT)

        self.assertEqual(payload_a["iframe_src"], "https://example.test/preview-a")
        self.assertEqual(payload_b["iframe_src"], "https://example.test/preview-b")
        self.assertNotEqual(payload_a["iframe_src"], payload_b["iframe_src"])
        self.assertEqual(mock_graph_get.call_count, 2)

        # Re-read: each account still gets its own cached payload.
        self.assertEqual(
            get_creative_preview(self.creative_a, AD_FORMAT)["iframe_src"],
            "https://example.test/preview-a",
        )
        self.assertEqual(
            get_creative_preview(self.creative_b, AD_FORMAT)["iframe_src"],
            "https://example.test/preview-b",
        )
        self.assertEqual(mock_graph_get.call_count, 2)

    @patch("meta_ads.services.graph_get")
    def test_missing_iframe_src_raises_and_does_not_cache(self, mock_graph_get):
        mock_graph_get.return_value = {
            "data": [{"body": "<div>no iframe</div>"}],
        }
        key = creative_preview_cache_key(
            platform="meta",
            account_id=self.account_a.id,
            creative_id=self.creative_a.meta_creative_id,
            variant=AD_FORMAT,
        )

        with self.assertRaises(CreativePreviewError) as ctx:
            get_creative_preview(self.creative_a, AD_FORMAT)

        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(ctx.exception.code, "missing_iframe_src")
        self.assertIsNone(cache.get(key))
        self.assertEqual(mock_graph_get.call_count, 1)

        mock_graph_get.return_value = {
            "data": [{"body": _iframe_body("https://example.test/preview-recovered")}]
        }
        payload = get_creative_preview(self.creative_a, AD_FORMAT)
        self.assertEqual(payload["iframe_src"], "https://example.test/preview-recovered")
        self.assertEqual(mock_graph_get.call_count, 2)

    @patch("meta_ads.services.graph_get")
    def test_iframe_src_accepts_single_quotes_and_spacing(self, mock_graph_get):
        mock_graph_get.return_value = {
            "data": [
                {
                    "body": "<iframe width='1' src = 'https://example.test/single-quoted'></iframe>"
                }
            ]
        }

        payload = get_creative_preview(self.creative_a, AD_FORMAT)

        self.assertEqual(payload["iframe_src"], "https://example.test/single-quoted")


@override_settings(CACHES=TEST_CACHES)
class MetaCreativeVideoSourceViewCacheTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="preview_view_user",
            email="preview_view@example.com",
            password="x",
        )
        cls.connection = FacebookConnection.objects.create(
            user=cls.user, fb_user_id="fb-preview-view", is_active=True
        )
        cls.connection.set_access_token("token-view")
        cls.connection.save(update_fields=["encrypted_access_token"])

        cls.ad_account = MetaAdAccount.objects.create(
            connection=cls.connection,
            meta_account_id="acct-view",
            name="View Account",
            currency="USD",
        )
        campaign = MetaCampaign.objects.create(
            ad_account=cls.ad_account, meta_campaign_id="c-view", name="Camp"
        )
        adset = MetaAdSet.objects.create(
            campaign=campaign, meta_adset_id="as-view", name="Adset"
        )
        cls.creative = MetaAdCreative.objects.create(
            ad_account=cls.ad_account,
            meta_creative_id="cr-view",
            name="Creative",
        )
        MetaAd.objects.create(
            adset=adset,
            creative=cls.creative,
            meta_ad_id="ad-view",
            name="Ad",
        )

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(user=self.user)
        self.url = reverse("meta-creative-video-source", args=[self.creative.id])

    def tearDown(self):
        cache.clear()

    @patch("meta_ads.services.graph_get")
    def test_view_returns_cached_preview(self, mock_graph_get):
        mock_graph_get.return_value = {
            "data": [{"body": _iframe_body("https://example.test/view-preview")}]
        }

        first = self.client.get(self.url)
        second = self.client.get(self.url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["iframe_src"], "https://example.test/view-preview")
        self.assertEqual(second.data["iframe_src"], "https://example.test/view-preview")
        self.assertEqual(mock_graph_get.call_count, 1)

    @patch("meta_ads.services.graph_get")
    def test_view_returns_502_when_iframe_src_missing(self, mock_graph_get):
        mock_graph_get.return_value = {"data": [{"body": "<iframe></iframe>"}]}

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "missing_iframe_src")
        self.assertIn("iframe src", response.data["detail"])
