"""Tests for rag.tasks — Celery orchestration only (MED-264).

`rag.indexing.index_source_document` is always mocked here via
`rag.tasks.index_source_document` -- this file proves what
`index_document_task` does with the `IndexResult` it gets back (retry
routing, tenant_schema_context usage) and what `rebuild_project_index_task`
enumerates/fans out, not indexing correctness itself (that's
test_indexing.py's job).

`index_document_task` / `rebuild_project_index_task` are `bind=True`, so a
plain `.run(...)` call would silently bind to the REAL task instance as
`self` (normal Python bound-method resolution), not to a fake one we
control. `<task>.run.__func__(fake_self, ...)` calls the underlying
undecorated function directly with an explicit `self`, which is what lets
these tests assert exactly what arguments our code passes to `self.retry()`
without needing Celery's own eager-execution/retry-looping machinery --
that machinery (enforcing `max_retries`, raising MaxRetriesExceededError) is
the library's own well-tested behavior, not ours to re-verify. Proving our
code supplies the correct, finite `max_retries`/`countdown` bounds is what
demonstrates the design cannot retry indefinitely -- Celery itself is what
actually refuses further retries once a bound is exceeded.
"""
import logging
from datetime import timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from celery.exceptions import Retry
from django.test import override_settings
from django.utils import timezone

from rag.indexing import IndexOutcome, IndexResult
from rag.models import DocumentChunk, DocumentIndexState, DocumentIndexStatus, DocumentSourceType
from rag.tasks import (
    _SUPERSEDED_MAX_RETRIES,
    _SUPERSEDED_RETRY_COUNTDOWN_SECONDS,
    _failed_retry_countdown,
    index_document_task,
    reconcile_dirty_rag_states,
    rebuild_project_index_task,
)
from retrospective.models import RetrospectiveStatus

TERMINAL_OUTCOMES = [
    IndexOutcome.INDEXED,
    IndexOutcome.SKIPPED_UNCHANGED,
    IndexOutcome.METADATA_REFRESHED,
    IndexOutcome.EXCLUDED,
    IndexOutcome.MOVED,
]


def _fake_self(retries=0):
    self_mock = MagicMock()
    self_mock.request.retries = retries
    self_mock.retry.side_effect = Retry()
    return self_mock


def _run_index_document_task(fake_self, tenant_schema='org_schema', project_id=1, source_id='5'):
    return index_document_task.run.__func__(
        fake_self, tenant_schema, project_id, DocumentSourceType.MEETING, source_id,
    )


