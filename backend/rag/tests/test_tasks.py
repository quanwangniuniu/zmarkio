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
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry

from rag.indexing import IndexOutcome, IndexResult
from rag.models import DocumentChunk, DocumentSourceType
from rag.tasks import (
    _SUPERSEDED_MAX_RETRIES,
    _SUPERSEDED_RETRY_COUNTDOWN_SECONDS,
    _failed_retry_countdown,
    index_document_task,
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
