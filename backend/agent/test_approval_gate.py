import pytest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase

from core.models import Organization, Project, ProjectMember
from decision.models import Decision
from task.models import Task

from .approval_gate import KIND_DECISION_TREE, KIND_TASK, request_external_commit, resolve_pending
from . import approval_gate
from .models import AgentPendingExternalApproval, AgentSession, AgentWorkflowRun
from .services import AgentOrchestrator

User = get_user_model()


@pytest.fixture
def approval_org_project_user(db):
    org = Organization.objects.create(name='Apr Org', slug='apr-org')
    user = User.objects.create_user(
        username='apruser',
        email='apruser@test.com',
        password='x',
        organization=org,
    )
    user.organization = org
    user.save()
    project = Project.objects.create(
        name='Apr Proj',
        organization=org,
        owner=user,
    )
    ProjectMember.objects.create(project=project, user=user, is_active=True)
    return org, project, user


@pytest.mark.django_db
def test_request_external_commit_auto_creates_tasks(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=False)
    wf = AgentWorkflowRun.objects.create(session=session, status='analyzing')
    decision = Decision.objects.create(
        title='D',
        project=project,
        project_seq=1,
        author=user,
    )
    wf.decision = decision
    wf.save()
    orch = AgentOrchestrator(user, project, session)
    draft = {
        'recommended_tasks': [
            {'summary': 'T1', 'description': 'd', 'type': 'execution', 'priority': 'LOW'},
        ],
    }
    ctx = {
        'input_data': {'analysis_result': {'recommended_tasks': draft['recommended_tasks']}},
        'analysis_result': {'recommended_tasks': draft['recommended_tasks']},
        'decision_id': decision.id,
    }
    gate = request_external_commit(
        orchestrator=orch,
        workflow_run=wf,
        step_execution=None,
        kind=KIND_TASK,
        draft=draft,
        commit_context=ctx,
    )
    assert gate.paused is False
    assert gate.workflow_run_patch.get('created_tasks')
    assert gate.sse_events
    ev = next((e for e in gate.sse_events if e.get('type') == 'task_created'), None)
    assert ev is not None
    data = ev.get('data') or {}
    assert isinstance(data.get('created_tasks'), list)
    assert data['created_tasks'][0]['index'] == 0
    assert data['created_tasks'][0]['summary'] == 'T1'
    assert isinstance(data['created_tasks'][0]['task_id'], int)
    assert not AgentPendingExternalApproval.objects.filter(session=session).exists()


@pytest.mark.django_db
def test_request_external_commit_auto_creates_decision_tree(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=False)
    wf = AgentWorkflowRun.objects.create(session=session, status='analyzing')
    orch = AgentOrchestrator(user, project, session)
    tree = {
        'nodes': [
            {'ref': 'root', 'layer': 0, 'title': 'Root decision', 'parent_refs': []},
            {'ref': 'child', 'layer': 1, 'title': 'Child decision', 'parent_refs': ['root']},
        ],
    }
    draft = {'recommended_decision_tree': tree}
    ctx = {
        'input_data': {'analysis_result': {'recommended_decision_tree': tree}},
        'analysis_result': {'recommended_decision_tree': tree},
    }
    gate = request_external_commit(
        orchestrator=orch,
        workflow_run=wf,
        step_execution=None,
        kind=KIND_DECISION_TREE,
        draft=draft,
        commit_context=ctx,
    )
    assert gate.paused is False
    assert len(gate.workflow_run_patch.get('created_decisions', [])) == 2
    ev = next((e for e in gate.sse_events if e.get('type') == 'decision_draft'), None)
    assert ev is not None
    data = ev.get('data') or {}
    assert len(data.get('decision_ids', [])) == 2
    assert len(data.get('created_decisions', [])) == 2


@pytest.mark.django_db
def test_request_external_commit_creates_pending_when_required(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=True)
    wf = AgentWorkflowRun.objects.create(session=session, status='analyzing')
    orch = AgentOrchestrator(user, project, session)
    draft = {'recommended_tasks': [{'summary': 'T', 'type': 'execution', 'priority': 'LOW'}]}
    ctx = {'input_data': {}, 'analysis_result': draft, 'decision_id': None}
    gate = request_external_commit(
        orchestrator=orch,
        workflow_run=wf,
        step_execution=None,
        kind=KIND_TASK,
        draft=draft,
        commit_context=ctx,
    )
    assert gate.paused is True
    assert AgentPendingExternalApproval.objects.filter(session=session, status='pending').exists()
    assert gate.sse_events
    ev = gate.sse_events[0]
    assert ev.get('type') == 'approval_request'
    data = ev.get('data') or {}
    assert data.get('approval_id')
    assert data.get('kind') == KIND_TASK
    assert isinstance(data.get('draft'), dict)


