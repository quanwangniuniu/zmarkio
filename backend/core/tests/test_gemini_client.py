"""Guardrails on core.services.gemini_client._gemini_request_with_retry:
transient retry, wall-clock deadline, and the cache circuit breaker."""
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from core.services import gemini_client as gc

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                       "LOCATION": "gc-test"}}


def _http_error(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    err = requests.exceptions.HTTPError(response=resp)
    m = MagicMock()
    m.raise_for_status.side_effect = err
    return m


@override_settings(CACHES=_LOCMEM, GEMINI_TIMEOUT_SECONDS=30,
                   GEMINI_TOTAL_DEADLINE_SECONDS=120, GEMINI_CB_THRESHOLD=3)
class GeminiRetryTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post")
    def test_retries_5xx_then_raises_unavailable(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(503)
        with self.assertRaises(gc.GeminiUnavailable):
            gc._gemini_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, gc._TRANSIENT_MAX_ATTEMPTS)
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post")
    def test_pure_429_raises_retries_exhausted_not_runtimeerror(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(429)
        with self.assertRaises(gc.GeminiRetriesExhausted):
            gc._gemini_request_with_retry("http://x", {})
        # NOT a RuntimeError: executors catch this to skip, below an except RuntimeError.
        self.assertNotIsInstance(gc.GeminiRetriesExhausted(), RuntimeError)
        self.assertEqual(mock_post.call_count, gc._TRANSIENT_MAX_ATTEMPTS)

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post")
    def test_non_transient_4xx_raises_immediately(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(400)
        with self.assertRaises(RuntimeError):
            gc._gemini_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post",
           side_effect=requests.exceptions.ReadTimeout())
    def test_connection_timeout_retried(self, mock_post, mock_sleep):
        with self.assertRaises(gc.GeminiUnavailable):
            gc._gemini_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, gc._TRANSIENT_MAX_ATTEMPTS)

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post")
    def test_deadline_stops_before_max_attempts(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(503)
        # monotonic jumps past the 120s budget on the 2nd check
        with patch("core.services.gemini_client.time.monotonic",
                   side_effect=[0, 0, 1, 999, 999, 999, 999]):
            with self.assertRaises(gc.GeminiUnavailable):
                gc._gemini_request_with_retry("http://x", {})
        self.assertLess(mock_post.call_count, gc._TRANSIENT_MAX_ATTEMPTS)

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post")
    def test_circuit_breaker_opens_and_blocks(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(503)
        # Each failed call records one failure; GEMINI_CB_THRESHOLD=3.
        for _ in range(3):
            with self.assertRaises(gc.GeminiUnavailable):
                gc._gemini_request_with_retry("http://x", {})
        calls_after_open = mock_post.call_count
        # breaker is now open -> next call must not touch the network
        with self.assertRaises(gc.GeminiUnavailable):
            gc._gemini_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, calls_after_open)

    @patch("core.services.gemini_client.requests.post")
    def test_success_clears_breaker(self, mock_post):
        ok = MagicMock()
        ok.raise_for_status.return_value = None
        mock_post.return_value = ok
        gc._circuit_record(False)
        gc._circuit_record(False)
        resp = gc._gemini_request_with_retry("http://x", {})
        self.assertIs(resp, ok)
        self.assertIsNone(cache.get(gc._CB_FAIL_KEY))
