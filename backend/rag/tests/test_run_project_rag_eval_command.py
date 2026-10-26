"""
Focused tests for rag/management/commands/run_project_rag_eval.py additions
(MED-264 Phase 2 local-provider unblock):

- fixture-declared expected source/question counts (partial fixture support)
- canonical fixture validation behavior is unchanged (no declared counts ->
  falls back to the hardcoded EXPECTED_SOURCE_COUNTS/EXPECTED_QUESTION_COUNTS)
- GEMINI_API_KEY is required only when RAG_EMBEDDING_PROVIDER == 'gemini'
- an unsupported provider value fails fast, before any DB/tenant work
- partial_baseline metadata flows from the loaded fixture into the JSON
  report's run_metadata untouched

None of these hit a real database, real Gemini, or real fastembed/ONNX:
the credential/provider-gate tests raise before any DB access is attempted
(proven by asserting *which* CommandError fires), and the partial_baseline
test mocks every seeding/tenant/retrieval call so only the command's own
report-assembly logic is under test.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.core.management.base import CommandError
from django.test import override_settings

from rag.management.commands.run_project_rag_eval import (
    EXPECTED_QUESTION_COUNTS,
    EXPECTED_SOURCE_COUNTS,
    Command,
    _validate_fixture_shape,
)

_NONEXISTENT_FIXTURE = Path('/nonexistent/fixture-for-tests.json')


# ---------------------------------------------------------------------------
# _validate_fixture_shape: fixture-declared counts vs. canonical fallback
# ---------------------------------------------------------------------------

def test_validate_fixture_shape_accepts_canonical_shape_without_declared_counts():
    """Regression guard: the canonical fixture has never declared
    expected_source_counts/expected_question_counts, so it must keep
    validating against the hardcoded module constants exactly as before.
    """
    data = {
        'sources': (
            [{'source_type': 'meeting', 'source_ref': f'm{i}'} for i in range(12)]
            + [{'source_type': 'notion_draft', 'source_ref': f'n{i}'} for i in range(8)]
            + [{'source_type': 'retrospective', 'source_ref': f'r{i}'} for i in range(6)]
        ),
        'questions': (
            [{'id': f'q{i}', 'question_type': 'single-source'} for i in range(14)]
            + [{'id': f'mq{i}', 'question_type': 'multi-source'} for i in range(6)]
            + [{'id': f'nq{i}', 'question_type': 'no-answer'} for i in range(2)]
        ),
    }
    assert data['sources'].__len__() == sum(EXPECTED_SOURCE_COUNTS.values())
    _validate_fixture_shape(data, Path('canonical.json'))  # must not raise


def test_validate_fixture_shape_uses_fixture_declared_counts_when_present():
    """A variant fixture (e.g. the no-retro partial baseline) declares its
    own expected_source_counts/expected_question_counts, which differ from
    the canonical constants -- validation must use the fixture's own
    declaration, not reject it against the canonical shape.
    """
    data = {
        'expected_source_counts': {'meeting': 12, 'notion_draft': 8},
        'expected_question_counts': {'single-source': 11, 'multi-source': 4, 'no-answer': 2},
        'sources': (
            [{'source_type': 'meeting', 'source_ref': f'm{i}'} for i in range(12)]
            + [{'source_type': 'notion_draft', 'source_ref': f'n{i}'} for i in range(8)]
        ),
        'questions': (
            [{'id': f'q{i}', 'question_type': 'single-source'} for i in range(11)]
            + [{'id': f'mq{i}', 'question_type': 'multi-source'} for i in range(4)]
            + [{'id': f'nq{i}', 'question_type': 'no-answer'} for i in range(2)]
        ),
    }
    _validate_fixture_shape(data, Path('no_retro.json'))  # must not raise


def test_validate_fixture_shape_rejects_mismatch_against_declared_counts():
    data = {
        'expected_source_counts': {'meeting': 12, 'notion_draft': 8},
        'expected_question_counts': EXPECTED_QUESTION_COUNTS,
        'sources': [{'source_type': 'meeting', 'source_ref': 'm1'}],  # far short of 12
        'questions': [],
    }
    with pytest.raises(CommandError, match='source counts'):
        _validate_fixture_shape(data, Path('bad.json'))


def test_validate_fixture_shape_rejects_mismatch_against_canonical_fallback():
    data = {
        'sources': [{'source_type': 'meeting', 'source_ref': 'm1'}],  # far short, no declared override
        'questions': [],
    }
    with pytest.raises(CommandError, match='source counts'):
        _validate_fixture_shape(data, Path('bad.json'))


# ---------------------------------------------------------------------------
# GEMINI_API_KEY required only for provider == 'gemini'; unknown provider
# fails fast. All of these raise before any DB/tenant access, so we prove
# "which gate fired" by asserting on the CommandError's own message: a
# fixture-not-found error can only happen after the credential gate passed.
# ---------------------------------------------------------------------------

@override_settings(RAG_EMBEDDING_PROVIDER='gemini', GEMINI_API_KEY='')
def test_gemini_provider_requires_api_key():
    with pytest.raises(CommandError, match='GEMINI_API_KEY is not configured'):
        Command().handle(fixture=_NONEXISTENT_FIXTURE, output=None, top_k=10)


@override_settings(RAG_EMBEDDING_PROVIDER='gemini', GEMINI_API_KEY='fake-key-for-test')
def test_gemini_provider_with_api_key_passes_credential_gate():
    """Proven by reaching _load_fixture's own "not found" error instead of
    the GEMINI_API_KEY error -- i.e. the credential gate did not block it.
    """
    with pytest.raises(CommandError, match='Fixture not found'):
        Command().handle(fixture=_NONEXISTENT_FIXTURE, output=None, top_k=10)


@override_settings(RAG_EMBEDDING_PROVIDER='local', GEMINI_API_KEY='')
def test_local_provider_does_not_require_api_key():
    """No GEMINI_API_KEY configured at all, provider is 'local' -- must not
    raise the GEMINI_API_KEY error. Proven the same way: it gets past the
    credential gate and fails on the (expected, harmless) missing fixture.
    """
    with pytest.raises(CommandError, match='Fixture not found'):
        Command().handle(fixture=_NONEXISTENT_FIXTURE, output=None, top_k=10)


@override_settings(RAG_EMBEDDING_PROVIDER='not-a-real-provider')
def test_unsupported_provider_fails_fast_before_any_fixture_or_db_access():
    with pytest.raises(CommandError, match="Unknown RAG_EMBEDDING_PROVIDER 'not-a-real-provider'"):
        Command().handle(fixture=_NONEXISTENT_FIXTURE, output=None, top_k=10)


# ---------------------------------------------------------------------------
# partial_baseline metadata flows from the fixture into the JSON report
# untouched. Every seeding/tenant/retrieval call is mocked -- only the
# command's own report-assembly logic (handle()) is under test.
# ---------------------------------------------------------------------------

_STUB_METRICS = {
    'single_source': {'n': 0, 'hit@1': 0.0, 'hit@3': 0.0, 'hit@5': 0.0, 'mrr': 0.0},
    'multi_source': {
        'n': 0, 'recall@3': 0.0, 'recall@5': 0.0, 'recall@10': 0.0,
        'full_coverage@3': 0.0, 'full_coverage@5': 0.0, 'full_coverage@10': 0.0,
        'precision@3': 0.0, 'precision@5': 0.0,
        'source_precision@3_diagnostic': 0.0, 'source_precision@5_diagnostic': 0.0,
        'aux_mrr_first_relevant': 0.0,
    },
    'no_answer': {'n': 0, 'note': 'excluded by design'},
}


def _stub_out_heavy_dependencies(monkeypatch, *, fixture_data: dict):
    import contextlib

    mock_org = MagicMock(slug='evalorg', name='RAG Eval Harness')
    mock_user = MagicMock()
    mock_project = MagicMock(id=1, name='MED-264 RAG Eval Fixture')

    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._load_fixture',
        lambda path: fixture_data,
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._get_or_create_eval_tenant',
        lambda: (mock_org, mock_user),
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval.slug_to_schema_name',
        lambda slug: 'org_evalorg',
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval.tenant_schema_context',
        lambda schema: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._get_or_create_eval_project',
        lambda org, user: mock_project,
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._seed_and_index',
        lambda project, user, sources: ({}, []),
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._validate_question_refs',
        lambda questions, source_ref_map: None,
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._run_questions',
        lambda project, questions, source_ref_map, top_k: [],
    )
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._aggregate_metrics',
        lambda per_question: _STUB_METRICS,
    )

    captured = {}

    def _fake_write_report(self, report, output_option):
        captured['report'] = report
        return Path('/dev/null/fake-report.json')

    monkeypatch.setattr(Command, '_write_report', _fake_write_report)
    return captured


@override_settings(RAG_EMBEDDING_PROVIDER='local')
def test_partial_baseline_metadata_is_retained_in_report(monkeypatch):
    fixture_data = {
        'dataset_version': '1.0.0-no-retro',
        'sources': [],
        'questions': [],
        'partial_baseline': {
            'reason': 'Meeting + Notion Draft sources only.',
            'excluded_source_types': ['retrospective'],
            'excluded_question_ids': ['q06', 'q07', 'q13', 'mq02', 'mq06'],
        },
    }
    captured = _stub_out_heavy_dependencies(monkeypatch, fixture_data=fixture_data)
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._validate_fixture_shape',
        lambda data, path: None,
    )

    Command().handle(fixture=Path('no_retro.json'), output=None, top_k=10)

    run_metadata = captured['report']['run_metadata']
    assert run_metadata['partial_baseline'] == fixture_data['partial_baseline']
    assert run_metadata['embedding_provider'] == 'local'
    assert run_metadata['embedding_model'] == 'BAAI/bge-base-en-v1.5'


@override_settings(RAG_EMBEDDING_PROVIDER='gemini', GEMINI_API_KEY='fake-key-for-test')
def test_canonical_fixture_without_partial_baseline_omits_the_key(monkeypatch):
    fixture_data = {
        'dataset_version': '1.0.0',
        'sources': [],
        'questions': [],
        # no 'partial_baseline' key -- this is the canonical fixture's shape.
    }
    captured = _stub_out_heavy_dependencies(monkeypatch, fixture_data=fixture_data)
    monkeypatch.setattr(
        'rag.management.commands.run_project_rag_eval._validate_fixture_shape',
        lambda data, path: None,
    )

    Command().handle(fixture=Path('canonical.json'), output=None, top_k=10)

    run_metadata = captured['report']['run_metadata']
    assert 'partial_baseline' not in run_metadata
    assert run_metadata['embedding_provider'] == 'gemini'
    assert run_metadata['embedding_model'] == 'gemini-embedding-2'