@pytest.mark.unit
class TestIndexDocumentTaskRetryRouting:
    @pytest.mark.parametrize('outcome', TERMINAL_OUTCOMES)
    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.index_source_document')
    def test_terminal_outcomes_do_not_retry(self, mock_index, mock_schema_ctx, outcome):
        mock_index.return_value = IndexResult(outcome)
        fake_self = _fake_self()

        result = _run_index_document_task(fake_self)

        assert result == outcome.value
        fake_self.retry.assert_not_called()

    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.index_source_document')
    def test_superseded_retries_with_5s_countdown_and_explicit_max_retries_3(self, mock_index, mock_schema_ctx):
        mock_index.return_value = IndexResult(IndexOutcome.SUPERSEDED, 'raced')
        fake_self = _fake_self(retries=1)

        with pytest.raises(Retry):
            _run_index_document_task(fake_self)

        fake_self.retry.assert_called_once()
        kwargs = fake_self.retry.call_args.kwargs
        assert kwargs['countdown'] == _SUPERSEDED_RETRY_COUNTDOWN_SECONDS == 5
        assert kwargs['max_retries'] == _SUPERSEDED_MAX_RETRIES == 3

    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.index_source_document')
    def test_superseded_retry_bound_is_fixed_regardless_of_current_retry_count(self, mock_index, mock_schema_ctx):
        """Our code always passes the same max_retries=3 to self.retry(),
        regardless of how many retries have already happened (self.request.retries
        below is varied from 0 up to 9 purely to prove OUR call site doesn't
        derive/scale the bound from that number). This test does NOT claim
        Celery would actually allow a 9th or 10th retry -- self.retry() is
        mocked here, so the real Task.retry()/MaxRetriesExceededError
        enforcement never runs. It's Celery's own retry() implementation,
        given this fixed max_retries=3, that is responsible for refusing
        further retries once request.retries reaches 3 -- that enforcement
        is the library's job, not re-tested here.
        """
        mock_index.return_value = IndexResult(IndexOutcome.SUPERSEDED)
        for retries in (0, 2, 9):
            fake_self = _fake_self(retries=retries)
            with pytest.raises(Retry):
                _run_index_document_task(fake_self)
            assert fake_self.retry.call_args.kwargs['max_retries'] == 3

    @pytest.mark.parametrize('retries,expected_countdown', [
        (0, 30), (1, 60), (2, 120), (3, 240), (4, 300), (5, 300), (10, 300),
    ])
    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.index_source_document')
    def test_failed_backoff_follows_30_60_120_240_capped_300(
        self, mock_index, mock_schema_ctx, retries, expected_countdown,
    ):
        mock_index.return_value = IndexResult(IndexOutcome.FAILED, 'boom')
        fake_self = _fake_self(retries=retries)

        with pytest.raises(Retry):
            _run_index_document_task(fake_self)

        assert fake_self.retry.call_args.kwargs['countdown'] == expected_countdown

    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.index_source_document')
    def test_failed_retry_relies_on_task_level_max_retries_not_an_explicit_override(
        self, mock_index, mock_schema_ctx,
    ):
        mock_index.return_value = IndexResult(IndexOutcome.FAILED, 'boom')
        fake_self = _fake_self(retries=0)

        with pytest.raises(Retry):
            _run_index_document_task(fake_self)

        assert 'max_retries' not in fake_self.retry.call_args.kwargs

    def test_failed_path_task_level_retry_cap_is_five(self):
        # This is the bound that stops the FAILED path from retrying
        # indefinitely: self.retry() never passes an explicit override (see
        # test above), so Celery enforces this task-level max_retries=5
        # itself once self.request.retries reaches it.
        assert index_document_task.max_retries == 5

    def test_failed_backoff_plateaus_past_the_retry_cap(self):
        # Defense in depth alongside the task-level max_retries=5 cap: even
        # past the nominal cap, the countdown itself never grows unbounded.
        assert _failed_retry_countdown(5) == 300
        assert _failed_retry_countdown(50) == 300

    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.index_source_document')
    def test_tenant_schema_context_entered_with_the_given_schema(self, mock_index, mock_schema_ctx):
        mock_index.return_value = IndexResult(IndexOutcome.INDEXED)
        fake_self = _fake_self()

        _run_index_document_task(fake_self, tenant_schema='org_acme', project_id=7, source_id='42')

        mock_schema_ctx.assert_called_once_with('org_acme')
        mock_schema_ctx.return_value.__enter__.assert_called_once()
        mock_schema_ctx.return_value.__exit__.assert_called_once()
        # index_source_document runs INSIDE the tenant_schema_context block.
        mock_index.assert_called_once_with(7, DocumentSourceType.MEETING, '42')


