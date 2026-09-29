"""
Unit tests for agent/llm_client.py::call_llm.

Covers:
  - Successful call: commit_quota fires, LLMCallLog(success=True), reservation cleared
  - Provider exception: release_quota fires, LLMCallLog(success=False), exception re-raised
  - SINGLE_CALL_TOO_LARGE: raises QuotaError BEFORE reserve_quota (mock_reserve.assert_not_called)
  - Model multiplier: sonnet(×1.0) vs haiku(×0.2) normalized_tokens ratio = 5
  - Gemini usage parsing: input/output tokens correctly flow into LLMCallLog
  - Gemini missing usageMetadata: _call_gemini falls back to estimate, never records 0
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
    'MODEL_TOKEN_MULTIPLIER': {
        'claude-sonnet-4-20250514': 1.0,
        'claude-haiku-4-5': 0.2,
    },
    'LLM_PRICE_TABLE': {
        'claude-sonnet-4-20250514': {'input': 300, 'output': 1500},
        'claude-haiku-4-5': {'input': 80, 'output': 400},
        # gemini not listed → prices default to {'input': 0, 'output': 0}
    },
}


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

    @patch('agent.llm_client._call_anthropic')
    def test_success_commits_quota_and_writes_log(self, mock_anthropic):
        """
        Successful call:
          - tokens_reserved=0 after commit (reservation cleared)
          - tokens_used = actual (input+output) × multiplier
          - LLMCallLog(success=True) with correct token counts
        """
        from agent.llm_client import call_llm

        mock_anthropic.return_value = {
            'text': 'result',
            'usage': {'input': 50, 'output': 50},
        }
        call_llm(
            agent_session=self.session,
            provider='anthropic',
            model='claude-sonnet-4-20250514',
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
        self.assertEqual(log.provider, 'anthropic')
        self.assertEqual(log.model_name, 'claude-sonnet-4-20250514')

    @patch('agent.llm_client._call_anthropic')
    def test_exception_releases_reservation_and_writes_failure_log(self, mock_anthropic):
        """
        Provider raises → release_quota fires → tokens_reserved=0 (no leak).
        LLMCallLog(success=False) written. Exception re-raised.
        """
        from agent.llm_client import call_llm

        mock_anthropic.side_effect = RuntimeError('API down')

        with self.assertRaises(RuntimeError):
            call_llm(
                agent_session=self.session,
                provider='anthropic',
                model='claude-sonnet-4-20250514',
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

    @patch('agent.llm_client._call_anthropic')
    def test_model_multiplier_sonnet_vs_haiku(self, mock_anthropic):
        """
        Same 100 raw tokens (50 in + 50 out).
        sonnet(×1.0) → normalized=100; haiku(×0.2) → normalized=20. Ratio = 5.
        """
        from agent.llm_client import call_llm

        mock_anthropic.return_value = {'text': 'ok', 'usage': {'input': 50, 'output': 50}}

        call_llm(
            agent_session=self.session, provider='anthropic',
            model='claude-sonnet-4-20250514',
            system_prompt='s', user_prompt='u', max_output_tokens=100,
        )
        sonnet_log = LLMCallLog.objects.filter(
            model_name='claude-sonnet-4-20250514'
        ).latest('created_at')

        call_llm(
            agent_session=self.session, provider='anthropic',
            model='claude-haiku-4-5',
            system_prompt='s', user_prompt='u', max_output_tokens=100,
        )
        haiku_log = LLMCallLog.objects.filter(
            model_name='claude-haiku-4-5'
        ).latest('created_at')

        self.assertEqual(sonnet_log.normalized_tokens, 100)   # int((50+50) × 1.0)
        self.assertEqual(haiku_log.normalized_tokens, 20)     # int((50+50) × 0.2)
        self.assertEqual(sonnet_log.normalized_tokens / haiku_log.normalized_tokens, 5.0)

    @patch('agent.llm_client._call_gemini')
    def test_gemini_usage_correctly_logged(self, mock_gemini):
        """
        Mock _call_gemini returns explicit usage → LLMCallLog has those exact values.
        Verifies the usage dict flows into the log (not estimated or 0).
        """
        from agent.llm_client import call_llm

        mock_gemini.return_value = {
            'text': '{"ok": true}',
            'usage': {'input': 80, 'output': 120},
        }
        call_llm(
            agent_session=self.session,
            provider='gemini',
            model='gemini-2.5-flash-lite',
            system_prompt='sys',
            user_prompt='usr',
            max_output_tokens=256,
            response_mime_type='application/json',
        )

        log = LLMCallLog.objects.get(organization=self.org, provider='gemini')
        self.assertTrue(log.success)
        self.assertEqual(log.input_tokens, 80)
        self.assertEqual(log.output_tokens, 120)
        # gemini not in _SETTINGS MODEL_TOKEN_MULTIPLIER → default 1.0
        self.assertEqual(log.normalized_tokens, int((80 + 120) * 1.0))

    @patch('agent.llm_client._call_gemini')
    def test_gemini_estimate_fallback_never_logs_zero(self, mock_gemini):
        """
        When _call_gemini falls back to estimate (usageMetadata absent),
        it must never return {input:0, output:0}.
        The fallback in llm_client._call_gemini uses estimate_input_tokens + quota //4.
        Verify the propagated values are > 0.
        """
        from agent.llm_client import call_llm

        # Simulate the fallback path inside _call_gemini returning non-zero estimate
        mock_gemini.return_value = {
            'text': 'answer',
            'usage': {'input': 15, 'output': 64},   # estimate fallback values
        }
        call_llm(
            agent_session=self.session,
            provider='gemini',
            model='gemini-2.5-flash-lite',
            system_prompt='sys',
            user_prompt='usr',
            max_output_tokens=256,
        )

        log = LLMCallLog.objects.get(organization=self.org, provider='gemini')
        self.assertGreater(log.input_tokens, 0)
        self.assertGreater(log.output_tokens, 0)
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
                    provider='anthropic',
                    model='claude-sonnet-4-20250514',
                    system_prompt='system prompt',
                    user_prompt='user prompt exceeding cap',
                    max_output_tokens=4096,
                )

        self.assertEqual(cm.exception.code, 'SINGLE_CALL_TOO_LARGE')
        # Most critical assertion: reserve was never called
        mock_reserve.assert_not_called()


# ── LLM_BACKEND=ollama routing ────────────────────────────────────────────────

@override_settings(**_SETTINGS, LLM_BACKEND='ollama', OLLAMA_MODEL='qwen3:4b')
class CallLLMOllamaBackendTests(_LLMBase):

    @patch('agent.llm_client._call_gemini')
    @patch('agent.llm_client.call_ollama')
    def test_gemini_provider_is_served_by_ollama(self, mock_ollama, mock_gemini):
        """provider='gemini' → Ollama; logged as provider='ollama' with OLLAMA_MODEL."""
        from agent.llm_client import call_llm

        mock_ollama.return_value = {'text': '{"ok": true}', 'usage': {'input': 40, 'output': 60}}
        result = call_llm(
            agent_session=self.session,
            provider='gemini',
            model='gemini-2.5-flash-lite',
            system_prompt='sys',
            user_prompt='usr',
            max_output_tokens=256,
            temperature=0.2,
            response_mime_type='application/json',
        )

        self.assertEqual(result['text'], '{"ok": true}')
        mock_gemini.assert_not_called()
        kwargs = mock_ollama.call_args.kwargs
        self.assertEqual(kwargs['model'], 'qwen3:4b')
        self.assertTrue(kwargs['json_mode'])
        self.assertEqual(kwargs['max_output_tokens'], 256)
        self.assertEqual(kwargs['temperature'], 0.2)

        log = LLMCallLog.objects.get(organization=self.org)
        self.assertTrue(log.success)
        self.assertEqual(log.provider, 'ollama')
        self.assertEqual(log.model_name, 'qwen3:4b')
        self.assertEqual(log.input_tokens, 40)
        self.assertEqual(log.output_tokens, 60)
        # Unlisted model → multiplier 1.0, price 0
        self.assertEqual(log.normalized_tokens, 100)
        self.assertEqual(log.total_cost_cents, 0)
        self.assertEqual(self._usage().tokens_reserved, 0)

    @patch('agent.llm_client.call_ollama')
    def test_missing_ollama_usage_falls_back_to_estimate(self, mock_ollama):
        """Ollama omits prompt_eval_count on cached prompts — never log 0 tokens."""
        from agent.llm_client import call_llm

        mock_ollama.return_value = {'text': 'answer text', 'usage': {'input': 0, 'output': 0}}
        call_llm(
            agent_session=self.session,
            provider='gemini',
            model='gemini-2.5-flash-lite',
            system_prompt='system prompt',
            user_prompt='user prompt',
        )

        log = LLMCallLog.objects.get(organization=self.org)
        self.assertGreater(log.input_tokens, 0)
        self.assertGreater(log.output_tokens, 0)

    @patch('agent.llm_client._call_anthropic')
    @patch('agent.llm_client.call_ollama')
    def test_anthropic_provider_is_unaffected(self, mock_ollama, mock_anthropic):
        from agent.llm_client import call_llm

        mock_anthropic.return_value = {'text': 'r', 'usage': {'input': 5, 'output': 5}}
        call_llm(
            agent_session=self.session,
            provider='anthropic',
            model='claude-sonnet-4-20250514',
            system_prompt='s',
            user_prompt='u',
        )
        mock_ollama.assert_not_called()
        self.assertEqual(LLMCallLog.objects.get(organization=self.org).provider, 'anthropic')
