"""Tests for run_project_rag_followup_eval (A/B phase). No real embeddings."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management.base import CommandError
from django.test import override_settings

from rag.management.commands.run_project_rag_followup_eval import (
    DEFAULT_FOLLOWUP_FIXTURE,
    PHASE_NOTE,
    Command,
    _compare_a_to_b,
    _history_turn_count,
    _validate_followup_fixture,
)
from rag.retrieval import RetrievedChunk

_NONEXISTENT = Path('/nonexistent/followup-fixture-for-tests.json')


def _minimal_question(**overrides):
    base = {
        'id': 'fq99',
        'case_kind': 'anaphoric',
        'question_type': 'single-source',
        'include_in_followup_metrics': True,
        'history': [
            {'role': 'user', 'content': 'Which vendor?'},
            {'role': 'assistant', 'content': 'Vendor A.'},
        ],
        'question': 'Why was that?',
        'expected_rewrite': 'Why did we pick vendor A?',
        'expected_sources': [
            {
                'source_type': 'meeting',
                'source_ref': 'meeting-001',
                'evidence': ['Vendor A'],
            }
        ],
    }
    base.update(overrides)
    return base


def test_validate_followup_fixture_accepts_committed_dataset():
    data = json.loads(DEFAULT_FOLLOWUP_FIXTURE.read_text(encoding='utf-8'))
    _validate_followup_fixture(data, DEFAULT_FOLLOWUP_FIXTURE)


def test_committed_fixture_includes_multi_turn_smoke_rows():
    data = json.loads(DEFAULT_FOLLOWUP_FIXTURE.read_text(encoding='utf-8'))
    multi = [q for q in data['questions'] if 'multi-turn' in (q.get('tags') or [])]
    assert len(multi) >= 4
    for q in multi:
        assert _history_turn_count(q['history']) >= 2, q['id']


def test_committed_fixture_includes_beyond_window_and_multi_source_rows():
    data = json.loads(DEFAULT_FOLLOWUP_FIXTURE.read_text(encoding='utf-8'))
    beyond = [q for q in data['questions'] if 'beyond-default-window' in (q.get('tags') or [])]
    multi_source = [q for q in data['questions'] if q.get('question_type') == 'multi-source']
    assert len(beyond) >= 3
    for q in beyond:
        assert _history_turn_count(q['history']) > 3, q['id']
    assert len(multi_source) >= 4
    for q in multi_source:
        assert len(q.get('expected_sources') or []) >= 2, q['id']


def test_validate_followup_fixture_rejects_multi_turn_tag_without_two_turns():
    data = {
        'source_corpus': {'fixture': 'project_rag_eval_dataset.json'},
        'questions': [
            _minimal_question(
                tags=['multi-turn'],
                history=[
                    {'role': 'user', 'content': 'Only one prior turn?'},
                    {'role': 'assistant', 'content': 'Yes.'},
                ],
            ),
        ],
    }
    with pytest.raises(CommandError, match='multi-turn requires history'):
        _validate_followup_fixture(data, Path('bad.json'))


@override_settings(RAG_QUERY_REWRITE_HISTORY_TURNS=3)
def test_validate_followup_fixture_rejects_beyond_window_tag_without_enough_turns():
    data = {
        'source_corpus': {'fixture': 'project_rag_eval_dataset.json'},
        'questions': [
            _minimal_question(
                tags=['multi-turn', 'beyond-default-window'],
                history=[
                    {'role': 'user', 'content': 'Turn one?'},
                    {'role': 'assistant', 'content': 'A.'},
                    {'role': 'user', 'content': 'Turn two?'},
                    {'role': 'assistant', 'content': 'B.'},
                    {'role': 'user', 'content': 'Turn three?'},
                    {'role': 'assistant', 'content': 'C.'},
                ],
            ),
        ],
    }
    with pytest.raises(CommandError, match='beyond-default-window requires'):
        _validate_followup_fixture(data, Path('bad.json'))


def test_validate_followup_fixture_rejects_case_kind_count_mismatch():
    data = {
        'source_corpus': {'fixture': 'project_rag_eval_dataset.json'},
        'expected_case_kind_counts': {'anaphoric': 1, 'already_complete': 0, 'topic_shift': 0, 'underspecified': 0},
        'questions': [_minimal_question(case_kind='already_complete')],
    }
    with pytest.raises(CommandError, match='case_kind counts'):
        _validate_followup_fixture(data, Path('bad.json'))


def test_validate_followup_fixture_rejects_invented_sources_on_no_answer():
    data = {
        'source_corpus': {'fixture': 'project_rag_eval_dataset.json'},
        'questions': [
            _minimal_question(
                question_type='no-answer',
                expected_sources=[{
                    'source_type': 'meeting',
                    'source_ref': 'meeting-001',
                    'evidence': ['x'],
                }],
            ),
        ],
    }
    with pytest.raises(CommandError, match='empty expected_sources'):
        _validate_followup_fixture(data, Path('bad.json'))


def test_compare_a_to_b_labels():
    assert _compare_a_to_b({'outcome': 'fail'}, {'outcome': 'pass'}) == 'improve'
    assert _compare_a_to_b({'outcome': 'pass'}, {'outcome': 'fail'}) == 'regress'
    assert _compare_a_to_b(
        {'outcome': 'pass', 'evidence_complete_rank': 3},
        {'outcome': 'pass', 'evidence_complete_rank': 1},
    ) == 'improve'
    assert _compare_a_to_b({'outcome': None}, {'outcome': None}) == 'n/a'


@override_settings(RAG_EMBEDDING_PROVIDER='gemini', GEMINI_API_KEY='')
def test_gemini_provider_requires_api_key():
    with pytest.raises(CommandError, match='GEMINI_API_KEY is not configured'):
        Command().handle(fixture=_NONEXISTENT, output=None, top_k=10, arms='A,B')


@override_settings(RAG_EMBEDDING_PROVIDER='local', GEMINI_API_KEY='')
def test_arm_c_requires_gemini_api_key_even_with_local_embeddings():
    with pytest.raises(CommandError, match='Arm C .* GEMINI_API_KEY'):
        Command().handle(fixture=_NONEXISTENT, output=None, top_k=10, arms='A,B,C')


@override_settings(RAG_EMBEDDING_PROVIDER='local', GEMINI_API_KEY='')
def test_ab_phase_runs_both_arms_and_states_scope(tmp_path):
    followup = {
        'dataset_version': '1.0.0-test',
        'source_corpus': {'fixture': 'corpus.json'},
        'expected_case_kind_counts': {
            'anaphoric': 1, 'already_complete': 0, 'topic_shift': 0, 'underspecified': 0,
        },
        'expected_question_type_counts': {
            'single-source': 1, 'multi-source': 0, 'no-answer': 0,
        },
        'questions': [_minimal_question()],
    }
    corpus = {
        'dataset_version': '2.1.0-test',
        'expected_source_counts': {'meeting': 1},
        'expected_question_counts': {},
        'sources': [{
            'source_type': 'meeting',
            'source_ref': 'meeting-001',
            'title': 't',
            'content': 'Vendor A was chosen.',
        }],
        'questions': [],
    }
    followup_path = tmp_path / 'followup.json'
    corpus_path = tmp_path / 'corpus.json'
    followup_path.write_text(json.dumps(followup), encoding='utf-8')
    corpus_path.write_text(json.dumps(corpus), encoding='utf-8')
    output_path = tmp_path / 'out.json'

    chunk = RetrievedChunk(
        content='Vendor A was chosen.',
        source_type='meeting',
        source_id='1',
        chunk_index=0,
        citation_metadata={},
        distance=0.1,
    )
    queries: list[str] = []

    def fake_retrieve(project_id, question, top_k=10, min_similarity=None):
        queries.append(question)
        return [chunk]

    org = MagicMock(name='org', slug='rag-eval-harness', id=1)
    user = MagicMock(name='user', id=2)
    project = MagicMock(name='project', id=9)

    with patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._get_or_create_eval_tenant',
        return_value=(org, user),
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._get_or_create_eval_project',
        return_value=project,
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._seed_and_index',
        return_value=({'meeting-001': ('meeting', '1')}, [{'outcome': 'indexed'}]),
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._validate_question_refs',
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.slug_to_schema_name',
        return_value='org_rag_eval_harness',
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.tenant_schema_context',
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.retrieve_chunks',
        side_effect=fake_retrieve,
    ):
        Command().handle(fixture=followup_path, output=output_path, top_k=5, arms='A,B')

    report = json.loads(output_path.read_text(encoding='utf-8'))
    assert report['run_metadata']['arms_executed'] == ['A', 'B']
    assert report['run_metadata']['phase'] == 'A_B_only'
    assert PHASE_NOTE in report['run_metadata']['phase_note']
    assert 'C' not in report['run_metadata']['arms_executed']
    assert queries == ['Why was that?', 'Why did we pick vendor A?']
    assert set(report['metrics_by_arm']) == {'A', 'B'}
    assert report['per_question'][0]['arms']['A']['query'] == 'Why was that?'
    assert report['per_question'][0]['arms']['B']['query'] == 'Why did we pick vendor A?'
    assert report['per_question'][0]['comparison']['A_to_B'] in {
        'improve', 'regress', 'same', 'n/a',
    }


@override_settings(RAG_EMBEDDING_PROVIDER='local', GEMINI_API_KEY='fake-key-for-test')
def test_arm_c_records_rewrite_meta_and_compares_to_a(tmp_path):
    from agent.query_rewrite import RewriteResult

    followup = {
        'dataset_version': '1.0.0-test',
        'source_corpus': {'fixture': 'corpus.json'},
        'expected_case_kind_counts': {
            'anaphoric': 1, 'already_complete': 0, 'topic_shift': 0, 'underspecified': 0,
        },
        'expected_question_type_counts': {
            'single-source': 1, 'multi-source': 0, 'no-answer': 0,
        },
        'questions': [_minimal_question()],
    }
    corpus = {
        'dataset_version': '2.1.0-test',
        'expected_source_counts': {'meeting': 1},
        'expected_question_counts': {},
        'sources': [{
            'source_type': 'meeting',
            'source_ref': 'meeting-001',
            'title': 't',
            'content': 'Vendor A was chosen.',
        }],
        'questions': [],
    }
    followup_path = tmp_path / 'followup.json'
    corpus_path = tmp_path / 'corpus.json'
    followup_path.write_text(json.dumps(followup), encoding='utf-8')
    corpus_path.write_text(json.dumps(corpus), encoding='utf-8')
    output_path = tmp_path / 'out.json'

    chunk = RetrievedChunk(
        content='Vendor A was chosen.',
        source_type='meeting',
        source_id='1',
        chunk_index=0,
        citation_metadata={},
        distance=0.1,
    )
    queries: list[str] = []

    def fake_retrieve(project_id, question, top_k=10, min_similarity=None):
        queries.append(question)
        return [chunk]

    org = MagicMock(name='org', slug='rag-eval-harness', id=1)
    user = MagicMock(name='user', id=2)
    project = MagicMock(name='project', id=9)
    session = MagicMock(id='session-c')

    with patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._get_or_create_eval_tenant',
        return_value=(org, user),
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._get_or_create_eval_project',
        return_value=project,
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._seed_and_index',
        return_value=({'meeting-001': ('meeting', '1')}, [{'outcome': 'indexed'}]),
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.main_eval._validate_question_refs',
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.slug_to_schema_name',
        return_value='org_rag_eval_harness',
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.tenant_schema_context',
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.retrieve_chunks',
        side_effect=fake_retrieve,
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval._seed_rewrite_session',
        return_value=session,
    ), patch(
        'rag.management.commands.run_project_rag_followup_eval.rewrite_project_question_result',
        return_value=RewriteResult(
            question='Why did we pick vendor A?',
            fell_back=False,
            reason='rewritten',
        ),
    ):
        Command().handle(fixture=followup_path, output=output_path, top_k=5, arms='A,B,C')

    report = json.loads(output_path.read_text(encoding='utf-8'))
    assert report['run_metadata']['arms_executed'] == ['A', 'B', 'C']
    assert report['run_metadata']['phase'] == 'A_B_C'
    assert 'real rewrite_project_question' in report['run_metadata']['phase_note']
    assert 'upper-bound' in report['run_metadata']['phase_note']
    assert queries == [
        'Why was that?',
        'Why did we pick vendor A?',
        'Why did we pick vendor A?',
    ]
    row = report['per_question'][0]
    arm_c = row['arms']['C']
    assert arm_c['rewritten_question'] == 'Why did we pick vendor A?'
    assert arm_c['fell_back'] is False
    assert arm_c['reason'] == 'rewritten'
    assert isinstance(arm_c['latency_ms'], (int, float))
    assert row['history_turn_count'] == 1
    assert row['multi_turn'] is False
    assert 'comparison_A_to_C' in report
    assert report['rewrite_summary']['n'] == 1
    assert report['rewrite_summary']['fallback_count'] == 0