@pytest.mark.django_db
class TestRebuildProjectIndexTaskEnumeration:
    """rebuild_project_index_task.delay() calls index_document_task.delay(
    tenant_schema, project_id, source_type, source_id) POSITIONALLY (see
    rag/tasks.py) -- unlike the signal handlers elsewhere in the codebase,
    which call it with keyword arguments. Assertions below match that
    positional style deliberately, not out of preference.
    """

    def _run_rebuild(self, project_id):
        rebuild_project_index_task.run.__func__(MagicMock(), 'public', project_id)

    def test_enumerates_non_deleted_meetings_only(self, project, make_meeting):
        keep = make_meeting(title='keep')
        make_meeting(title='gone', is_deleted=True)

        with patch('rag.tasks.index_document_task') as mock_task:
            self._run_rebuild(project.id)

        called = [c.args for c in mock_task.delay.call_args_list if c.args[2] == DocumentSourceType.MEETING]
        assert called == [('public', project.id, DocumentSourceType.MEETING, str(keep.id))]

    def test_enumerates_linked_drafts_only(self, project, make_draft, make_draft_link):
        linked = make_draft(title='linked')
        make_draft_link(linked, project)
        make_draft(title='unlinked')  # no DraftProjectLink -> not enumerated

        with patch('rag.tasks.index_document_task') as mock_task:
            self._run_rebuild(project.id)

        called = [c.args for c in mock_task.delay.call_args_list if c.args[2] == DocumentSourceType.NOTION_DRAFT]
        assert called == [('public', project.id, DocumentSourceType.NOTION_DRAFT, str(linked.id))]

    def test_enumerates_non_cancelled_retrospectives_only(self, project, make_retrospective):
        active = make_retrospective(status=RetrospectiveStatus.SCHEDULED)
        make_retrospective(status=RetrospectiveStatus.CANCELLED)

        with patch('rag.tasks.index_document_task') as mock_task:
            self._run_rebuild(project.id)

        called = [c.args for c in mock_task.delay.call_args_list if c.args[2] == DocumentSourceType.RETROSPECTIVE]
        assert called == [('public', project.id, DocumentSourceType.RETROSPECTIVE, str(active.id))]

    def test_fans_out_via_delay_and_does_not_index_inline(self, project, make_meeting):
        make_meeting(title='keep')

        with patch('rag.tasks.index_document_task') as mock_task:
            self._run_rebuild(project.id)

        mock_task.delay.assert_called_once()
        mock_task.assert_not_called()  # never invoked synchronously/inline
        assert DocumentChunk.objects.count() == 0  # no indexing actually ran


