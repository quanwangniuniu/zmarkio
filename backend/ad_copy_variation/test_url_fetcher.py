"""MED-506: url_fetcher must only call a self-hosted Browserless, with the token in a header."""
import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from ad_copy_variation import url_fetcher

SELF_HOSTED = {'BROWSERLESS_BASE': 'http://browserless.internal:3000'}


def _ok_response():
    return MagicMock(status_code=200, text='<html><body><p>Hello</p></body></html>')


@override_settings(BROWSERLESS_API_KEY='test-key')
class FetchUrlTextTests(SimpleTestCase):
    @patch.dict(os.environ, SELF_HOSTED)
    @patch('ad_copy_variation.url_fetcher.requests.post')
    def test_calls_configured_self_hosted_base(self, mock_post):
        mock_post.return_value = _ok_response()

        text = url_fetcher.fetch_url_text('https://example.com/landing')

        self.assertIn('Hello', text)
        mock_post.assert_called_once()
        self.assertTrue(
            mock_post.call_args.args[0].startswith('http://browserless.internal:3000/content')
        )

    @patch.dict(os.environ, {})
    @patch('ad_copy_variation.url_fetcher.requests.post')
    def test_fails_closed_without_base_and_makes_no_request(self, mock_post):
        os.environ.pop('BROWSERLESS_BASE', None)

        with self.assertRaises(RuntimeError):
            url_fetcher.fetch_url_text('https://example.com/landing')

        mock_post.assert_not_called()

    @patch.dict(os.environ, SELF_HOSTED)
    @patch('ad_copy_variation.url_fetcher.requests.post')
    def test_sends_token_in_header_not_url(self, mock_post):
        mock_post.return_value = _ok_response()

        url_fetcher.fetch_url_text('https://example.com/landing')

        args, kwargs = mock_post.call_args
        self.assertNotIn('test-key', args[0])
        self.assertNotIn('params', kwargs)
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-key')
