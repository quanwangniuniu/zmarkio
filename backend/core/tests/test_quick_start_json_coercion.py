"""LLM JSON coercion for Quick Start."""

import pytest

from core.services.quick_start.exceptions import QuickStartValidationError
from core.services.quick_start.json_coercion import coerce_ollama_json_object
from core.services.quick_start.llm import QuickStartLLMChain
from core.tests.test_quick_start_plan import SAMPLE_CAMPAIGN_PLAN
from core.tests.test_quick_start_llm import DEFAULT_MODULES, SAMPLE_LLM_PAYLOAD


def test_unwraps_single_element_array():
    plan = coerce_ollama_json_object(
        [SAMPLE_CAMPAIGN_PLAN],
        stage='campaign_plan',
        expect_plan=True,
    )
    assert plan['campaign_summary']


def test_unwraps_wrapped_plan_key():
    plan = coerce_ollama_json_object(
        {'campaign_plan': SAMPLE_CAMPAIGN_PLAN},
        stage='campaign_plan',
        expect_plan=True,
    )
    assert plan['project']['name'] == 'Q1 Meta Launch'


def test_rejects_multi_item_array():
    from core.services.quick_start.exceptions import QuickStartLLMError

    with pytest.raises(QuickStartLLMError):
        coerce_ollama_json_object([{'a': 1}, {'b': 2}], stage='test')


def test_retries_when_first_payload_is_array():
    calls = {'n': 0}

    def _mock(**kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            return [SAMPLE_CAMPAIGN_PLAN]
        return SAMPLE_LLM_PAYLOAD

    chain = QuickStartLLMChain(call_json=_mock)
    blueprint = chain.generate_blueprint(
        prompt='US Meta Q2 campaign with $30k budget for six weeks',
        selected_modules=DEFAULT_MODULES,
    )
    assert calls['n'] == 2
    assert blueprint['project']['name'] == 'Q1 Meta Launch'
