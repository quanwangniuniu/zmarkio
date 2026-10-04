"""
Unit tests for agent/llm_client.py::call_llm.

Covers:
  - Successful call: commit_quota fires, LLMCallLog(success=True), reservation cleared
  - Provider exception: release_quota fires, LLMCallLog(success=False), exception re-raised
  - SINGLE_CALL_TOO_LARGE: raises QuotaError BEFORE reserve_quota (mock_reserve.assert_not_called)
  - Model multiplier: model-a(×1.0) vs model-b(×0.2) normalized_tokens ratio = 5
  - Ollama usage parsing: prompt_eval_count/eval_count flow into LLMCallLog
  - Ollama missing token counts: _call_ollama falls back to estimate, never records 0
"""
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from core.models import Organization, Project
from agent.models import AgentSession
from stripe_meta.models import Plan, Subscription, UsageMonthly, LLMCallLog
from stripe_meta.exceptions import QuotaError

User = get_user_model()

# Settings override: controls multiplier and price tables without relying on
# env-driven values. Models not listed get multiplier=1.0 (default in .get(...,1.0)).
_SETTINGS = {
    'OLLAMA_BASE_URL': 'http://ollama.test',
    'OLLAMA_MODEL': 'model-a',
    'MODEL_TOKEN_MULTIPLIER': {
        'model-a': 1.0,
        'model-b': 0.2,
    },
    'LLM_PRICE_TABLE': {
        'model-a': {'input': 300, 'output': 1500},
        'model-b': {'input': 80, 'output': 400},
        # model-c not listed → multiplier 1.0, prices {'input': 0, 'output': 0}
    },
}


def _ollama_response(content, **counts):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        'message': {'role': 'assistant', 'content': content}, **counts,
    }
    return response


class _LLMBase(TestCase):
    """
    Minimal fixture: one org, one project, one session, one generous plan.
    Plans deleted first so org auto-subscribe signal is a no-op;
    plan is then created explicitly after the org.
    """

    def setUp(self):
        Plan.objects.all().delete()

        self.org = Organization.objects.create(name='LLMOrg')
        self.user = User.objects.create_user(
            email='llm@test.com', username='llmuser', password='pw'
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='LLMProj', organization=self.org, owner=self.user
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)

        # Generous plan: quota large enough that no test hits it by accident.
        self.plan = Plan.objects.create(
            name='LLMTestPlan',
            monthly_token_quota=100_000_000,
            max_tokens_per_call=None,   # no per-call cap by default
            base_price_cents=0,
        )
        Subscription.objects.create(
            organization=self.org,
            plan=self.plan,
            stripe_subscription_id=f'sub_llm_{self.org.id}',
            start_date=timezone.now(),
            end_date=timezone.now().replace(year=2099),
            is_active=True,
            is_internal=True,
        )

    def _ym(self):
        return timezone.now().strftime('%Y-%m')

    def _usage(self):
        return UsageMonthly.objects.filter(
            organization=self.org, year_month=self._ym()
        ).first()


# ── Successful call path ───────────────────────────────────────────────────────