@pytest.mark.django_db
def test_resolve_pending_reject(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=True)
    wf = AgentWorkflowRun.objects.create(session=session, status='analyzing')
    pending = AgentPendingExternalApproval.objects.create(
        session=session,
        workflow_run=wf,
        step_execution=None,
        kind=KIND_TASK,
        status='pending',
        draft={'recommended_tasks': []},
        commit_context={'input_data': {}, 'analysis_result': {}, 'decision_id': None},
        destination_options=[],
        default_destination=None,
    )
    orch = AgentOrchestrator(user, project, session)
    result = resolve_pending(
        orchestrator=orch,
        pending_id=str(pending.id),
        decision='reject',
        draft={},
        destination=None,
    )
    assert any('rejected' in (e.get('content') or '') for e in result.sse_events)
    pending.refresh_from_db()
    assert pending.status == 'rejected'


@pytest.mark.django_db
def test_resolve_pending_task_can_override_recommended_tasks_and_preserve_indexes(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=True)
    wf = AgentWorkflowRun.objects.create(session=session, status='analyzing')
    decision = Decision.objects.create(
        title='D',
        project=project,
        project_seq=1,
        author=user,
    )
    wf.decision = decision
    wf.save()

    orch = AgentOrchestrator(user, project, session)
    base_tasks = [
        {'summary': 'T0', 'description': 'd0', 'type': 'execution', 'priority': 'LOW'},
        {'summary': 'T1', 'description': 'd1', 'type': 'execution', 'priority': 'LOW'},
        {'summary': 'T2', 'description': 'd2', 'type': 'execution', 'priority': 'LOW'},
    ]
    ctx = {
        'input_data': {'analysis_result': {'recommended_tasks': base_tasks}},
        'analysis_result': {'recommended_tasks': base_tasks},
        'decision_id': decision.id,
    }
    gate = request_external_commit(
        orchestrator=orch,
        workflow_run=wf,
        step_execution=None,
        kind=KIND_TASK,
        draft={'recommended_tasks': base_tasks},
        commit_context=ctx,
    )
    assert gate.paused is True
    approval_id = gate.pending_id
    assert approval_id

    # Select only tasks 0 and 2 and preserve their original indexes.
    override = [
        {**base_tasks[0], 'index': 0},
        {**base_tasks[2], 'index': 2},
    ]
    before_count = Task.objects.filter(project=project, owner=user).count()
    result = resolve_pending(
        orchestrator=orch,
        pending_id=str(approval_id),
        decision='approve',
        draft={'recommended_tasks': override},
        destination=None,
    )
    assert result.paused is False
    ev = next((e for e in result.sse_events if e.get('type') == 'task_created'), None)
    assert ev is not None
    data = ev.get('data') or {}
    created = data.get('created_tasks') or []
    assert len(created) == 2
    assert {c.get('index') for c in created} == {0, 2}

    after_count = Task.objects.filter(project=project, owner=user).count()
    assert after_count - before_count == 2


