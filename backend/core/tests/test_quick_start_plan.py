"""Quick Start two-stage plan validation and LLM chain."""

from __future__ import annotations

import pytest

from core.services.quick_start.exceptions import QuickStartLLMError, QuickStartValidationError
from core.services.quick_start.llm import QuickStartLLMChain
from core.services.quick_start.plan_validation import validate_campaign_plan
from core.tests.test_quick_start_llm import DEFAULT_MODULES, SAMPLE_LLM_PAYLOAD

SAMPLE_CAMPAIGN_PLAN = {
    'version': 1,
    'campaign_summary': 'US Meta Q2 awareness campaign with prospecting and retargeting.',
    'project': {
        'name': 'Q1 Meta Launch',
        'description': 'US performance campaign',
        'objectives': ['awareness'],
        'project_type': ['paid_social'],
        'advertising_platforms': ['meta'],
    },
    'tasks_plan': [
        {
            'summary': f'Task {index}',
            'intent': f'Deliver work stream {index}',
            'type': 'execution',
            'priority': 'MEDIUM',
            'timing_hint': f'week {index}',
        }
        for index in range(1, 6)
    ],
    'spreadsheet_plan': {
        'name': 'Campaign Budget',
        'sheet_name': 'Budget',
        'columns': ['Channel', 'Budget', 'Notes'],
        'row_themes': ['Meta prospecting', 'Google search'],
    },
    'calendar_plan': [
        {
            'title': 'Kickoff',
            'description': 'Align stakeholders',
            'timing_hint': 'week 1',
            'duration_hint': '1 hour',
        }
    ],
    'decisions_plan': [
        {
            'title': 'Prospecting vs retargeting budget split',
            'description': 'Allocate $30k/month across funnel stages.',
        },
        {
            'title': 'Creative testing cadence',
            'description': 'Choose weekly vs bi-weekly creative refreshes.',
        },
    ],
}


class TestCampaignPlanValidation:
    def test_validate_sample_plan(self):
        plan = validate_campaign_plan(SAMPLE_CAMPAIGN_PLAN, DEFAULT_MODULES)
        assert plan['project']['name'] == 'Q1 Meta Launch'
        assert len(plan['tasks_plan']) == 5


class TestQuickStartTwoStageLLMChain:
    def test_generate_blueprint_calls_plan_then_blueprint(self):
        calls: list[str] = []

        def _mock(**kwargs):
            calls.append(kwargs.get('user_prompt', ''))
            if len(calls) == 1:
                return SAMPLE_CAMPAIGN_PLAN
            return SAMPLE_LLM_PAYLOAD

        chain = QuickStartLLMChain(call_json=_mock)
        blueprint = chain.generate_blueprint(
            prompt='US Meta Q2 campaign with $30k budget for six weeks on Meta',
            supplement='Include prospecting and retargeting flights',
            chips={'channels': ['meta'], 'objectives': ['awareness']},
            selected_modules=DEFAULT_MODULES,
        )

        assert len(calls) == 2
        assert 'CampaignPlan' in calls[0]
        assert 'Approved CampaignPlan' in calls[1]
        assert blueprint['project']['name'] == 'Q1 Meta Launch'
        assert len(blueprint['tasks']) == 5

    def test_ollama_failure_wrapped_on_plan_stage(self):
        def _fail(**kwargs):
            raise RuntimeError('upstream error')

        chain = QuickStartLLMChain(call_json=_fail)
        with pytest.raises(QuickStartLLMError):
            chain.generate_blueprint(
                prompt='US Meta Q2 campaign with $30k budget for six weeks',
                selected_modules=DEFAULT_MODULES,
            )

    def test_invalid_plan_rejected(self):
        def _bad_plan(**kwargs):
            return {'version': 1, 'campaign_summary': 'x', 'project': {'name': 'X'}}

        chain = QuickStartLLMChain(call_json=_bad_plan)
        with pytest.raises(QuickStartLLMError):
            chain.generate_blueprint(
                prompt='US Meta Q2 campaign with $30k budget for six weeks',
                selected_modules=DEFAULT_MODULES,
            )
