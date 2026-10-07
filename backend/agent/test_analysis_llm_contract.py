"""Exercise analysis through the real billed LLM caller with a mocked Anthropic SDK."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Organization, Project
from stripe_meta.models import LLMCallLog, Plan, Subscription

from .executors import CallLLMExecutor
from .models import AgentSession
from .services import _run_analysis


@patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-anthropic-key"})
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

    def _mock_response(self, mock_anthropic):
        create_message = mock_anthropic.return_value.messages.create
        create_message.return_value = SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps({
                "anomalies": [],
                "recommended_tasks": [{
                    "type": "execution", "summary": "Review campaign performance",
                    "priority": "MEDIUM",
                }],
            }))],
            usage=SimpleNamespace(input_tokens=12, output_tokens=5),
        )
        return create_message

    @patch("anthropic.Anthropic")
    def test_executor_serializes_prompt_and_returns_parsed_analysis(self, mock_anthropic):
        create_message = self._mock_response(mock_anthropic)
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
        prompt = create_message.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(json.loads(prompt), {"rows": [{"spend": "10.50"}]})
        self.assertTrue(LLMCallLog.objects.get(agent_session=self.session).success)

    @patch("core.services.gemini_client._get_api_key", return_value="")
    @patch("anthropic.Anthropic")
    def test_claude_fallback_parses_text_before_validating_tasks(self, mock_anthropic, _mock_key):
        create_message = self._mock_response(mock_anthropic)

        result = _run_analysis(
            {"rows": [{"spend": Decimal("10.50")}]},
            generation_outputs=["recommended_tasks"], agent_session=self.session,
        )

        self.assertEqual(result["recommended_tasks"], [{
            "type": "execution", "summary": "Review campaign performance",
            "priority": "MEDIUM",
        }])
        self.assertTrue(result["anomalies_confirmed"])
        prompt = create_message.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(json.loads(prompt), {"rows": [{"spend": "10.50"}]})
        log = LLMCallLog.objects.get(agent_session=self.session)
        self.assertTrue(log.success)
        self.assertEqual((log.input_tokens, log.output_tokens), (12, 5))