class DestinationOptionTests(SimpleTestCase):
    def setUp(self):
        self.project = SimpleNamespace(id=42, name='Marketing')
        self.user = object()
        self.session = object()

    def build(self, kind, *, draft=None, commit_context=None):
        return approval_gate.build_destination_options(
            kind=kind,
            session=self.session,
            project=self.project,
            user=self.user,
            draft=draft or {},
            commit_context=commit_context or {},
        )

    def test_project_destination_option_contains_id_label_and_value(self):
        self.assertEqual(
            approval_gate._project_destination_options(self.project),
            [{'id': '42', 'label': 'Marketing', 'value': {'project_id': '42'}}],
        )

    @patch('core.models.Project.objects.filter')
    @patch('core.models.ProjectMember.objects.filter')
    def test_user_accessible_projects_returns_active_non_deleted_projects(
        self,
        mock_member_filter,
        mock_project_filter,
    ):
        mock_member_filter.return_value.values_list.return_value = [8, 13]
        expected_projects = [SimpleNamespace(id=8), SimpleNamespace(id=13)]
        ordered = mock_project_filter.return_value.order_by.return_value
        ordered.__getitem__.return_value = expected_projects

        result = approval_gate._user_accessible_projects(self.user)

        self.assertEqual(result, expected_projects)
        mock_member_filter.assert_called_once_with(user=self.user, is_active=True)
        mock_project_filter.assert_called_once_with(
            id__in=[8, 13],
            is_deleted=False,
        )

    def test_internal_commit_kinds_default_to_current_project(self):
        for kind in (
            approval_gate.KIND_TASK,
            approval_gate.KIND_DECISION_TREE,
            approval_gate.KIND_MIRO_BOARD,
        ):
            with self.subTest(kind=kind):
                self.assertEqual(self.build(kind), ([], {'project_id': '42'}))

    def test_calendar_without_organization_has_no_destination(self):
        self.assertEqual(self.build(approval_gate.KIND_CALENDAR_EVENT), ([], None))

    @patch('calendars.models.Calendar.objects.filter')
    def test_calendar_options_prefer_primary_calendar(self, mock_filter):
        secondary = SimpleNamespace(id='cal-2', name='Team', is_primary=False)
        primary = SimpleNamespace(id='cal-1', name='Personal', is_primary=True)
        queryset = mock_filter.return_value
        queryset.order_by.return_value.__getitem__.return_value = [secondary, primary]

        options, default = self.build(
            approval_gate.KIND_CALENDAR_EVENT,
            commit_context={'organization_id': 'org-1'},
        )

        self.assertEqual(
            options,
            [
                {'id': 'cal-2', 'label': 'Team', 'value': {'calendar_id': 'cal-2'}},
                {'id': 'cal-1', 'label': 'Personal', 'value': {'calendar_id': 'cal-1'}},
            ],
        )
        self.assertEqual(default, {'calendar_id': 'cal-1'})

    def test_forward_options_skip_blank_usernames(self):
        options, default = self.build(
            approval_gate.KIND_FORWARD_MESSAGE,
            draft={
                'forwards': [
                    {'username': ' alex '},
                    {'username': ''},
                    {'content': 'missing username'},
                    {'username': 'sam'},
                ]
            },
        )

        self.assertEqual(
            options,
            [
                {'id': 'alex:0', 'label': 'alex', 'value': {'username': 'alex'}},
                {'id': 'sam:3', 'label': 'sam', 'value': {'username': 'sam'}},
            ],
        )
        self.assertEqual(default, {'usernames': ['alex', 'sam']})

    def test_custom_api_email_and_unknown_destinations(self):
        self.assertEqual(
            self.build(approval_gate.KIND_CUSTOM_API),
            ([{'id': 'default', 'label': 'External API', 'value': {}}], {}),
        )
        self.assertEqual(self.build(approval_gate.KIND_EMAIL), ([], None))
        self.assertEqual(self.build('unsupported'), ([], None))


class CommitDispatchTests(SimpleTestCase):
    def test_run_commit_wraps_registered_handler_result(self):
        handler = MagicMock(return_value=({'answer': 1}, [{'type': 'done'}], None))

        with patch.dict(approval_gate.COMMIT_REGISTRY, {'example': handler}, clear=True):
            result = approval_gate.run_commit(
                orchestrator='orchestrator',
                kind='example',
                draft={'draft': True},
                destination={'target': 1},
                commit_context={'context': True},
            )

        self.assertFalse(result.paused)
        self.assertEqual(result.output_data, {'answer': 1})
        self.assertEqual(result.sse_events, [{'type': 'done'}])
        self.assertEqual(result.workflow_run_patch, {})

    def test_run_commit_rejects_unknown_kind(self):
        with self.assertRaisesRegex(ValueError, 'Unknown external outcome kind'):
            approval_gate.run_commit(
                orchestrator=object(),
                kind='unsupported',
                draft={},
                destination=None,
                commit_context={},
            )


class CustomAPICommitTests(SimpleTestCase):
    @patch('agent.approval_gate.http_requests.request')
    def test_json_body_and_response_are_added_to_output(self, mock_request):
        response = MagicMock(status_code=201)
        response.json.return_value = {'external_id': 17}
        mock_request.return_value = response

        output, events, workflow_patch = approval_gate._commit_custom_api(
            None,
            {
                'method': 'patch',
                'url': 'https://example.test/items/17',
                'headers': {'Authorization': 'Bearer test'},
                'body': {'enabled': True},
            },
            None,
            {'timeout': 8, 'merge_output': {'source': 'workflow'}},
        )

        self.assertEqual(
            output,
            {'source': 'workflow', 'api_response': {'external_id': 17}},
        )
        self.assertEqual(events[0]['content'], 'Custom API call completed (201).')
        self.assertEqual(workflow_patch, {})
        mock_request.assert_called_once_with(
            'PATCH',
            'https://example.test/items/17',
            headers={'Authorization': 'Bearer test'},
            data='{"enabled": true}',
            timeout=8,
        )
        response.raise_for_status.assert_called_once_with()

    @patch('agent.approval_gate.http_requests.request')
    def test_non_json_response_is_returned_as_truncated_raw_text(self, mock_request):
        response = MagicMock(status_code=200, text='x' * 2100)
        response.json.side_effect = ValueError('not json')
        mock_request.return_value = response

        output, _events, _workflow_patch = approval_gate._commit_custom_api(
            None,
            {'url': 'https://example.test/plain'},
            None,
            {},
        )

        self.assertEqual(output['api_response'], {'raw': 'x' * 2000})

    def test_missing_url_is_rejected_before_http_call(self):
        with self.assertRaisesRegex(ValueError, 'No URL'):
            approval_gate._commit_custom_api(None, {}, None, {})


