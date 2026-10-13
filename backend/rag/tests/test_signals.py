"""Tests for RAG indexing signal wiring across the meetings / notion_editor /
retrospective apps (MED-264).

These tests only prove: the right signal fires, enqueues exactly the right
(tenant_schema, project_id, source_type, source_id) via transaction.on_commit,
and that the on_commit callback actually runs at commit time. They do NOT
re-verify index_source_document's outcome logic (test_indexing.py) or the
Celery task's own retry behavior (test_tasks.py) --
rag.tasks.index_document_task.delay is mocked everywhere below, so no
indexing or Celery machinery ever runs.

Every signal handler does `from rag.tasks import index_document_task` as a
LOCAL import inside its enqueue closure, then calls
`index_document_task.delay(...)`. Patching `rag.tasks.index_document_task.delay`
(the attribute on the shared Task object, not a per-module name) intercepts
every call site regardless of which app's signals.py did the local import,
so every assertion below targets that one patch path.

Each app's signals.py imports `current_tenant_schema` at module top level
(`from core.tenant_context import current_tenant_schema`) and calls it
directly, so tenant-identity determinism is patched per-module
(`<app>.signals.current_tenant_schema`) rather than at the source module --
patching the source wouldn't affect the name already bound into each
signals module's own namespace.

Because the enqueue functions wrap `index_document_task.delay(...)` in
`transaction.on_commit(...)`, and django.test.TestCase wraps each test in an
outer atomic block that is rolled back (never actually committed) rather than
committed, `self.captureOnCommitCallbacks(execute=True)` is required around
each triggering action -- it is what actually runs the queued on_commit
callbacks without needing a real commit or `transaction=True`.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Organization, Project, ProjectMember
from meetings.models import Meeting, MeetingDocument
from meetings.services import ensure_meeting_type_definition
from notion_editor.models import ContentBlock, Draft, DraftProjectLink
from rag.models import DocumentSourceType
from retrospective.models import Insight, RetrospectiveTask

User = get_user_model()


class _SignalTestCase(TestCase):
    """Own setUp rather than the pytest-fixture conftest: captureOnCommitCallbacks
    is a TestCase method, and TestCase doesn't take pytest fixture injection.
    """

    def setUp(self):
        self.organization = Organization.objects.create(name='Signals Org', email_domain='signals.test')
        self.user = User.objects.create_user(
            username='signals_user', email='signals@test.com', password='pw', organization=self.organization,
        )
        self.project = Project.objects.create(
            name='Signals Project', organization=self.organization, owner=self.user,
            objectives=['awareness'], kpis={'ctr': {'target': 0.02}},
        )
        ProjectMember.objects.create(user=self.user, project=self.project, role='owner', is_active=True)
        self.project_b = Project.objects.create(
            name='Signals Project B', organization=self.organization, owner=self.user,
            objectives=['awareness'], kpis={'ctr': {'target': 0.02}},
        )

    def _patch_current_tenant_schema(self, module_path, value='org_test_schema'):
        patcher = patch(module_path, return_value=value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def _make_meeting(self, **kwargs):
        type_def = ensure_meeting_type_definition(self.project, 'planning')
        defaults = dict(project=self.project, title='M', type_definition=type_def, objective='O')
        defaults.update(kwargs)
        return Meeting.objects.create(**defaults)


@patch('rag.tasks.index_document_task.delay')
class MeetingSignalTests(_SignalTestCase):
    def setUp(self):
        super().setUp()
        self.tenant_schema = self._patch_current_tenant_schema('meetings.signals.current_tenant_schema')

    def test_meeting_save_enqueues_parent_meeting(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            meeting = self._make_meeting()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.MEETING
        assert kwargs['source_id'] == str(meeting.id)

    def test_meeting_delete_enqueues_parent_meeting(self, mock_delay):
        meeting = self._make_meeting()
        meeting_id = meeting.id
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            meeting.delete()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.MEETING
        assert kwargs['source_id'] == str(meeting_id)

    def test_meeting_document_save_enqueues_parent_meeting(self, mock_delay):
        meeting = self._make_meeting()
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            MeetingDocument.objects.create(meeting=meeting, content='doc content')

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.MEETING
        assert kwargs['source_id'] == str(meeting.id)

    def test_meeting_document_delete_enqueues_parent_meeting(self, mock_delay):
        meeting = self._make_meeting()
        document = MeetingDocument.objects.create(meeting=meeting, content='doc content')
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            document.delete()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.MEETING
        assert kwargs['source_id'] == str(meeting.id)

    def test_meeting_document_cascade_delete_via_meeting_does_not_raise(self, mock_delay):
        meeting = self._make_meeting()
        MeetingDocument.objects.create(meeting=meeting, content='doc content')
        meeting_id = meeting.id
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            meeting.delete()  # cascades to MeetingDocument

        # Meeting's own post_delete and MeetingDocument's pre_delete-guarded
        # post_delete may both fire (harmless duplicate, per the module
        # docstrings) -- not requiring exactly one call, but at least one
        # of them must correctly target this meeting under this tenant/project.
        matching = [
            call for call in mock_delay.call_args_list
            if call.kwargs.get('tenant_schema') == self.tenant_schema
            and call.kwargs.get('project_id') == self.project.id
            and call.kwargs.get('source_type') == DocumentSourceType.MEETING
            and call.kwargs.get('source_id') == str(meeting_id)
        ]
        assert len(matching) >= 1


@patch('rag.tasks.index_document_task.delay')
class DraftSignalTests(_SignalTestCase):
    def setUp(self):
        super().setUp()
        self.tenant_schema = self._patch_current_tenant_schema('notion_editor.signals.current_tenant_schema')

    def test_unlinked_draft_save_does_not_enqueue(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            Draft.objects.create(user=self.user, title='Unlinked', status='draft')

        mock_delay.assert_not_called()

    def test_linked_draft_save_enqueues_parent_draft(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='Linked', status='draft')
        DraftProjectLink.objects.create(draft=draft, project=self.project)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            draft.title = 'Linked updated'
            draft.save()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.NOTION_DRAFT
        assert kwargs['source_id'] == str(draft.id)

    def test_content_block_save_on_linked_draft_enqueues_parent_draft(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='Linked', status='draft')
        DraftProjectLink.objects.create(draft=draft, project=self.project)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            ContentBlock.objects.create(draft=draft, block_type='text', content={'text': 'hi'}, order=0)

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['source_type'] == DocumentSourceType.NOTION_DRAFT
        assert kwargs['source_id'] == str(draft.id)

    def test_content_block_save_on_unlinked_draft_does_not_enqueue(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='Unlinked', status='draft')

        with self.captureOnCommitCallbacks(execute=True):
            ContentBlock.objects.create(draft=draft, block_type='text', content={'text': 'hi'}, order=0)

        mock_delay.assert_not_called()

    def test_content_block_delete_on_linked_draft_enqueues_parent_draft(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='Linked', status='draft')
        DraftProjectLink.objects.create(draft=draft, project=self.project)
        block = ContentBlock.objects.create(draft=draft, block_type='text', content={'text': 'hi'}, order=0)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            block.delete()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['source_type'] == DocumentSourceType.NOTION_DRAFT
        assert kwargs['source_id'] == str(draft.id)

    def test_draft_project_link_create_enqueues_new_project(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='D', status='draft')

        with self.captureOnCommitCallbacks(execute=True):
            DraftProjectLink.objects.create(draft=draft, project=self.project)

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.NOTION_DRAFT
        assert kwargs['source_id'] == str(draft.id)

    def test_draft_project_link_move_enqueues_both_old_and_new_projects(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='D', status='draft')
        link = DraftProjectLink.objects.create(draft=draft, project=self.project)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            link.project = self.project_b
            link.save()

        assert mock_delay.call_count == 2
        project_ids = {call.kwargs['project_id'] for call in mock_delay.call_args_list}
        assert project_ids == {self.project.id, self.project_b.id}
        for call in mock_delay.call_args_list:
            assert call.kwargs['tenant_schema'] == self.tenant_schema
            assert call.kwargs['source_type'] == DocumentSourceType.NOTION_DRAFT
            assert call.kwargs['source_id'] == str(draft.id)

    def test_draft_project_link_delete_enqueues_old_project(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='D', status='draft')
        link = DraftProjectLink.objects.create(draft=draft, project=self.project)
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            link.delete()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.NOTION_DRAFT
        assert kwargs['source_id'] == str(draft.id)

    def test_draft_delete_cascades_to_content_block_and_link_without_raising(self, mock_delay):
        draft = Draft.objects.create(user=self.user, title='Linked', status='draft')
        DraftProjectLink.objects.create(draft=draft, project=self.project)
        ContentBlock.objects.create(draft=draft, block_type='text', content={'text': 'hi'}, order=0)
        draft_id = draft.id
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            draft.delete()  # cascades to ContentBlock and DraftProjectLink

        # Not requiring exactly one call (duplicate cleanup from cascaded
        # handlers is intentionally harmless), but at least one queued call
        # must correctly target the old project / this draft.
        matching = [
            call for call in mock_delay.call_args_list
            if call.kwargs.get('tenant_schema') == self.tenant_schema
            and call.kwargs.get('project_id') == self.project.id
            and call.kwargs.get('source_type') == DocumentSourceType.NOTION_DRAFT
            and call.kwargs.get('source_id') == str(draft_id)
        ]
        assert len(matching) >= 1


@patch('rag.tasks.index_document_task.delay')
class RetrospectiveSignalTests(_SignalTestCase):
    def setUp(self):
        super().setUp()
        self.tenant_schema = self._patch_current_tenant_schema('retrospective.signals.current_tenant_schema')

    def _make_retro(self, **kwargs):
        defaults = dict(campaign=self.project, created_by=self.user)
        defaults.update(kwargs)
        return RetrospectiveTask.objects.create(**defaults)

    def test_retrospective_save_enqueues_itself(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            retro = self._make_retro()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.RETROSPECTIVE
        assert kwargs['source_id'] == str(retro.id)

    def test_retrospective_delete_enqueues_itself(self, mock_delay):
        retro = self._make_retro()
        retro_id = retro.id
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            retro.delete()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.RETROSPECTIVE
        assert kwargs['source_id'] == str(retro_id)

    def test_insight_save_enqueues_parent_retrospective(self, mock_delay):
        retro = self._make_retro()
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            Insight.objects.create(retrospective=retro, title='I', description='d')

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.RETROSPECTIVE
        assert kwargs['source_id'] == str(retro.id)

    def test_insight_delete_enqueues_parent_retrospective(self, mock_delay):
        retro = self._make_retro()
        insight = Insight.objects.create(retrospective=retro, title='I', description='d')
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            insight.delete()

        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        assert kwargs['tenant_schema'] == self.tenant_schema
        assert kwargs['project_id'] == self.project.id
        assert kwargs['source_type'] == DocumentSourceType.RETROSPECTIVE
        assert kwargs['source_id'] == str(retro.id)

    def test_retrospective_cascade_delete_with_insights_does_not_raise(self, mock_delay):
        retro = self._make_retro()
        Insight.objects.create(retrospective=retro, title='I', description='d')
        retro_id = retro.id
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            retro.delete()  # cascades to Insight

        matching = [
            call for call in mock_delay.call_args_list
            if call.kwargs.get('tenant_schema') == self.tenant_schema
            and call.kwargs.get('project_id') == self.project.id
            and call.kwargs.get('source_type') == DocumentSourceType.RETROSPECTIVE
            and call.kwargs.get('source_id') == str(retro_id)
        ]
        assert len(matching) >= 1
