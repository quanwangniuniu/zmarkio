"""Exercise analysis through the real billed LLM caller with a mocked Ollama server."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Organization, Project
from stripe_meta.models import LLMCallLog, Plan, Subscription

from .executors import CallLLMExecutor
from .models import AgentSession
from .services.analysis import _run_analysis


@override_settings(OLLAMA_BASE_URL="http://ollama.test", OLLAMA_MODEL="qwen3:4b")
class AnalysisLLMContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Analysis contract org")
        user = get_user_model().objects.create_user(
            username="analysis-contract", email="analysis-contract@test.com",
            organization=cls.org,
        )
        project = Project.objects.create(
            name="Analysis contract project", organization=cls.org, owner=user
        )
        cls.session = AgentSession.objects.create(user=user, project=project)

        # Keep real billing enabled, with a generous plan unrelated to these checks.
        plan = Plan.objects.create(
            name="Analysis contract plan", base_price_cents=0,
            monthly_token_quota=100_000_000, max_tokens_per_call=None,
        )
        Subscription.objects.create(
            organization=cls.org, plan=plan,
            stripe_subscription_id=f"sub_analysis_contract_{cls.org.id}",
            start_date=timezone.now(), end_date=timezone.now() + timedelta(days=1),
            is_active=True, is_internal=False,
        )

    def _mock_response(self, mock_post):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"role": "assistant", "content": json.dumps({
                "anomalies": [],
                "recommended_tasks": [{
                    "type": "execution", "summary": "Review campaign performance",
                    "priority": "MEDIUM",
                }],
            })},
            "prompt_eval_count": 12,
            "eval_count": 5,
        }
        mock_post.return_value = response
        return mock_post

    @patch("core.services.ollama_client.requests.post")
    def test_executor_serializes_prompt_and_returns_parsed_analysis(self, mock_post):
        post = self._mock_response(mock_post)
        spreadsheet_data = {"rows": [{"spend": Decimal("10.50")}]}
        executor = CallLLMExecutor(
            SimpleNamespace(config={}), None, SimpleNamespace(session=self.session)
        )

        result = executor.execute({"spreadsheet_data": spreadsheet_data})

        self.assertTrue(result.success, result.error)
        self.assertEqual(result.output_data["analysis_result"], {
            "anomalies": [],
            "recommended_tasks": [{
                "type": "execution", "summary": "Review campaign performance",
                "priority": "MEDIUM",
            }],
        })
        self.assertIs(result.output_data["spreadsheet_data"], spreadsheet_data)
        self.assertEqual(post.call_args.args[0], "http://ollama.test/api/chat")
        prompt = post.call_args.kwargs["json"]["messages"][1]["content"]
        self.assertEqual(json.loads(prompt), {"rows": [{"spend": "10.50"}]})
        log = LLMCallLog.objects.get(agent_session=self.session)
        self.assertTrue(log.success)
        self.assertEqual((log.provider, log.model_name), ("ollama", "qwen3:4b"))
        self.assertEqual((log.input_tokens, log.output_tokens), (12, 5))

    @override_settings(OLLAMA_BASE_URL="")
    @patch("core.services.ollama_client.requests.post")
    def test_run_analysis_without_llm_raises_and_calls_nothing(self, mock_post):
        with self.assertRaisesMessage(RuntimeError, "No analysis provider available."):
            _run_analysis(
                {"rows": [{"spend": Decimal("10.50")}]},
                generation_outputs=["recommended_tasks"], agent_session=self.session,
            )
        mock_post.assert_not_called()
        self.assertFalse(LLMCallLog.objects.filter(agent_session=self.session).exists())
