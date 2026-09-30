"""Tests for post-LLM Ollama output repair."""

from __future__ import annotations

from core.services.quick_start.repair import repair_blueprint, repair_campaign_plan, repair_iso_datetime
from core.services.quick_start.validation import validate_and_normalize_blueprint
from core.tests.test_quick_start_llm import DEFAULT_MODULES, SAMPLE_LLM_PAYLOAD


class TestRepairIsoDatetime:
    def test_date_only_becomes_datetime(self):
        assert repair_iso_datetime('2026-07-01') == '2026-07-01T09:00:00Z'

    def test_space_separated_datetime(self):
        result = repair_iso_datetime('2026-07-01 14:30:00')
        assert result is not None
        assert result.startswith('2026-07-01T14:30:00')


class TestRepairBlueprint:
    def test_normalizes_invalid_task_type(self):
        payload = {
            **SAMPLE_LLM_PAYLOAD,
            'tasks': [
                {**SAMPLE_LLM_PAYLOAD['tasks'][0], 'type': 'planning'},
                *SAMPLE_LLM_PAYLOAD['tasks'][1:],
            ],
        }
        repaired = repair_blueprint(payload, DEFAULT_MODULES)
        assert repaired['tasks'][0]['type'] == 'execution'

    def test_repairs_calendar_date_only(self):
        payload = {
            **SAMPLE_LLM_PAYLOAD,
            'calendar_events': [
                {
                    'title': 'Launch',
                    'start_datetime': '2026-07-08',
                    'end_datetime': '2026-07-08',
                }
            ],
        }
        repaired = repair_blueprint(payload, DEFAULT_MODULES)
        assert repaired['calendar_events'][0]['start_datetime'].endswith('Z')

    def test_repairs_spreadsheet_string_columns(self):
        payload = {
            **SAMPLE_LLM_PAYLOAD,
            'spreadsheets': [
                {
                    'name': 'Budget',
                    'sheets': [
                        {
                            'name': 'Plan',
                            'columns': ['Date', 'Channel', 'Spend'],
                            'rows': [['2026-07-01', 'Meta', '1000']],
                        }
                    ],
                }
            ],
        }
        blueprint = validate_and_normalize_blueprint(payload, DEFAULT_MODULES)
        columns = blueprint['spreadsheets'][0]['sheets'][0]['columns']
        assert len(columns) == 3
        assert columns[0]['name'] == 'Date'


class TestRepairCampaignPlan:
    def test_repairs_summary_alias(self):
        plan = repair_campaign_plan(
            {
                'version': 1,
                'summary': 'Cross-channel launch',
                'project': {'title': 'EcoThread Q3'},
                'tasks_plan': [{'summary': 'Kickoff', 'type': 'strategy'}],
            },
            {'tasks': True, 'spreadsheet': False, 'calendar': False, 'decisions': False, 'miro': False},
        )
        assert plan['campaign_summary'] == 'Cross-channel launch'
        assert plan['project']['name'] == 'EcoThread Q3'
        assert plan['tasks_plan'][0]['type'] == 'execution'