class ForwardCommitTests(SimpleTestCase):
    @patch('agent.services._forward_to_users')
    def test_destination_filters_recipients_and_reports_successes_and_failures(self, mock_forward):
        mock_forward.return_value = [
            {'username': 'alex', 'status': 'sent'},
            {'username': 'sam', 'status': 'not_found'},
        ]
        orchestrator = SimpleNamespace(user='sender', project='project')
        draft = {
            'forwards': [
                {'username': 'alex', 'content': 'A'},
                {'username': 'sam', 'content': 'B'},
                {'username': 'pat', 'content': 'C'},
            ]
        }

        output, events, workflow_patch = approval_gate._commit_forward(
            orchestrator,
            draft,
            {'usernames': ['ALEX', 'sam']},
            {},
        )

        self.assertEqual(output, {})
        self.assertEqual(workflow_patch, {})
        self.assertEqual(
            events,
            [
                {'type': 'text', 'content': 'Message forwarded to: alex'},
                {'type': 'text', 'content': 'Could not forward to: sam'},
            ],
        )
        self.assertEqual(
            mock_forward.call_args.args[0],
            [
                {'username': 'alex', 'content': 'A'},
                {'username': 'sam', 'content': 'B'},
            ],
        )


class MiroCommitTests(SimpleTestCase):
    def test_miro_commit_requires_workflow_run_id(self):
        with self.assertRaisesRegex(ValueError, 'workflow_run_id required'):
            approval_gate._commit_miro_board(
                SimpleNamespace(project=object(), session=object()),
                {},
                None,
                {},
            )

    @patch('agent.models.AgentWorkflowRun.objects.get')
    def test_miro_commit_requires_snapshot(self, mock_get):
        mock_get.return_value = SimpleNamespace(miro_snapshot=None)

        with self.assertRaisesRegex(ValueError, 'No miro snapshot'):
            approval_gate._commit_miro_board(
                SimpleNamespace(project=object(), session=object()),
                {},
                None,
                {'workflow_run_id': 'run-1'},
            )

    @patch('agent.miro_board_service.create_board_from_snapshot')
    @patch('agent.models.AgentWorkflowRun.objects.get')
    def test_miro_commit_persists_board_and_merges_output(self, mock_get, mock_create_board):
        workflow_run = MagicMock(miro_snapshot={'items': []})
        board = SimpleNamespace(id='board-1', title='Campaign plan', slug='campaign-plan')
        persisted_snapshot = {'items': [{'id': 'sticky-1'}]}
        mock_get.return_value = workflow_run
        mock_create_board.return_value = (board, persisted_snapshot)
        orchestrator = SimpleNamespace(project='project', session='session')

        output, events, workflow_patch = approval_gate._commit_miro_board(
            orchestrator,
            {'snapshot': {'items': [{'id': 'draft-sticky'}]}},
            None,
            {'workflow_run_id': 'run-1', 'merge_output': {'analysis': 'kept'}},
        )

        self.assertEqual(
            output,
            {
                'analysis': 'kept',
                'miro_snapshot': persisted_snapshot,
                'miro_board_id': 'board-1',
            },
        )
        self.assertEqual(workflow_patch['miro_board_id'], 'board-1')
        self.assertEqual(events[0]['data'], {'board_id': 'board-1', 'board_slug': 'campaign-plan'})
        self.assertIs(workflow_run.miro_board, board)
        self.assertEqual(workflow_run.miro_snapshot, persisted_snapshot)
        workflow_run.save.assert_called_once_with(
            update_fields=['miro_board', 'miro_snapshot', 'updated_at']
        )