@override_settings(**_SETTINGS)
class CallLLMSuccessTests(_LLMBase):

    @patch('agent.llm_client._call_ollama')
    def test_success_commits_quota_and_writes_log(self, mock_ollama):
        """
        Successful call:
          - tokens_reserved=0 after commit (reservation cleared)
          - tokens_used = actual (input+output) × multiplier
          - LLMCallLog(success=True) with correct token counts
        """
        from agent.llm_client import call_llm

        mock_ollama.return_value = {
            'text': 'result',
            'usage': {'input': 50, 'output': 50},
        }
        call_llm(
            agent_session=self.session,
            system_prompt='system',
            user_prompt='user',
            max_output_tokens=200,
        )

        row = self._usage()
        self.assertIsNotNone(row)
        self.assertEqual(row.tokens_reserved, 0)
        # actual normalized = int((50+50) × 1.0) = 100
        self.assertEqual(row.tokens_used, 100)

        log = LLMCallLog.objects.get(organization=self.org)
        self.assertTrue(log.success)
        self.assertEqual(log.input_tokens, 50)
        self.assertEqual(log.output_tokens, 50)
        self.assertEqual(log.normalized_tokens, 100)
        self.assertEqual(log.provider, 'ollama')
        self.assertEqual(log.model_name, 'model-a')

    @patch('agent.llm_client._call_ollama')
    def test_exception_releases_reservation_and_writes_failure_log(self, mock_ollama):
        """
        Provider raises → release_quota fires → tokens_reserved=0 (no leak).
        LLMCallLog(success=False) written. Exception re-raised.
        """
        from agent.llm_client import call_llm

        mock_ollama.side_effect = RuntimeError('API down')

        with self.assertRaises(RuntimeError):
            call_llm(
                agent_session=self.session,
                system_prompt='system',
                user_prompt='user',
                max_output_tokens=200,
            )

        row = self._usage()
        self.assertIsNotNone(row)
        self.assertEqual(row.tokens_reserved, 0)  # released, not leaked

        log = LLMCallLog.objects.get(organization=self.org)
        self.assertFalse(log.success)
        self.assertIn('API down', log.error_message)

    @patch('agent.llm_client._call_ollama')
    def test_model_multiplier_model_a_vs_model_b(self, mock_ollama):
        """
        Same 100 raw tokens (50 in + 50 out).
        model-a(×1.0) → normalized=100; model-b(×0.2) → normalized=20. Ratio = 5.
        """
        from agent.llm_client import call_llm

        mock_ollama.return_value = {'text': 'ok', 'usage': {'input': 50, 'output': 50}}

        call_llm(
            agent_session=self.session,
            system_prompt='s', user_prompt='u', max_output_tokens=100,
        )
        a_log = LLMCallLog.objects.filter(model_name='model-a').latest('created_at')

        with override_settings(OLLAMA_MODEL='model-b'):
            call_llm(
                agent_session=self.session,
                system_prompt='s', user_prompt='u', max_output_tokens=100,
            )
        b_log = LLMCallLog.objects.filter(model_name='model-b').latest('created_at')

        self.assertEqual(a_log.normalized_tokens, 100)   # int((50+50) × 1.0)
        self.assertEqual(b_log.normalized_tokens, 20)    # int((50+50) × 0.2)
        self.assertEqual(a_log.normalized_tokens / b_log.normalized_tokens, 5.0)

    @override_settings(OLLAMA_MODEL='model-c')
    @patch('core.services.ollama_client.requests.post')
    def test_ollama_usage_correctly_logged(self, mock_post):
        """
        The real _call_ollama reads prompt_eval_count / eval_count from the
        /api/chat response → LLMCallLog has those exact values (not estimated or 0).
        """
        from agent.llm_client import call_llm

        mock_post.return_value = _ollama_response(
            '{"ok": true}', prompt_eval_count=80, eval_count=120,
        )
        result = call_llm(
            agent_session=self.session,
            system_prompt='sys',
            user_prompt='usr',
            max_output_tokens=256,
            response_mime_type='application/json',
        )

        self.assertEqual(result['text'], '{"ok": true}')
        body = mock_post.call_args.kwargs['json']
        self.assertEqual(body['model'], 'model-c')
        self.assertEqual(body['format'], 'json')
        self.assertEqual(body['options']['num_predict'], 256)
        log = LLMCallLog.objects.get(organization=self.org, provider='ollama')
        self.assertTrue(log.success)
        self.assertEqual(log.input_tokens, 80)
        self.assertEqual(log.output_tokens, 120)
        # model-c not in _SETTINGS MODEL_TOKEN_MULTIPLIER → default 1.0
        self.assertEqual(log.normalized_tokens, int((80 + 120) * 1.0))

    @patch('core.services.ollama_client.requests.post')
    def test_ollama_estimate_fallback_never_logs_zero(self, mock_post):
        """
        When the /api/chat response carries no token counts, the real
        _call_ollama falls back to estimate_input_tokens + max_output // 4.
        It must never record {input:0, output:0}.
        """
        from agent.llm_client import call_llm

        mock_post.return_value = _ollama_response('answer')
        call_llm(
            agent_session=self.session,
            system_prompt='sys',
            user_prompt='usr',
            max_output_tokens=256,
        )

        log = LLMCallLog.objects.get(organization=self.org, provider='ollama')
        self.assertGreater(log.input_tokens, 0)
        self.assertEqual(log.output_tokens, 64)
        self.assertGreater(log.normalized_tokens, 0)


# ── Quota enforcement inside call_llm ─────────────────────────────────────────

@override_settings(**_SETTINGS)
class CallLLMQuotaEnforcementTests(_LLMBase):

    def test_single_call_too_large_raises_before_reserve(self):
        """
        plan.max_tokens_per_call=10 and any realistic prompt exceeds it.
        call_llm must:
          1. Raise QuotaError(code='SINGLE_CALL_TOO_LARGE').
          2. NOT call reserve_quota (error fires before reservation).
        """
        from agent.llm_client import call_llm

        self.plan.max_tokens_per_call = 10
        self.plan.save()

        with patch('agent.llm_client.reserve_quota') as mock_reserve:
            with self.assertRaises(QuotaError) as cm:
                call_llm(
                    agent_session=self.session,
                    system_prompt='system prompt',
                    user_prompt='user prompt exceeding cap',
                    max_output_tokens=4096,
                )

        self.assertEqual(cm.exception.code, 'SINGLE_CALL_TOO_LARGE')
        # Most critical assertion: reserve was never called
        mock_reserve.assert_not_called()
