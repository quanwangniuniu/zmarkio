"""core.services.ollama_client and the LLM_BACKEND=ollama routing in gemini_client."""
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from core.services import gemini_client as gc
from core.services import ollama_client as oc

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                       "LOCATION": "ollama-test"}}
_OLLAMA = dict(
    CACHES=_LOCMEM,
    LLM_BACKEND="ollama",
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

    @patch("core.services.gemini_client.requests.post")
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

    @patch("core.services.gemini_client.requests.post")
    def test_json_mode_sets_format(self, mock_post):
        mock_post.return_value = _chat_response('{"a": 1}')
        oc.call_ollama("sys", "usr", json_mode=True)
        self.assertEqual(mock_post.call_args.kwargs["json"]["format"], "json")

    @patch("core.services.gemini_client.requests.post")
    def test_strips_think_block_and_defaults_missing_usage_to_zero(self, mock_post):
        mock_post.return_value = _chat_response(
            "<think>reasoning</think>\nanswer", prompt_eval_count=None, eval_count=None
        )
        result = oc.call_ollama("sys", "usr")
        self.assertEqual(result["text"], "answer")
        self.assertEqual(result["usage"], {"input": 0, "output": 0})

    @patch("core.services.gemini_client.time.sleep")
    @patch("core.services.gemini_client.requests.post")
    def test_connection_failure_raises_gemini_unavailable(self, mock_post, _sleep):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(gc.GeminiUnavailable):
            oc.call_ollama("sys", "usr")


@override_settings(**_OLLAMA)
class GeminiClientOllamaRoutingTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_api_key_gate_passes_without_gemini_key(self):
        with override_settings(GEMINI_API_KEY=""), patch.dict("os.environ", {}, clear=True):
            self.assertEqual(gc._get_api_key(), oc.OLLAMA_KEY_SENTINEL)

    @patch("core.services.gemini_client.requests.post")
    def test_call_gemini_routes_to_ollama(self, mock_post):
        mock_post.return_value = _chat_response("plain text")

        text = gc.call_gemini("sys", "usr", temperature=0.5, timeout=10)

        self.assertEqual(text, "plain text")
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_post.call_args.args[0], "http://ollama.test:11434/api/chat")
        body = mock_post.call_args.kwargs["json"]
        self.assertNotIn("format", body)
        self.assertEqual(body["options"]["temperature"], 0.5)

    @patch("core.services.gemini_client.requests.post")
    def test_call_gemini_json_routes_to_ollama_in_json_mode(self, mock_post):
        mock_post.return_value = _chat_response('```json\n{"ok": true}\n```')

        self.assertEqual(gc.call_gemini_json("sys", "usr"), {"ok": True})
        self.assertEqual(mock_post.call_args.kwargs["json"]["format"], "json")


@override_settings(CACHES=_LOCMEM, LLM_BACKEND="gemini", GEMINI_API_KEY="k")
class GeminiDefaultBackendTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_default_backend_is_not_ollama(self):
        self.assertFalse(oc.is_ollama_backend())
        self.assertEqual(gc._get_api_key(), "k")

    @patch("core.services.gemini_client.requests.post")
    def test_call_gemini_still_hits_gemini(self, mock_post):
        resp = MagicMock()
        resp.iter_content.return_value = [
            b'[{"candidates":[{"content":{"parts":[{"text":"hi"}]}}]}]'
        ]
        mock_post.return_value = resp

        self.assertEqual(gc.call_gemini("sys", "usr"), "hi")
        self.assertIn("streamGenerateContent", mock_post.call_args.args[0])
