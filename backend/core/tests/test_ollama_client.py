"""Guardrails on core.services.ollama_client: request body, transient retry,
wall-clock deadline, the cache circuit breaker, and JSON parsing."""
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from core.services import ollama_client as oc

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                       "LOCATION": "oc-test"}}


def _http_error(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    if json_body is None:
        resp.json.side_effect = ValueError("not JSON")
    else:
        resp.json.return_value = json_body
    err = requests.exceptions.HTTPError(response=resp)
    m = MagicMock()
    m.raise_for_status.side_effect = err
    return m


def _ok(content, **extra):
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {"message": {"role": "assistant", "content": content}, **extra}
    return m


@override_settings(CACHES=_LOCMEM, OLLAMA_TIMEOUT_SECONDS=30,
                   OLLAMA_TOTAL_DEADLINE_SECONDS=120, OLLAMA_CB_THRESHOLD=3)
class OllamaRetryTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_retries_5xx_then_raises_unavailable(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(500)
        with self.assertRaises(oc.OllamaUnavailable):
            oc._ollama_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, oc._TRANSIENT_MAX_ATTEMPTS)
        mock_sleep.assert_any_call(2.0)
        mock_sleep.assert_any_call(4.0)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_only_busy_raises_retries_exhausted_not_runtimeerror(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(503)
        with self.assertRaises(oc.OllamaRetriesExhausted):
            oc._ollama_request_with_retry("http://x", {})
        # NOT a RuntimeError: executors catch this separately, below an except RuntimeError.
        self.assertNotIsInstance(oc.OllamaRetriesExhausted(), RuntimeError)
        self.assertEqual(mock_post.call_count, oc._TRANSIENT_MAX_ATTEMPTS)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_busy_mixed_with_other_failure_raises_unavailable(self, mock_post, mock_sleep):
        mock_post.side_effect = [_http_error(503), _http_error(500),
                                 _http_error(503), _http_error(503)]
        with self.assertRaises(oc.OllamaUnavailable):
            oc._ollama_request_with_retry("http://x", {})

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_non_transient_4xx_raises_immediately(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(400)
        with self.assertRaises(RuntimeError):
            oc._ollama_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_missing_model_raises_immediately_with_pull_hint(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(
            404, {"error": "model 'qwen3:4b-instruct' not found"})
        with self.assertRaises(RuntimeError) as ctx:
            oc._ollama_request_with_retry("http://x", {"model": "qwen3:4b-instruct"})
        self.assertNotIsInstance(ctx.exception, oc.OllamaUnavailable)
        self.assertIn("ollama pull qwen3:4b-instruct", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 1)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_wrong_path_404_points_at_base_url_not_pull(self, mock_post, mock_sleep):
        # A wrong OLLAMA_BASE_URL path returns a plain-text "404 page not found".
        mock_post.return_value = _http_error(404)
        with self.assertRaises(RuntimeError) as ctx:
            oc._ollama_request_with_retry("http://x/v1", {"model": "qwen3:4b-instruct"})
        self.assertIn("OLLAMA_BASE_URL", str(ctx.exception))
        self.assertNotIn("ollama pull", str(ctx.exception))
        self.assertEqual(mock_post.call_count, 1)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post",
           side_effect=requests.exceptions.ReadTimeout())
    def test_connection_timeout_retried(self, mock_post, mock_sleep):
        with self.assertRaises(oc.OllamaUnavailable):
            oc._ollama_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, oc._TRANSIENT_MAX_ATTEMPTS)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post",
           side_effect=requests.exceptions.ConnectionError())
    def test_server_down_raises_unavailable(self, mock_post, mock_sleep):
        with self.assertRaises(oc.OllamaUnavailable):
            oc._ollama_request_with_retry("http://x", {})

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_deadline_stops_before_max_attempts(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(500)
        # monotonic jumps past the 120s budget on the 2nd check
        with patch("core.services.ollama_client.time.monotonic",
                   side_effect=[0, 0, 1, 999, 999, 999, 999]):
            with self.assertRaises(oc.OllamaUnavailable):
                oc._ollama_request_with_retry("http://x", {})
        self.assertLess(mock_post.call_count, oc._TRANSIENT_MAX_ATTEMPTS)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_circuit_breaker_opens_and_blocks(self, mock_post, mock_sleep):
        mock_post.return_value = _http_error(500)
        # Each failed call records one failure; OLLAMA_CB_THRESHOLD=3.
        for _ in range(3):
            with self.assertRaises(oc.OllamaUnavailable):
                oc._ollama_request_with_retry("http://x", {})
        calls_after_open = mock_post.call_count
        # breaker is now open -> next call must not touch the network
        with self.assertRaises(oc.OllamaUnavailable):
            oc._ollama_request_with_retry("http://x", {})
        self.assertEqual(mock_post.call_count, calls_after_open)

    @patch("core.services.ollama_client.requests.post")
    def test_success_clears_breaker(self, mock_post):
        ok = _ok("hi")
        mock_post.return_value = ok
        oc._circuit_record(False)
        oc._circuit_record(False)
        resp = oc._ollama_request_with_retry("http://x", {})
        self.assertIs(resp, ok)
        self.assertIsNone(cache.get(oc._CB_FAIL_KEY))


@override_settings(CACHES=_LOCMEM, OLLAMA_BASE_URL="http://ollama:11434/",
                   OLLAMA_MODEL="qwen3:4b", OLLAMA_NUM_CTX=8192, OLLAMA_KEEP_ALIVE="5m")
class OllamaCallTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_chat_body_shape(self):
        body = oc._chat_body("sys", "user", 0.2, "application/json", max_output_tokens=512)
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ])
        self.assertIs(body["stream"], False)
        self.assertIs(body["think"], False)
        self.assertEqual(body["keep_alive"], "5m")
        self.assertEqual(body["format"], "json")
        self.assertEqual(body["options"],
                         {"temperature": 0.2, "num_ctx": 8192, "num_predict": 512})

    def test_chat_body_plain_text_has_no_format(self):
        body = oc._chat_body("sys", "user", 0.3)
        self.assertNotIn("format", body)
        self.assertNotIn("num_predict", body["options"])

    @patch("core.services.ollama_client.requests.post")
    def test_call_ollama_posts_to_configured_server(self, mock_post):
        mock_post.return_value = _ok("  hello  ")
        text = oc.call_ollama("sys", "user")
        self.assertEqual(text, "hello")
        self.assertEqual(mock_post.call_args.args[0], "http://ollama:11434/api/chat")

    @override_settings(OLLAMA_BASE_URL="")
    @patch("core.services.ollama_client.requests.post")
    def test_call_ollama_without_base_url_raises(self, mock_post):
        with self.assertRaises(RuntimeError):
            oc.call_ollama("sys", "user")
        mock_post.assert_not_called()

    @patch("core.services.ollama_client.requests.post")
    def test_call_ollama_json_parses_fenced_json(self, mock_post):
        mock_post.return_value = _ok('```json\n{"a": 1}\n```')
        self.assertEqual(oc.call_ollama_json("sys", "user"), {"a": 1})
        self.assertEqual(mock_post.call_args.kwargs["json"]["format"], "json")

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_call_ollama_json_retries_then_raises(self, mock_post, mock_sleep):
        mock_post.return_value = _ok("not json at all")
        with self.assertRaises(RuntimeError):
            oc.call_ollama_json("sys", "user")
        self.assertEqual(mock_post.call_count, 3)

    def test_get_base_url_strips_trailing_slash(self):
        self.assertEqual(oc._get_base_url(), "http://ollama:11434")