class CalendarCommitTests(SimpleTestCase):
    def test_calendar_commit_requires_organization_and_destination(self):
        orchestrator = SimpleNamespace(user=object())

        with self.assertRaisesRegex(ValueError, 'organization_id required'):
            approval_gate._commit_calendar_events(orchestrator, {}, None, {})
        with self.assertRaisesRegex(ValueError, 'calendar_id destination required'):
            approval_gate._commit_calendar_events(
                orchestrator,
                {},
                None,
                {'organization_id': 'org-1'},
            )

    @patch('calendars.models.Event.objects.create')
    @patch('calendars.models.Calendar.objects.get')
    def test_calendar_commit_creates_valid_events_and_skips_invalid_entries(
        self,
        mock_calendar_get,
        mock_event_create,
    ):
        calendar = object()
        mock_calendar_get.return_value = calendar
        mock_event_create.side_effect = [SimpleNamespace(id='event-1')]
        orchestrator = SimpleNamespace(user='owner')

        output, events, workflow_patch = approval_gate._commit_calendar_events(
            orchestrator,
            {
                'events': [
                    'invalid',
                    {
                        'title': 'Review results',
                        'description': 'Discuss campaign performance',
                        'start_datetime': '2026-09-03 10:00',
                        'end_datetime': None,
                        'location': 'Meeting room',
                    },
                ]
            },
            {'calendar_id': 'cal-1'},
            {'organization_id': 'org-1', 'user_timezone': 'Not/A-Timezone'},
        )

        self.assertEqual(output, {})
        self.assertEqual(workflow_patch, {'created_event_ids': ['event-1']})
        self.assertEqual(
            events,
            [{'type': 'calendar_updated', 'content': '', 'data': {'event_ids': ['event-1']}}],
        )
        created = mock_event_create.call_args.kwargs
        self.assertEqual(created['calendar'], calendar)
        self.assertEqual(created['title'], 'Review results')
        self.assertEqual(created['timezone'], 'UTC')
        self.assertIsNotNone(created['start_datetime'].tzinfo)
        self.assertIsNone(created['end_datetime'])


class CommitValidationTests(SimpleTestCase):
    def test_empty_task_and_decision_drafts_are_rejected(self):
        orchestrator = SimpleNamespace(project=object(), user=object(), session=object())

        with self.assertRaisesRegex(ValueError, 'No tasks in draft'):
            approval_gate._commit_task(orchestrator, {}, None, {})
        with self.assertRaisesRegex(ValueError, 'No decision tree nodes'):
            approval_gate._commit_decision_tree(orchestrator, {}, None, {})


@pytest.mark.django_db
def test_commit_task_handles_missing_decision_and_invalid_original_index(
    approval_org_project_user,
):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(
        user=user,
        project=project,
        approval_required=False,
    )
    orchestrator = AgentOrchestrator(user, project, session)

    output, events, workflow_patch = approval_gate._commit_task(
        orchestrator,
        {
            'recommended_tasks': [
                {
                    'index': 'not-an-integer',
                    # Keep the task valid so execution reaches the index fallback.
                    'type': 'execution',
                    'summary': 'Review campaign performance',
                    'priority': 'MEDIUM',
                    'description': '',
                }
            ]
        },
        None,
        {
            'decision_id': 999999,
            'input_data': {'source': 'analysis'},
            'analysis_result': {'recommended_tasks': []},
        },
    )

    assert output['source'] == 'analysis'
    assert workflow_patch['created_tasks']
    created = events[0]['data']['created_tasks'][0]
    assert created['index'] == 0
    assert created['summary'] == 'Review campaign performance'
    assert events[0]['data']['decision_id'] is None


@pytest.mark.django_db
def test_resolve_pending_rejects_unknown_approval(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=True)
    orchestrator = AgentOrchestrator(user, project, session)

    with pytest.raises(ValueError, match='Approval request not found'):
        resolve_pending(
            orchestrator=orchestrator,
            pending_id=str(uuid.uuid4()),
            decision='approve',
            draft=None,
            destination=None,
        )


@pytest.mark.django_db
def test_resolve_pending_rejects_already_resolved_approval(approval_org_project_user):
    _org, project, user = approval_org_project_user
    session = AgentSession.objects.create(user=user, project=project, approval_required=True)
    workflow_run = AgentWorkflowRun.objects.create(session=session, status='analyzing')
    pending = AgentPendingExternalApproval.objects.create(
        session=session,
        workflow_run=workflow_run,
        kind=KIND_TASK,
        status='approved',
        draft={},
        commit_context={},
        destination_options=[],
        default_destination=None,
    )
    orchestrator = AgentOrchestrator(user, project, session)

    with pytest.raises(ValueError, match='Approval is no longer pending'):
        resolve_pending(
            orchestrator=orchestrator,
            pending_id=str(pending.id),
            decision='approve',
            draft=None,
            destination=None,
        )
