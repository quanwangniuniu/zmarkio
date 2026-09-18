"""Unit tests for generation_registry."""
from django.test import TestCase

from .generation_registry import (
    GenerationValidationError,
    build_analysis_prompt,
    filter_sse_analysis_payload,
    normalize_generation_outputs,
    should_skip_workflow_step,
    validate_analysis_response,
    validate_calendar_events_response,
)


class GenerationRegistryTests(TestCase):
    def test_normalize_defaults_when_omitted(self):
        self.assertEqual(
            normalize_generation_outputs(None),
            [
                'recommended_tasks',
                'recommended_decision_tree',
                'miro_board',
            ],
        )

    def test_normalize_parses_json_string(self):
        self.assertEqual(
            normalize_generation_outputs('["recommended_tasks"]'),
            ['recommended_tasks'],
        )

    def test_normalize_rejects_unknown_key(self):
        with self.assertRaises(GenerationValidationError):
            normalize_generation_outputs(['anomalies'])

    def test_normalize_rejects_empty_list(self):
        with self.assertRaises(GenerationValidationError):
            normalize_generation_outputs([])

    def test_build_analysis_prompt_includes_tasks_only(self):
        prompt = build_analysis_prompt(frozenset({'recommended_tasks'}), 'criteria here')
        self.assertIn('recommended_tasks', prompt)
        self.assertNotIn('anomalies', prompt)

    def test_build_analysis_prompt_includes_decision_tree(self):
        prompt = build_analysis_prompt(frozenset({'recommended_decision_tree'}), 'criteria here')
        self.assertIn('recommended_decision_tree', prompt)
        self.assertIn('parent_refs', prompt)
        self.assertIn('Never null, never omitted, never a single string', prompt)
        self.assertIn('"parent_refs": ["root_goal"]', prompt)

    def test_build_analysis_prompt_includes_both_analysis_keys(self):
        prompt = build_analysis_prompt(
            frozenset({'recommended_tasks', 'recommended_decision_tree'}),
            'criteria here',
        )
        self.assertIn('recommended_tasks', prompt)
        self.assertIn('recommended_decision_tree', prompt)

    def test_build_analysis_prompt_empty_object_when_no_analysis_keys(self):
        prompt = build_analysis_prompt(frozenset({'miro_board'}), 'criteria here')
        self.assertIn('{}', prompt)

    def test_validate_analysis_response_exact_keys(self):
        data = {
            'recommended_tasks': [
                {
                    'type': 'optimization',
                    'summary': 'Fix ROAS',
                    'priority': 'HIGH',
                },
            ],
        }
        result = validate_analysis_response(data, frozenset({'recommended_tasks'}))
        self.assertEqual(len(result['recommended_tasks']), 1)

    def test_validate_analysis_rejects_extra_key(self):
        data = {'recommended_tasks': [], 'anomalies': []}
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_tasks'}))

    def test_validate_analysis_rejects_missing_key(self):
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response({}, frozenset({'recommended_tasks'}))

    def test_validate_recommended_tasks_normalizes_type_and_priority(self):
        data = {
            'recommended_tasks': [
                {
                    'type': 'Alert',
                    'summary': 'Fix spam complaints',
                    'priority': 'High',
                },
            ],
        }
        result = validate_analysis_response(data, frozenset({'recommended_tasks'}))
        task = result['recommended_tasks'][0]
        self.assertEqual(task['type'], 'alert')
        self.assertEqual(task['priority'], 'HIGH')

    def test_validate_recommended_tasks_normalizes_highest_lowest_priority(self):
        data = {
            'recommended_tasks': [
                {'type': 'report', 'summary': 'A', 'priority': 'HIGHEST'},
                {'type': 'alert', 'summary': 'B', 'priority': 'LOWEST'},
            ],
        }
        result = validate_analysis_response(data, frozenset({'recommended_tasks'}))
        self.assertEqual(result['recommended_tasks'][0]['priority'], 'HIGH')
        self.assertEqual(result['recommended_tasks'][1]['priority'], 'LOW')

    def test_validate_recommended_tasks_rejects_invalid_type(self):
        data = {
            'recommended_tasks': [
                {'type': 'not_a_real_type', 'summary': 'Bad', 'priority': 'HIGH'},
            ],
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_tasks'}))

    def test_validate_recommended_tasks_rejects_long_summary(self):
        data = {
            'recommended_tasks': [
                {
                    'type': 'alert',
                    'summary': 'x' * 256,
                    'priority': 'HIGH',
                },
            ],
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_tasks'}))

    def test_validate_recommended_tasks_rejects_non_string_description(self):
        data = {
            'recommended_tasks': [
                {
                    'type': 'alert',
                    'summary': 'Valid',
                    'priority': 'HIGH',
                    'description': 123,
                },
            ],
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_tasks'}))

    def _sample_decision_tree(self):
        return {
            'nodes': [
                {
                    'ref': 'root',
                    'layer': 0,
                    'title': 'Reallocate Meta budget?',
                    'parent_refs': [],
                },
                {
                    'ref': 'child_a',
                    'layer': 1,
                    'title': 'Pause underperforming ad sets',
                    'parent_refs': ['root'],
                },
            ],
        }

    def test_validate_decision_tree_valid(self):
        data = {'recommended_decision_tree': self._sample_decision_tree()}
        result = validate_analysis_response(
            data, frozenset({'recommended_decision_tree'})
        )
        self.assertEqual(len(result['recommended_decision_tree']['nodes']), 2)

    def test_validate_decision_tree_empty_nodes(self):
        data = {'recommended_decision_tree': {'nodes': []}}
        result = validate_analysis_response(
            data, frozenset({'recommended_decision_tree'})
        )
        self.assertEqual(result['recommended_decision_tree']['nodes'], [])

    def test_validate_decision_tree_rejects_duplicate_ref(self):
        data = {
            'recommended_decision_tree': {
                'nodes': [
                    {'ref': 'dup', 'layer': 0, 'title': 'A', 'parent_refs': []},
                    {'ref': 'dup', 'layer': 1, 'title': 'B', 'parent_refs': []},
                ],
            },
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_decision_tree'}))

    def test_validate_decision_tree_rejects_bad_layer(self):
        data = {
            'recommended_decision_tree': {
                'nodes': [
                    {'ref': 'root', 'layer': 0, 'title': 'Root', 'parent_refs': []},
                    {'ref': 'child', 'layer': 0, 'title': 'Child', 'parent_refs': ['root']},
                ],
            },
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_decision_tree'}))

    def test_validate_decision_tree_rejects_unknown_parent(self):
        data = {
            'recommended_decision_tree': {
                'nodes': [
                    {'ref': 'child', 'layer': 1, 'title': 'Child', 'parent_refs': ['missing']},
                ],
            },
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(data, frozenset({'recommended_decision_tree'}))

    def test_validate_decision_tree_rejects_extra_top_level_key(self):
        data = {
            'recommended_decision_tree': self._sample_decision_tree(),
            'recommended_tasks': [],
        }
        with self.assertRaises(GenerationValidationError):
            validate_analysis_response(
                data,
                frozenset({'recommended_decision_tree'}),
            )

    def test_filter_sse_includes_decision_tree(self):
        analysis = {'recommended_decision_tree': self._sample_decision_tree()}
        filtered = filter_sse_analysis_payload(
            analysis,
            frozenset({'recommended_decision_tree', 'miro_board'}),
        )
        self.assertEqual(list(filtered.keys()), ['recommended_decision_tree'])

    def test_should_skip_create_decision_without_output(self):
        self.assertTrue(
            should_skip_workflow_step('create_decision', frozenset({'recommended_tasks'}))
        )
        self.assertFalse(
            should_skip_workflow_step(
                'create_decision',
                frozenset({'recommended_decision_tree'}),
            )
        )

    def test_validate_calendar_events_response(self):
        data = {
            'calendar_events': [
                {
                    'title': 'Review',
                    'start_datetime': '2026-06-02T10:00:00',
                    'end_datetime': '2026-06-02T11:00:00',
                    'location': '',
                    'description': '',
                },
            ],
        }
        result = validate_calendar_events_response(data)
        self.assertEqual(len(result['calendar_events']), 1)

    def test_validate_calendar_rejects_extra_top_level_key(self):
        data = {'calendar_events': [], 'answer': 'hi'}
        with self.assertRaises(GenerationValidationError):
            validate_calendar_events_response(data)

    def test_filter_sse_analysis_payload(self):
        analysis = {'recommended_tasks': [{'type': 'alert', 'summary': 'x', 'priority': 'LOW'}]}
        filtered = filter_sse_analysis_payload(
            analysis,
            frozenset({'recommended_tasks', 'miro_board'}),
        )
        self.assertEqual(list(filtered.keys()), ['recommended_tasks'])

    def test_should_skip_workflow_step(self):
        self.assertTrue(
            should_skip_workflow_step('create_tasks', frozenset({'miro_board'}))
        )
        self.assertTrue(
            should_skip_workflow_step('generate_miro_snapshot', frozenset({'recommended_tasks'}))
        )
        self.assertFalse(
            should_skip_workflow_step('analyze_data', frozenset({'recommended_tasks'}))
        )