@pytest.mark.django_db
class TestReconcileDirtyRagStates:
    """Single-org tests for the dirty-row scan / re-enqueue / per-row
    failure isolation. `tenant_schema_context` is patched out to a no-op,
    so `Organization.objects.all()` is also pinned to exactly
    `[project.organization]` (never left to "however many Organizations
    happen to exist in the test DB") -- with the schema switch neutered,
    a second stray Organization would make every test in this class query
    the SAME (test-connection) schema twice and silently double-count.
    Multi-org scoping itself is proven separately, by mocking, in
    `TestReconcileDirtyRagStatesPerOrgIsolation` below.
    """

    @pytest.fixture(autouse=True)
    def _restrict_to_project_org(self, project):
        with patch('rag.tasks.Organization.objects.all', return_value=[project.organization]):
            yield

    def _dirty_row(self, project, source_type, source_id, age_seconds, **kwargs):
        return DocumentIndexState.objects.create(
            project=project,
            source_type=source_type,
            source_id=source_id,
            dirty_since=timezone.now() - timedelta(seconds=age_seconds),
            **kwargs,
        )

    @patch('rag.tasks.slug_to_schema_name')
    @patch('rag.tasks.tenant_schema_context')
    def test_reenqueues_only_rows_dirty_past_the_stale_threshold(self, mock_ctx, mock_slug, project):
        mock_slug.return_value = 'public'
        self._dirty_row(project, DocumentSourceType.MEETING, 'stale', age_seconds=700)
        self._dirty_row(project, DocumentSourceType.NOTION_DRAFT, 'fresh', age_seconds=10)
        DocumentIndexState.objects.create(
            project=project, source_type=DocumentSourceType.RETROSPECTIVE, source_id='clean', dirty_since=None,
        )

        with override_settings(RAG_RECONCILE_STALE_SECONDS=600), patch('rag.tasks.index_document_task') as mock_task:
            result = reconcile_dirty_rag_states()

        assert result == 1
        mock_task.delay.assert_called_once_with(
            tenant_schema='public', project_id=project.id,
            source_type=DocumentSourceType.MEETING, source_id='stale',
        )

    @patch('rag.tasks.slug_to_schema_name')
    @patch('rag.tasks.tenant_schema_context')
    def test_stale_dirty_row_with_status_failed_is_still_reenqueued(self, mock_ctx, mock_slug, project):
        """dirty_since, not status, is the sole trigger (see
        DocumentIndexState's class docstring) -- a FAILED row can coexist
        with already-current chunks if the source was reverted after the
        failed attempt, so status must never gate re-enqueueing.
        """
        mock_slug.return_value = 'public'
        self._dirty_row(
            project, DocumentSourceType.MEETING, 'failed-src', age_seconds=700,
            status=DocumentIndexStatus.FAILED,
        )

        with override_settings(RAG_RECONCILE_STALE_SECONDS=600), patch('rag.tasks.index_document_task') as mock_task:
            result = reconcile_dirty_rag_states()

        assert result == 1
        mock_task.delay.assert_called_once_with(
            tenant_schema='public', project_id=project.id,
            source_type=DocumentSourceType.MEETING, source_id='failed-src',
        )

    @patch('rag.tasks.slug_to_schema_name')
    @patch('rag.tasks.tenant_schema_context')
    def test_one_delay_failure_does_not_block_remaining_dirty_rows(self, mock_ctx, mock_slug, project):
        """The core reliability fix under test: an exception from one row's
        `.delay()` (e.g. broker unreachable at publish time) must not abort
        the loop -- every other stale-dirty row still gets attempted.
        """
        mock_slug.return_value = 'public'
        self._dirty_row(project, DocumentSourceType.MEETING, 'a', age_seconds=700)
        self._dirty_row(project, DocumentSourceType.NOTION_DRAFT, 'b', age_seconds=700)

        with override_settings(RAG_RECONCILE_STALE_SECONDS=600), patch('rag.tasks.index_document_task') as mock_task:
            mock_task.delay.side_effect = [RuntimeError('broker unreachable'), None]
            result = reconcile_dirty_rag_states()

        assert mock_task.delay.call_count == 2  # both rows were attempted despite the first failing
        assert result == 1  # only the successful publish is counted

    @patch('rag.tasks.slug_to_schema_name')
    @patch('rag.tasks.tenant_schema_context')
    def test_delay_failure_does_not_clear_dirty_since(self, mock_ctx, mock_slug, project):
        mock_slug.return_value = 'public'
        row = self._dirty_row(project, DocumentSourceType.MEETING, 'a', age_seconds=700)
        original_dirty_since = row.dirty_since

        with override_settings(RAG_RECONCILE_STALE_SECONDS=600), patch('rag.tasks.index_document_task') as mock_task:
            mock_task.delay.side_effect = RuntimeError('broker unreachable')
            reconcile_dirty_rag_states()

        row.refresh_from_db()
        # dirty_since is the retry mechanism for the next periodic run -- a
        # failed publish must leave it untouched, not merely non-null.
        assert row.dirty_since == original_dirty_since

    @patch('rag.tasks.slug_to_schema_name')
    @patch('rag.tasks.tenant_schema_context')
    def test_delay_failure_is_logged_with_tenant_project_source_identity(
        self, mock_ctx, mock_slug, project, caplog,
    ):
        mock_slug.return_value = 'org_schema'
        self._dirty_row(project, DocumentSourceType.MEETING, 'm1', age_seconds=700)

        with override_settings(RAG_RECONCILE_STALE_SECONDS=600), \
                patch('rag.tasks.index_document_task') as mock_task, \
                caplog.at_level(logging.ERROR, logger='rag.tasks'):
            mock_task.delay.side_effect = RuntimeError('broker unreachable')
            reconcile_dirty_rag_states()

        [record] = [r for r in caplog.records if 'failed to enqueue' in r.getMessage()]
        message = record.getMessage()
        assert 'org_schema' in message
        assert str(project.id) in message
        assert DocumentSourceType.MEETING in message
        assert 'm1' in message

    @patch('rag.tasks.slug_to_schema_name')
    @patch('rag.tasks.tenant_schema_context')
    def test_returns_zero_and_does_not_log_when_nothing_is_dirty(self, mock_ctx, mock_slug, project, caplog):
        mock_slug.return_value = 'public'

        with patch('rag.tasks.index_document_task') as mock_task, caplog.at_level(logging.INFO, logger='rag.tasks'):
            result = reconcile_dirty_rag_states()

        assert result == 0
        mock_task.delay.assert_not_called()
        assert not any('re-enqueued' in r.getMessage() for r in caplog.records)


