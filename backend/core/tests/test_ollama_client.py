"""core.services.ollama_client: /api/chat calls, text/JSON helpers, config gate."""
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from core.services import ollama_client as oc

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                       "LOCATION": "ollama-test"}}
_OLLAMA = dict(
    CACHES=_LOCMEM,
    OLLAMA_BASE_URL="http://ollama.test:11434/",
    OLLAMA_MODEL="qwen3:4b",
    OLLAMA_TIMEOUT_SECONDS=300,
    OLLAMA_TOTAL_DEADLINE_SECONDS=600,
)


def _chat_response(content, prompt_eval_count=12, eval_count=34):
    resp = MagicMock()
    data = {"message": {"role": "assistant", "content": content}}
    if prompt_eval_count is not None:
        data["prompt_eval_count"] = prompt_eval_count
    if eval_count is not None:
        data["eval_count"] = eval_count
    resp.json.return_value = data
    return resp


@override_settings(**_OLLAMA)
class CallOllamaTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("core.services.ollama_client.requests.post")
    def test_posts_chat_request_and_parses_usage(self, mock_post):
        mock_post.return_value = _chat_response("hello")

        result = oc.call_ollama("sys", "usr", temperature=0.2, max_output_tokens=512)

        self.assertEqual(result, {"text": "hello", "usage": {"input": 12, "output": 34}})
        url = mock_post.call_args.args[0]
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(url, "http://ollama.test:11434/api/chat")
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ])
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertEqual(body["options"], {"temperature": 0.2, "num_predict": 512})
        self.assertNotIn("format", body)
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 300)

    @patch("core.services.ollama_client.requests.post")
    def test_json_mode_sets_format(self, mock_post):
        mock_post.return_value = _chat_response('{"a": 1}')
        oc.call_ollama("sys", "usr", json_mode=True)
        self.assertEqual(mock_post.call_args.kwargs["json"]["format"], "json")

    @patch("core.services.ollama_client.requests.post")
    def test_strips_think_block_and_defaults_missing_usage_to_zero(self, mock_post):
        mock_post.return_value = _chat_response(
            "<think>reasoning</think>\nanswer", prompt_eval_count=None, eval_count=None
        )
        result = oc.call_ollama("sys", "usr")
        self.assertEqual(result["text"], "answer")
        self.assertEqual(result["usage"], {"input": 0, "output": 0})

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_connection_failure_raises_ollama_unavailable(self, mock_post, _sleep):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(oc.OllamaUnavailable):
            oc.call_ollama("sys", "usr")


@override_settings(**_OLLAMA)
class OllamaHelperTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_configured_when_base_url_set(self):
        self.assertEqual(oc._get_api_key(), oc.OLLAMA_KEY_SENTINEL)
        self.assertTrue(oc.is_llm_configured())

    def test_not_configured_without_base_url(self):
        with override_settings(OLLAMA_BASE_URL=""):
            self.assertEqual(oc._get_api_key(), "")
            self.assertFalse(oc.is_llm_configured())

    @patch("core.services.ollama_client.requests.post")
    def test_call_ollama_text_returns_plain_text(self, mock_post):
        mock_post.return_value = _chat_response("plain text")

        text = oc.call_ollama_text("sys", "usr", temperature=0.5, timeout=10)

        self.assertEqual(text, "plain text")
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_post.call_args.args[0], "http://ollama.test:11434/api/chat")
        body = mock_post.call_args.kwargs["json"]
        self.assertNotIn("format", body)
        self.assertEqual(body["options"]["temperature"], 0.5)

    @patch("core.services.ollama_client.requests.post")
    def test_call_ollama_json_uses_json_mode_and_strips_fences(self, mock_post):
        mock_post.return_value = _chat_response('```json\n{"ok": true}\n```')

        self.assertEqual(oc.call_ollama_json("sys", "usr"), {"ok": True})
        self.assertEqual(mock_post.call_args.kwargs["json"]["format"], "json")

    @patch("core.services.ollama_client.requests.post")
    def test_timeout_caps_per_request_timeout(self, mock_post):
        mock_post.return_value = _chat_response('{"ok": true}')

        oc.call_ollama_json("sys", "usr", timeout=45)

        self.assertLessEqual(mock_post.call_args.kwargs["timeout"], 45)

    @patch("core.services.ollama_client.time.sleep")
    @patch("core.services.ollama_client.requests.post")
    def test_json_retries_stop_when_budget_spent(self, mock_post, _sleep):
        mock_post.return_value = _chat_response("not json")

        with patch("core.services.ollama_client.time.monotonic",
                   side_effect=[0, 0, 0, 0, 100, 100, 100]):
            with self.assertRaises(RuntimeError):
                oc.call_ollama_json("sys", "usr", timeout=10)

        self.assertEqual(mock_post.call_count, 1)
