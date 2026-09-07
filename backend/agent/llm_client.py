"""
Unified LLM call entry point.
Every LLM call in the agent pipeline must go through call_llm() — bypassing it
causes token billing to be missed.

PM decisions: Confluence 'Stripe Subscription Redesign' Q1-Q19
"""
import logging
import os
from typing import Any

from django.conf import settings

from core.services.log_redaction import redact_string
from stripe_meta.exceptions import QuotaError
from stripe_meta.models import LLMCallLog
from stripe_meta.services import (
    check_quota_or_402,
    commit_quota,
    estimate_input_tokens,
    get_active_real_subscription,
    release_quota,
    reserve_quota,
    resolve_charging_org,
)

logger = logging.getLogger(__name__)


def call_llm(
    *,
    agent_session,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 4096,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    call_purpose: str = 'other',
) -> dict[str, Any]:
    """
    Unified LLM call.

    Returns {'text': str, 'usage': {'input': int, 'output': int}}.

    Flow:
      resolve org → estimate tokens → cap checks → reserve → call → commit → log
    On failure: release reservation, write failure log row, re-raise.
    All arithmetic is integer-only.
    """
    org = resolve_charging_org(agent_session)
    multiplier = settings.MODEL_TOKEN_MULTIPLIER.get(model, 1.0)

    # Pre-estimate (used for reservation and cap checks)
    input_estimate = estimate_input_tokens(system_prompt + user_prompt, model)
    estimated_total = int((input_estimate + max_output_tokens) * multiplier)

    # 1. Explicit per-call cap check (before touching quota counters)
    sub = get_active_real_subscription(org)
    if sub and sub.plan.max_tokens_per_call and estimated_total > sub.plan.max_tokens_per_call:
        raise QuotaError(
            code='SINGLE_CALL_TOO_LARGE',
            message='Single call exceeds your plan per-call token limit',
            limit=sub.plan.max_tokens_per_call,
            estimated=estimated_total,
        )

    # 2. Monthly quota check (Free blocks; Team passes to metered overage)
    allowed, err_payload = check_quota_or_402(org, estimated_total)
    if not allowed:
        raise QuotaError(**err_payload)

    # 3. Reserve — capture the month the reservation landed in so commit/release
    #    hit the same row even if the call spans midnight on the 1st.
    reserved_ym = reserve_quota(org, estimated_total)

    try:
        # 4. Dispatch to provider
        if provider == 'anthropic':
            result = _call_anthropic(model, system_prompt, user_prompt,
                                     max_output_tokens, temperature)
        elif provider == 'gemini':
            result = _call_gemini(model, system_prompt, user_prompt,
                                  max_output_tokens, temperature, response_mime_type)
        else:
            raise ValueError(f'Unknown LLM provider: {provider!r}')

        # 5. Reconcile with actual usage
        actual_input = result['usage']['input']
        actual_output = result['usage']['output']
        actual_normalized = int((actual_input + actual_output) * multiplier)
        commit_quota(org, actual_normalized, estimated_total, year_month=reserved_ym)

        # 6. Write cost log (integer arithmetic, cents per 1M tokens)
        prices = settings.LLM_PRICE_TABLE.get(model, {'input': 0, 'output': 0})
        input_cost = actual_input * prices['input'] // 1_000_000
        output_cost = actual_output * prices['output'] // 1_000_000
        LLMCallLog.objects.create(
            organization=org,
            agent_session=agent_session,
            user=getattr(agent_session, 'user', None),
            provider=provider,
            model_name=model,
            call_purpose=call_purpose,
            input_tokens=actual_input,
            output_tokens=actual_output,
            normalized_tokens=actual_normalized,
            input_cost_cents=input_cost,
            output_cost_cents=output_cost,
            total_cost_cents=input_cost + output_cost,
            success=True,
        )
        return result

    except QuotaError:
        release_quota(org, estimated_total, year_month=reserved_ym)
        raise

    except Exception as exc:
        release_quota(org, estimated_total, year_month=reserved_ym)
        LLMCallLog.objects.create(
            organization=org,
            agent_session=agent_session,
            user=getattr(agent_session, 'user', None),
            provider=provider,
            model_name=model,
            call_purpose=call_purpose,
            input_tokens=0,
            output_tokens=0,
            normalized_tokens=0,
            input_cost_cents=0,
            output_cost_cents=0,
            total_cost_cents=0,
            success=False,
            error_message=redact_string(str(exc))[:500],
        )
        raise


# ---------------------------------------------------------------------------
# Provider backends
# ---------------------------------------------------------------------------

def _call_anthropic(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    temperature: float,
) -> dict:
    import anthropic
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    client = anthropic.Anthropic(api_key=api_key)
    logger.info("_call_anthropic model=%s system_chars=%d user_chars=%d",
                model, len(system_prompt), len(user_prompt))
    message = client.messages.create(
        model=model,
        max_tokens=max_output_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return {
        'text': message.content[0].text if message.content else '',
        'usage': {
            'input': message.usage.input_tokens,
            'output': message.usage.output_tokens,
        },
    }


def _call_gemini(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int,
    temperature: float,
    response_mime_type: str | None,
) -> dict:
    """
    Non-streaming generateContent endpoint — usageMetadata is stable here.
    DO NOT use streamGenerateContent (core.services.gemini_client.call_gemini) for billing:
    its usageMetadata is unreliable across chunks.
    Retry/backoff via shared _gemini_request_with_retry helper.
    """
    from core.services.gemini_client import _get_api_key, _GEMINI_BASE, _gemini_request_with_retry

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    url = f"{_GEMINI_BASE}/{model}:generateContent?key={api_key}"   # non-streaming
    generation_config: dict = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
    }
    if response_mime_type:
        generation_config["responseMimeType"] = response_mime_type
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": generation_config,
    }

    logger.info("_call_gemini model=%s system_chars=%d user_chars=%d",
                model, len(system_prompt), len(user_prompt))

    response = _gemini_request_with_retry(url, body, timeout=None, stream=False)
    data = response.json()

    # Extract text
    text_parts: list[str] = []
    for candidate in data.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if part.get("text"):
                text_parts.append(part["text"])

    # Extract usage — must not silently record 0 if metadata is absent
    usage_meta = data.get("usageMetadata")
    if usage_meta:
        input_tokens = usage_meta.get("promptTokenCount", 0)
        output_tokens = usage_meta.get("candidatesTokenCount", 0)
    else:
        # Non-streaming endpoint should always have usageMetadata; log as error
        logger.error(
            "_call_gemini: usageMetadata missing in generateContent response "
            "for model %s — falling back to estimate; verify billing accuracy",
            model,
        )
        from stripe_meta.services import estimate_input_tokens
        input_tokens = estimate_input_tokens(system_prompt + user_prompt, model)
        output_tokens = max_output_tokens // 4   # conservative fallback

    return {
        'text': "".join(text_parts).strip(),
        'usage': {'input': input_tokens, 'output': output_tokens},
    }