@pytest.mark.unit
class TestReconcileDirtyRagStatesPerOrgIsolation:
    """Proves the org loop treats each Organization's dirty-row set
    independently (correct tenant_schema per row, no cross-org bleed).

    This CANNOT be proven by creating two real Organizations under
    `tenant_schema_context` mocked out to a no-op: with the context manager
    neutered, every org iteration would query whatever schema the test
    connection actually has active (a single shared schema), so a naive
    two-org django_db test would see the SAME rows on every iteration --
    passing or failing for the wrong reason, not because per-org scoping
    is (or isn't) correct. Real per-schema isolation is exercised by
    Postgres schema-switching itself (`core.tenant_context`), not by this
    task's own logic, so it doesn't need re-proving here.

    Instead, `Organization` and `DocumentIndexState` are mocked directly:
    `DocumentIndexState.objects.filter(...).values_list(...)` is given a
    distinct canned row set per call via `side_effect`, one call per org
    loop iteration. That directly proves the loop pairs each org with its
    OWN query result and its OWN `tenant_schema`, without depending on (or
    faking) real multi-schema Postgres behavior.
    """

    @patch('rag.tasks.index_document_task')
    @patch('rag.tasks.Organization')
    @patch('rag.tasks.DocumentIndexState')
    @patch('rag.tasks.tenant_schema_context')
    @patch('rag.tasks.slug_to_schema_name')
    def test_scans_every_organization_with_its_own_row_set(
        self, mock_slug, mock_ctx, mock_state_model, mock_org_model, mock_task,
    ):
        org1 = MagicMock(slug='org-one')
        org2 = MagicMock(slug='org-two')
        mock_org_model.objects.all.return_value = [org1, org2]
        mock_slug.side_effect = lambda slug: f'schema_{slug}'

        mock_state_model.objects.filter.return_value.values_list.side_effect = [
            [(101, DocumentSourceType.MEETING, 'm1')],
            [(202, DocumentSourceType.MEETING, 'm2')],
        ]

        result = reconcile_dirty_rag_states()

        assert result == 2
        assert mock_task.delay.call_count == 2
        mock_task.delay.assert_any_call(
            tenant_schema='schema_org-one', project_id=101,
            source_type=DocumentSourceType.MEETING, source_id='m1',
        )
        mock_task.delay.assert_any_call(
            tenant_schema='schema_org-two', project_id=202,
            source_type=DocumentSourceType.MEETING, source_id='m2',
        )

        # tenant_schema_context entered exactly once per expected schema,
        # in org-iteration order.
        assert mock_ctx.call_args_list == [call('schema_org-one'), call('schema_org-two')]
        assert mock_ctx.return_value.__enter__.call_count == 2

        # The dirty-state queryset was executed exactly once per organization.
        assert mock_state_model.objects.filter.call_count == 2
        assert mock_state_model.objects.filter.return_value.values_list.call_count == 2
