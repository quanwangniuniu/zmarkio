import json
import tempfile
import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from core.models import Organization, Project, ProjectMember, CustomUser
from core.test_utils import grant_ai_consent
from .models import (
    AgentSession, AgentMessage, AgentWorkflowRun,
    AgentWorkflowDefinition, AgentWorkflowStep, AgentStepExecution,
    AgentWorkflowTemplate,
)
from .services import (
    AgentOrchestrator,
    _forward_to_users,
    _preprocess_spreadsheet,
    _resolve_analysis_columns,
)


def _test_anomaly(**overrides):
    """A single anomaly dict (with a stable id) for tests."""
    anomaly = {
        "id": "anom_0",
        "metric": "ROAS",
        "movement": "SHARP_DECREASE",
        "scope_type": "CAMPAIGN",
        "scope_value": "Test Campaign",
        "delta_value": -20.0,
        "delta_unit": "PERCENT",
        "period": "LAST_7_DAYS",
        "severity": "critical",
        "description": "Test Campaign ROAS dropped 20%",
    }
    anomaly.update(overrides)
    return anomaly


def _test_analysis_data(confirmed=True):
    """Minimal valid analysis structure for tests.

    Defaults to an already-confirmed analysis (anomalies reviewed and included)
    so the existing task-creation tests exercise the post-confirmation path.
    Pass ``confirmed=False`` for tests that need the pre-confirmation state.
    """
    anomaly = _test_anomaly()
    analysis = {
        "anomalies": [dict(anomaly)],
        "recommended_tasks": [
            {"type": "optimization", "summary": "Test task", "priority": "MEDIUM"},
        ],
    }
    if confirmed:
        analysis["reviewed_anomalies"] = [dict(anomaly, included=True)]
        analysis["anomalies_confirmed"] = True
    return analysis


class AgentModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org', slug='test-org')
        self.user = CustomUser.objects.create_user(
            email='agent@test.com',
            username='agentuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project',
            organization=self.org,
            owner=self.user,
        )

    def test_create_session(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Test Session',
        )
        self.assertEqual(session.status, 'active')
        self.assertEqual(str(session.id), str(session.pk))

    def test_create_message(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        msg = AgentMessage.objects.create(
            session=session,
            role='user',
            content='Hello agent',
        )
        self.assertEqual(msg.message_type, 'text')
        self.assertEqual(msg.metadata, {})

    def test_create_workflow_run(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        run = AgentWorkflowRun.objects.create(
            session=session,
            status='analyzing',
        )
        self.assertEqual(run.status, 'analyzing')
        self.assertIsNone(run.spreadsheet)


class AgentSessionAPITests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org API', slug='test-org-api')
        self.user = CustomUser.objects.create_user(
            email='agentapi@test.com',
            username='agentapiuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project API',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            role='owner',
            is_active=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_session(self):
        response = self.client.post(
            '/api/agent/sessions/',
            {'project_id': self.project.id},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', response.data)

    def test_list_sessions(self):
        AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Session 1',
        )
        response = self.client.get(
            '/api/agent/sessions/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_retrieve_session(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Detail Session',
        )
        AgentMessage.objects.create(
            session=session,
            role='user',
            content='test message',
        )
        response = self.client.get(
            f'/api/agent/sessions/{session.id}/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['messages']), 1)

    def test_retrieve_session_excludes_deleted_messages(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Deleted message session',
        )
        AgentMessage.objects.create(
            session=session,
            role='user',
            content='visible',
        )
        deleted = AgentMessage.objects.create(
            session=session,
            role='assistant',
            content='hidden',
        )
        deleted.is_deleted = True
        deleted.save(update_fields=['is_deleted'])

        response = self.client.get(
            f'/api/agent/sessions/{session.id}/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['messages']), 1)
        self.assertEqual(response.data['messages'][0]['content'], 'visible')

    def test_retrieve_session_preserves_message_order(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Ordered session',
        )
        AgentMessage.objects.create(session=session, role='user', content='first')
        AgentMessage.objects.create(session=session, role='assistant', content='second')

        response = self.client.get(
            f'/api/agent/sessions/{session.id}/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        contents = [m['content'] for m in response.data['messages']]
        self.assertEqual(contents, ['first', 'second'])

    def test_delete_session(self):
        session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        response = self.client.delete(
            f'/api/agent/sessions/{session.id}/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        session.refresh_from_db()
        self.assertTrue(session.is_deleted)

    def test_list_sessions_pinned_first(self):
        old = AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Old',
            is_pinned=False,
        )
        pinned = AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Pinned',
            is_pinned=True,
        )
        response = self.client.get(
            '/api/agent/sessions/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row['id'] for row in response.data]
        self.assertEqual(ids[0], str(pinned.id))
        self.assertEqual(ids[1], str(old.id))

    def test_list_sessions_search(self):
        AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Alpha board',
        )
        AgentSession.objects.create(
            user=self.user,
            project=self.project,
            title='Beta other',
        )
        response = self.client.get(
            '/api/agent/sessions/',
            {'search': 'Alpha'},
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], 'Alpha board')

    def test_patch_session_status_and_pin(self):
        session = AgentSession.objects.create(user=self.user, project=self.project)
        response = self.client.patch(
            f'/api/agent/sessions/{session.id}/',
            {'status': 'archived', 'is_pinned': True},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        session.refresh_from_db()
        self.assertEqual(session.status, 'archived')
        self.assertTrue(session.is_pinned)

    def test_list_has_unread_when_newer_assistant_than_read_cursor(self):
        from django.utils import timezone
        from datetime import timedelta

        session = AgentSession.objects.create(user=self.user, project=self.project)
        AgentMessage.objects.create(session=session, role='assistant', content='First')
        session.last_read_at = timezone.now() - timedelta(hours=1)
        session.save()
        AgentMessage.objects.create(session=session, role='assistant', content='Second')
        response = self.client.get(
            '/api/agent/sessions/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        row = next(r for r in response.data if r['id'] == str(session.id))
        self.assertTrue(row['has_unread'])

    def test_list_has_unread_false_when_read_cursor_covers_latest_assistant(self):
        from django.utils import timezone

        session = AgentSession.objects.create(user=self.user, project=self.project)
        AgentMessage.objects.create(session=session, role='assistant', content='Hi')
        session.last_read_at = timezone.now()
        session.save()
        response = self.client.get(
            '/api/agent/sessions/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        row = next(r for r in response.data if r['id'] == str(session.id))
        self.assertFalse(row['has_unread'])

    def test_list_has_unread_false_without_assistant_messages(self):
        session = AgentSession.objects.create(user=self.user, project=self.project)
        AgentMessage.objects.create(session=session, role='user', content='Only user')
        response = self.client.get(
            '/api/agent/sessions/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        row = next(r for r in response.data if r['id'] == str(session.id))
        self.assertFalse(row['has_unread'])


class AgentSessionAppendMessageAPITests(APITestCase):
    """Covers the lightweight messages action used by out-of-band generation
    flows (NL pattern/pivot generation) to persist transcript turns without
    invoking the agent pipeline."""

    def setUp(self):
        self.org = Organization.objects.create(name='Test Org Append', slug='test-org-append')
        self.user = CustomUser.objects.create_user(
            email='agentappend@test.com',
            username='agentappenduser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project Append',
            organization=self.org,
            owner=self.user,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_append_user_and_assistant_messages(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'user', 'content': 'summarize revenue by region'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['role'], 'user')
        self.assertEqual(response.data['content'], 'summarize revenue by region')

        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'assistant', 'content': 'Done! Created a pivot sheet.'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.session.messages.count(), 2)

    def test_append_message_defaults_message_type_to_text(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'assistant', 'content': 'hello'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['message_type'], 'text')

    def test_append_message_accepts_error_message_type(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'assistant', 'content': 'boom', 'message_type': 'error'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['message_type'], 'error')

    def test_append_message_rejects_invalid_role(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'system-hacker', 'content': 'hello'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_append_message_rejects_blank_content(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'user', 'content': '   '},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_append_message_persists_and_is_returned_on_retrieve(self):
        self.client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'user', 'content': 'add a pivot table'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        response = self.client.get(
            f'/api/agent/sessions/{self.session.id}/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['messages']), 1)
        self.assertEqual(response.data['messages'][0]['content'], 'add a pivot table')

    def test_append_message_scoped_to_owning_user(self):
        other = CustomUser.objects.create_user(
            email='otherappend@test.com',
            username='otherappenduser',
            password='testpass123',
        )
        other.organization = self.org
        other.save()
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        response = other_client.post(
            f'/api/agent/sessions/{self.session.id}/messages/',
            {'role': 'user', 'content': 'not mine'},
            format='json',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ChatAPITests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org Chat', slug='test-org-chat')
        self.user = CustomUser.objects.create_user(
            email='chat@test.com',
            username='chatuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project Chat',
            organization=self.org,
            owner=self.user,
        )
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_chat_returns_sse_response(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            {'message': 'Hello'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'text/event-stream')

    def test_chat_creates_messages(self):
        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            {'message': 'Hello agent'},
            format='json',
        )
        # Consume the streaming response
        content = b''.join(response.streaming_content).decode()
        self.assertIn('"type": "done"', content)

        # Check messages were saved
        messages = AgentMessage.objects.filter(session=self.session)
        self.assertEqual(messages.filter(role='user').count(), 1)
        self.assertEqual(messages.filter(role='assistant').count(), 1)

    def test_chat_session_not_found(self):
        fake_id = uuid.uuid4()
        response = self.client.post(
            f'/api/agent/sessions/{fake_id}/chat/',
            {'message': 'Hello'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_chat_sets_session_title(self):
        self.client.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            {'message': 'Analyze my campaign data'},
            format='json',
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, 'Analyze my campaign data')


class SpreadsheetListAPITests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org SS', slug='test-org-ss')
        self.user = CustomUser.objects.create_user(
            email='ss@test.com',
            username='ssuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project SS',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_list_spreadsheets(self):
        from spreadsheet.models import Spreadsheet
        Spreadsheet.objects.create(project=self.project, name='Test Sheet')
        response = self.client.get(
            '/api/agent/spreadsheets/',
            HTTP_X_PROJECT_ID=str(self.project.id),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['name'], 'Test Sheet')

    def test_list_spreadsheets_no_project(self):
        response = self.client.get('/api/agent/spreadsheets/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OrchestratorTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org Orch', slug='test-org-orch')
        self.user = CustomUser.objects.create_user(
            email='orch@test.com',
            username='orchuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project Orch',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            role='owner',
            is_active=True,
        )
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )

    def test_handle_message_general_chat(self):
        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("hello"))
        types = [c['type'] for c in chunks]
        self.assertIn('text', types)
        self.assertIn('done', types)

    def test_create_tasks_from_decision(self):
        from decision.models import Decision
        from task.models import Task

        decision = Decision.objects.create(
            title='Test Decision',
            project=self.project,
            project_seq=1,
            author=self.user,
        )
        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            decision=decision,
            analysis_result=_test_analysis_data(),
        )
        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.create_tasks_from_analysis(workflow_run))
        types = [c['type'] for c in chunks]
        self.assertIn('task_created', types)

        # Verify tasks were created
        tasks = Task.objects.filter(project=self.project)
        self.assertTrue(tasks.exists())
        workflow_run.refresh_from_db()
        self.assertEqual(workflow_run.status, 'completed')


    @patch('agent.services._call_gemini_chat')
    def test_follow_up_completed_marks_run_and_passes_project_members(self, mock_call_gemini_chat):
        teammate = CustomUser.objects.create_user(
            email='alice@test.com',
            username='alice',
            password='testpass123',
            first_name='Alice',
            last_name='Chen',
        )
        teammate.organization = self.org
        teammate.save()
        ProjectMember.objects.create(
            user=teammate,
            project=self.project,
            role='member',
            is_active=True,
        )
        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result=_test_analysis_data(),
            chat_follow_up_started=True,
        )
        AgentMessage.objects.create(
            session=self.session,
            role='assistant',
            content='Analysis complete.',
        )
        mock_call_gemini_chat.return_value = {
            'status': 'completed',
            'text': 'Prepared a summary.',
            'forwards': [],
        }

        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("Explain this to me."))

        self.assertIn(
            {'type': 'text', 'content': 'Prepared a summary.'},
            chunks,
        )
        workflow_run.refresh_from_db()
        self.assertTrue(workflow_run.chat_followed_up)

        self.assertTrue(mock_call_gemini_chat.called)
        project_members = mock_call_gemini_chat.call_args.kwargs['project_members']
        current_username = mock_call_gemini_chat.call_args.kwargs['current_username']
        usernames = {member['username'] for member in project_members}
        self.assertEqual(current_username, 'orchuser')
        self.assertIn('orchuser', usernames)
        self.assertIn('alice', usernames)
        self.assertNotIn('agent-bot', usernames)

    @patch('agent.services._call_gemini_chat')
    def test_follow_up_needs_clarification_keeps_run_open(self, mock_call_gemini_chat):
        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result=_test_analysis_data(),
            chat_follow_up_started=True,
        )
        mock_call_gemini_chat.return_value = {
            'status': 'needs_clarification',
            'text': 'Please provide the exact username.',
            'forwards': [],
        }

        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("Forward this to Alex."))

        self.assertIn(
            {'type': 'text', 'content': 'Please provide the exact username.'},
            chunks,
        )
        self.assertEqual(
            mock_call_gemini_chat.call_args.kwargs['current_username'],
            'orchuser',
        )
        workflow_run.refresh_from_db()
        self.assertFalse(workflow_run.chat_followed_up)
        self.assertTrue(workflow_run.chat_follow_up_started)

    def test_start_follow_up_marks_run_started_and_returns_prompt(self):
        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result=_test_analysis_data(),
        )

        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("start_follow_up", action="start_follow_up"))

        self.assertIn(
            {
                'type': 'follow_up_prompt',
                'content': 'Follow-up chat started. Ask one follow-up question about the analysis, or include the exact username/email if you want me to prepare a forwarded message.',
                'data': {'workflow_run_id': str(workflow_run.id)},
            },
            chunks,
        )
        workflow_run.refresh_from_db()
        self.assertTrue(workflow_run.chat_follow_up_started)
        self.assertFalse(workflow_run.chat_followed_up)

    def test_cancel_follow_up_marks_run_inactive(self):
        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result=_test_analysis_data(),
            chat_follow_up_started=True,
        )

        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("cancel_follow_up", action="cancel_follow_up"))

        self.assertIn(
            {
                'type': 'text',
                'content': 'Follow-up chat closed.',
                'data': {'workflow_run_id': str(workflow_run.id)},
            },
            chunks,
        )
        workflow_run.refresh_from_db()
        self.assertFalse(workflow_run.chat_follow_up_started)
        self.assertFalse(workflow_run.chat_followed_up)

    @patch('agent.services._call_gemini_chat')
    def test_follow_up_requires_explicit_start(self, mock_call_gemini_chat):
        AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result=_test_analysis_data(),
        )

        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("Explain this to me."))

        self.assertIn(
            {
                'type': 'text',
                'content': (
                    "I can help you analyze spreadsheet data and recommended tasks. "
                    "To get started, select a spreadsheet and use the 'analyze' action."
                ),
            },
            chunks,
        )
        mock_call_gemini_chat.assert_not_called()

    def test_forward_to_users_does_not_match_first_name(self):
        teammate = CustomUser.objects.create_user(
            email='alice-fn@test.com',
            username='alice-ops',
            password='testpass123',
            first_name='Alice',
            last_name='Operator',
        )
        teammate.organization = self.org
        teammate.save()
        ProjectMember.objects.create(
            user=teammate,
            project=self.project,
            role='member',
            is_active=True,
        )

        results = _forward_to_users(
            [{'username': 'Alice', 'content': 'Please review the report.'}],
            self.user,
            self.project,
        )

        self.assertEqual(results[0]['status'], 'not_found')

    def test_forward_to_users_reactivates_existing_private_chat(self):
        from chat.models import Chat, ChatOutboxEvent, ChatParticipant, ChatType
        from core.utils.bot_user import get_agent_bot_user

        teammate = CustomUser.objects.create_user(
            email='alice-chat@test.com',
            username='alice-chat',
            password='testpass123',
        )
        teammate.organization = self.org
        teammate.save()
        ProjectMember.objects.create(
            user=teammate,
            project=self.project,
            role='member',
            is_active=True,
        )

        bot = get_agent_bot_user()
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=bot, is_active=True)
        participant = ChatParticipant.objects.create(chat=chat, user=teammate, is_active=False)

        results = _forward_to_users(
            [{'username': 'alice-chat', 'content': 'Please review Campaign A.'}],
            self.user,
            self.project,
        )

        participant.refresh_from_db()
        self.assertTrue(participant.is_active)
        self.assertEqual(results[0]['status'], 'sent')
        self.assertEqual(
            ChatOutboxEvent.objects.filter(
                event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
            ).count(),
            1,
        )


class AnomalyIdAssignmentTests(TestCase):
    """_assign_anomaly_ids: stable ids + zero-anomaly auto-confirm."""

    def test_assigns_sequential_ids(self):
        from agent.services import _assign_anomaly_ids
        analysis = {"anomalies": [{"metric": "ROAS"}, {"metric": "CPA"}]}
        result = _assign_anomaly_ids(analysis)
        self.assertEqual(result["anomalies"][0]["id"], "anom_0")
        self.assertEqual(result["anomalies"][1]["id"], "anom_1")

    def test_idempotent_existing_ids_untouched(self):
        from agent.services import _assign_anomaly_ids
        analysis = {"anomalies": [{"id": "keep_me", "metric": "ROAS"}]}
        result = _assign_anomaly_ids(analysis)
        self.assertEqual(result["anomalies"][0]["id"], "keep_me")

    def test_zero_anomalies_auto_confirmed(self):
        from agent.services import _assign_anomaly_ids
        analysis = {"anomalies": [], "recommended_tasks": [{"summary": "x"}]}
        result = _assign_anomaly_ids(analysis)
        self.assertTrue(result["anomalies_confirmed"])

    def test_nonempty_anomalies_not_auto_confirmed(self):
        from agent.services import _assign_anomaly_ids
        analysis = {"anomalies": [{"metric": "ROAS"}]}
        result = _assign_anomaly_ids(analysis)
        self.assertNotIn("anomalies_confirmed", result)


class AnomalyConfirmationTests(TestCase):
    """confirm_anomalies action + task-creation gate."""

    def setUp(self):
        self.org = Organization.objects.create(name='Org Anom', slug='org-anom')
        self.user = CustomUser.objects.create_user(
            email='anom@test.com', username='anomuser', password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Project Anom', organization=self.org, owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role='owner', is_active=True,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)
        self.orch = AgentOrchestrator(self.user, self.project, self.session)

    def _unconfirmed_run(self):
        return AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result=_test_analysis_data(confirmed=False),
        )

    def _full_payload(self, included=True, **overrides):
        entry = {"id": "anom_0", "included": included,
                 "severity": "critical", "description": "edited desc"}
        entry.update(overrides)
        return [entry]

    # --- confirm_anomalies ------------------------------------------------

    def test_confirm_persists_reviewed_list_full_objects(self):
        run = self._unconfirmed_run()
        chunks = list(self.orch.confirm_anomalies(run, self._full_payload()))
        types = [c['type'] for c in chunks]
        self.assertIn('anomalies_confirmed', types)
        run.refresh_from_db()
        ar = run.analysis_result
        self.assertTrue(ar['anomalies_confirmed'])
        reviewed = ar['reviewed_anomalies']
        self.assertEqual(len(reviewed), 1)
        # full merged object retains original display fields
        self.assertEqual(reviewed[0]['metric'], 'ROAS')
        self.assertEqual(reviewed[0]['period'], 'LAST_7_DAYS')
        self.assertTrue(reviewed[0]['included'])
        # original anomalies untouched
        self.assertNotIn('included', ar['anomalies'][0])

    def test_confirm_merges_severity_and_description_edits(self):
        run = self._unconfirmed_run()
        list(self.orch.confirm_anomalies(
            run, self._full_payload(severity='info', description='new text')))
        run.refresh_from_db()
        reviewed = run.analysis_result['reviewed_anomalies'][0]
        self.assertEqual(reviewed['severity'], 'info')
        self.assertEqual(reviewed['description'], 'new text')

    def test_confirm_unknown_id_rejected_atomically(self):
        run = self._unconfirmed_run()
        payload = [{"id": "anom_0", "included": True},
                   {"id": "ghost", "included": True}]
        chunks = list(self.orch.confirm_anomalies(run, payload))
        self.assertEqual(chunks[0]['type'], 'error')
        run.refresh_from_db()
        self.assertNotIn('anomalies_confirmed', run.analysis_result)

    def test_confirm_missing_id_rejected_atomically(self):
        run = AgentWorkflowRun.objects.create(
            session=self.session, status='awaiting_confirmation',
            analysis_result={
                "anomalies": [_test_anomaly(id="anom_0"), _test_anomaly(id="anom_1")],
                "recommended_tasks": [{"summary": "x"}],
            },
        )
        payload = [{"id": "anom_0", "included": True}]  # missing anom_1
        chunks = list(self.orch.confirm_anomalies(run, payload))
        self.assertEqual(chunks[0]['type'], 'error')
        run.refresh_from_db()
        self.assertNotIn('anomalies_confirmed', run.analysis_result)

    def test_confirm_duplicate_id_rejected_atomically(self):
        run = self._unconfirmed_run()
        payload = [{"id": "anom_0", "included": True},
                   {"id": "anom_0", "included": False}]
        chunks = list(self.orch.confirm_anomalies(run, payload))
        self.assertEqual(chunks[0]['type'], 'error')
        run.refresh_from_db()
        self.assertNotIn('anomalies_confirmed', run.analysis_result)

    def test_confirm_invalid_severity_rejected(self):
        run = self._unconfirmed_run()
        chunks = list(self.orch.confirm_anomalies(
            run, self._full_payload(severity='nope')))
        self.assertEqual(chunks[0]['type'], 'error')
        run.refresh_from_db()
        self.assertNotIn('anomalies_confirmed', run.analysis_result)

    def test_already_confirmed_is_safe_noop_on_partial_payload(self):
        run = AgentWorkflowRun.objects.create(
            session=self.session, status='awaiting_confirmation',
            analysis_result=_test_analysis_data(confirmed=True),
        )
        # Partial/stale payload (missing ids) must NOT error -> no-op.
        chunks = list(self.orch.confirm_anomalies(run, []))
        self.assertEqual(chunks[0]['type'], 'anomalies_confirmed')
        self.assertTrue(chunks[0].get('already_confirmed'))

    def test_confirm_no_run_errors(self):
        chunks = list(self.orch.confirm_anomalies(None, self._full_payload()))
        self.assertEqual(chunks[0]['type'], 'error')

    def test_confirm_updates_stored_analysis_message_for_restore(self):
        run = self._unconfirmed_run()
        # Simulate the persisted analysis message the SSE stream would have saved.
        msg = AgentMessage.objects.create(
            session=self.session,
            role='assistant',
            content='Found 1 anomalies.',
            message_type='analysis',
            metadata={'anomalies': [_test_anomaly()]},
        )
        list(self.orch.confirm_anomalies(run, self._full_payload(included=False)))
        msg.refresh_from_db()
        self.assertTrue(msg.metadata['anomalies_confirmed'])
        self.assertEqual(len(msg.metadata['reviewed_anomalies']), 1)
        self.assertFalse(msg.metadata['reviewed_anomalies'][0]['included'])

    # --- task-creation gate ------------------------------------------------

    def test_create_tasks_blocked_before_confirmation(self):
        from task.models import Task
        run = self._unconfirmed_run()
        chunks = list(self.orch.create_tasks_from_analysis(run))
        types = [c['type'] for c in chunks]
        self.assertIn('error', types)
        self.assertNotIn('task_created', types)
        self.assertFalse(Task.objects.filter(project=self.project).exists())

    def test_create_tasks_allowed_after_confirmation(self):
        from task.models import Task
        run = self._unconfirmed_run()
        list(self.orch.confirm_anomalies(run, self._full_payload(included=True)))
        run.refresh_from_db()
        chunks = list(self.orch.create_tasks_from_analysis(run))
        types = [c['type'] for c in chunks]
        self.assertIn('task_created', types)
        self.assertTrue(Task.objects.filter(project=self.project).exists())

    def test_all_excluded_still_creates_tasks_when_requested(self):
        from task.models import Task
        run = self._unconfirmed_run()
        list(self.orch.confirm_anomalies(run, self._full_payload(included=False)))
        run.refresh_from_db()
        self.assertTrue(run.analysis_result['anomalies_confirmed'])
        chunks = list(self.orch.create_tasks_from_analysis(run))
        types = [c['type'] for c in chunks]
        self.assertIn('task_created', types)
        self.assertTrue(Task.objects.filter(project=self.project).exists())

    def test_create_tasks_with_anomalies_but_no_reviewed_list(self):
        """Spreadsheet insights auto-confirms anomalies without reviewed_anomalies."""
        from task.models import Task
        run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='awaiting_confirmation',
            analysis_result={
                'anomalies': [_test_anomaly()],
                'anomalies_confirmed': True,
                'recommended_tasks': [
                    {'type': 'alert', 'summary': 'Investigate spike', 'priority': 'HIGH'},
                ],
            },
        )
        chunks = list(self.orch.create_tasks_from_analysis(run))
        types = [c['type'] for c in chunks]
        contents = ' '.join(c.get('content', '') for c in chunks)
        self.assertIn('task_created', types)
        self.assertNotIn('All anomalies were excluded', contents)
        self.assertTrue(Task.objects.filter(project=self.project).exists())

    def test_zero_anomalies_preserves_task_creation(self):
        from task.models import Task
        # Clean dataset: no anomalies key at all and no confirmation flag — the
        # gate must NOT block; tasks still flow (existing behaviour preserved).
        run = AgentWorkflowRun.objects.create(
            session=self.session, status='awaiting_confirmation',
            analysis_result={
                "recommended_tasks": [
                    {"type": "optimization", "summary": "Monitor", "priority": "LOW"},
                ],
            },
        )
        chunks = list(self.orch.create_tasks_from_analysis(run))
        types = [c['type'] for c in chunks]
        self.assertIn('task_created', types)
        self.assertTrue(Task.objects.filter(project=self.project).exists())

    def test_included_anomalies_attached_to_commit_context(self):
        captured = {}
        run = self._unconfirmed_run()
        list(self.orch.confirm_anomalies(run, self._full_payload(included=True)))
        run.refresh_from_db()

        from agent import approval_gate

        real = approval_gate.request_external_commit

        def _spy(*args, **kwargs):
            captured['commit_context'] = kwargs.get('commit_context')
            return real(*args, **kwargs)

        with patch('agent.approval_gate.request_external_commit', side_effect=_spy):
            list(self.orch.create_tasks_from_analysis(run))

        self.assertIn('commit_context', captured)
        included = captured['commit_context'].get('included_anomalies')
        self.assertEqual(len(included), 1)
        self.assertEqual(included[0]['id'], 'anom_0')


class CalendarAgentTests(TestCase):
    """
    Verify that answer_calendar_question() correctly routes calendar
    context through the Dify Calendar workflow and handles responses.
    """

    def setUp(self):
        self.org = Organization.objects.create(name='Test Org Cal', slug='test-org-cal')
        self.user = CustomUser.objects.create_user(
            email='cal@test.com',
            username='caluser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project Cal',
            organization=self.org,
            owner=self.user,
        )
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        self.orchestrator = AgentOrchestrator(self.user, self.project, self.session)

    def _make_calendar_and_event(self, days_offset=1):
        """Create a Calendar + Event for this org and return (calendar, event)."""
        from calendars.models import Calendar as CalendarModel, Event as EventModel
        from django.utils import timezone
        cal = CalendarModel.objects.create(
            organization=self.org,
            owner=self.user,
            name='Test Calendar',
        )
        now = timezone.now()
        event = EventModel.objects.create(
            organization=self.org,
            calendar=cal,
            created_by=self.user,
            title='Team Standup',
            description='Daily sync',
            start_datetime=now + timezone.timedelta(days=days_offset),
            end_datetime=now + timezone.timedelta(days=days_offset, hours=1),
            timezone='UTC',
        )
        return cal, event

    # ------------------------------------------------------------------ #
    # handle_message routing                                              #
    # ------------------------------------------------------------------ #

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('core.services.gemini_client.call_gemini')
    def test_handle_message_routes_to_calendar_when_context_provided(self, mock_call_gemini):
        """handle_message with calendar_context skips general chat and calls Gemini calendar."""
        mock_call_gemini.return_value = '{"answer": "You have 1 event.", "create_events": []}'

        calendar_context = {'type': 'calendar', 'calendarIds': [], 'currentView': 'week'}
        chunks = list(self.orchestrator.handle_message(
            'What is on my calendar?',
            calendar_context=calendar_context,
        ))
        types = [c['type'] for c in chunks]
        self.assertIn('text', types)
        self.assertIn('done', types)
        self.assertTrue(mock_call_gemini.called)

    @patch('agent.services.requests.post')
    def test_handle_message_without_calendar_context_skips_calendar(self, mock_post):
        """handle_message without calendar_context must NOT call the calendar Dify endpoint."""
        chunks = list(self.orchestrator.handle_message('Hello'))
        # requests.post should not have been called for the calendar workflow
        self.assertFalse(mock_post.called)

    # ------------------------------------------------------------------ #
    # _fetch_events_for_context                                           #
    # ------------------------------------------------------------------ #

    def test_fetch_events_returns_upcoming_events(self):
        """Events within the 30-day past / 60-day future window are returned."""
        _, event = self._make_calendar_and_event(days_offset=5)
        calendar_context = {'type': 'calendar', 'calendarIds': []}
        events = self.orchestrator._fetch_events_for_context(calendar_context)
        event_ids = [str(e.id) for e in events]
        self.assertIn(str(event.id), event_ids)

    def test_fetch_events_returns_specific_event_by_id(self):
        """When eventId is given, only that event is returned."""
        _, event = self._make_calendar_and_event(days_offset=3)
        calendar_context = {'type': 'event', 'eventId': str(event.id)}
        events = self.orchestrator._fetch_events_for_context(calendar_context)
        self.assertEqual(len(events), 1)
        self.assertEqual(str(events[0].id), str(event.id))

    def test_fetch_events_filters_by_calendar_ids(self):
        """calendarIds filter limits results to the specified calendar."""
        from calendars.models import Calendar as CalendarModel, Event as EventModel
        from django.utils import timezone

        cal1, event1 = self._make_calendar_and_event(days_offset=2)
        cal2 = CalendarModel.objects.create(
            organization=self.org, owner=self.user, name='Other Calendar',
        )
        now = timezone.now()
        event2 = EventModel.objects.create(
            organization=self.org,
            calendar=cal2,
            created_by=self.user,
            title='Other Event',
            start_datetime=now + timezone.timedelta(days=2),
            end_datetime=now + timezone.timedelta(days=2, hours=1),
            timezone='UTC',
        )

        context_cal1_only = {'type': 'calendar', 'calendarIds': [str(cal1.id)]}
        events = self.orchestrator._fetch_events_for_context(context_cal1_only)
        ids = [str(e.id) for e in events]
        self.assertIn(str(event1.id), ids)
        self.assertNotIn(str(event2.id), ids)

    def test_fetch_events_returns_empty_for_unknown_event_id(self):
        """Non-existent eventId returns empty list (no crash)."""
        context = {'type': 'event', 'eventId': str(uuid.uuid4())}
        events = self.orchestrator._fetch_events_for_context(context)
        self.assertEqual(events, [])

    # ------------------------------------------------------------------ #
    # answer_calendar_question — Dify response handling                  #
    # ------------------------------------------------------------------ #

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('core.services.gemini_client.call_gemini')
    def test_answer_calendar_question_yields_text_chunk(self, mock_call_gemini):
        """A successful Gemini response yields a text chunk with the answer."""
        mock_call_gemini.return_value = '{"answer": "You have 2 events this week.", "create_events": []}'

        self._make_calendar_and_event(days_offset=1)
        context = {'type': 'calendar', 'calendarIds': []}
        chunks = list(self.orchestrator.answer_calendar_question('What is on my calendar?', context))
        text_chunks = [c for c in chunks if c['type'] == 'text' and 'events this week' in c.get('content', '')]
        self.assertTrue(len(text_chunks) > 0)

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('core.services.gemini_client.call_gemini')
    def test_answer_calendar_question_creates_event_from_dify(self, mock_call_gemini):
        """When Gemini returns create_events, the events are created in the DB."""
        from calendars.models import Calendar as CalendarModel, Event as EventModel
        CalendarModel.objects.create(
            organization=self.org, owner=self.user, name='My Calendar',
        )
        mock_call_gemini.return_value = json.dumps({
            'answer': 'I have scheduled a meeting for you.',
            'create_events': [{
                'title': 'AI Scheduled Meeting',
                'description': 'Auto-created by agent',
                'start_datetime': '2026-04-01T10:00:00+00:00',
                'end_datetime': '2026-04-01T11:00:00+00:00',
            }]
        })

        before_count = EventModel.objects.filter(organization=self.org).count()
        context = {'type': 'calendar', 'calendarIds': []}
        list(self.orchestrator.answer_calendar_question('Schedule a meeting', context))
        after_count = EventModel.objects.filter(organization=self.org).count()
        self.assertEqual(after_count, before_count + 1)

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key'})
    @patch('core.services.gemini_client.call_gemini')
    def test_answer_calendar_question_dify_error_yields_error_chunk(self, mock_call_gemini):
        """A Gemini error yields an error chunk without raising."""
        mock_call_gemini.side_effect = Exception('Network timeout')
        context = {'type': 'calendar', 'calendarIds': []}
        chunks = list(self.orchestrator.answer_calendar_question('What is on my calendar?', context))
        error_chunks = [c for c in chunks if c['type'] == 'error']
        self.assertTrue(len(error_chunks) > 0)

    @override_settings(GEMINI_API_KEY='')
    @patch.dict('os.environ', {'GEMINI_API_KEY': ''}, clear=False)
    def test_answer_calendar_question_no_api_key_yields_error(self):
        """Missing GEMINI_API_KEY yields a configuration error chunk."""
        context = {'type': 'calendar', 'calendarIds': []}
        chunks = list(self.orchestrator.answer_calendar_question('Any events?', context))
        error_chunks = [c for c in chunks if c['type'] == 'error']
        self.assertTrue(len(error_chunks) > 0)


def _create_default_workflow():
    """Helper: create a system default 3-step workflow definition."""
    wf = AgentWorkflowDefinition.objects.create(
        name='Default Analysis Workflow',
        description='Analyze, confirm, create tasks',
        is_default=True,
        is_system=True,
        status='active',
    )
    steps = [
        ('Analyze Data', 'analyze_data', 1, {}),
        ('Confirm Analysis', 'await_confirmation', 2,
         {'message': 'Analysis complete. Create recommended tasks?'}),
        ('Create Tasks', 'create_tasks', 3, {}),
    ]
    for name, step_type, order, config in steps:
        AgentWorkflowStep.objects.create(
            workflow=wf, name=name, step_type=step_type,
            order=order, config=config,
        )
    return wf


class WorkflowEngineTests(TestCase):
    """AGENT-9: Workflow engine tests."""

    def setUp(self):
        self.org = Organization.objects.create(name='Test Org WF', slug='test-org-wf')
        self.user = CustomUser.objects.create_user(
            email='wf@test.com',
            username='wfuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project WF',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            role='owner',
            is_active=True,
        )
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        self.workflow = _create_default_workflow()
        self.orchestrator = AgentOrchestrator(self.user, self.project, self.session)

    def test_workflow_definition_creation(self):
        """Workflow + steps are created with correct ordering."""
        self.assertEqual(self.workflow.steps.count(), 3)
        orders = list(self.workflow.steps.values_list('order', flat=True))
        self.assertEqual(orders, [1, 2, 3])

    def test_default_workflow_lookup_system(self):
        """File upload path resolves to the system default workflow."""
        found = self.orchestrator._resolve_workflow(file_id='test-file-trigger')
        self.assertEqual(found, self.workflow)

    def test_resolve_workflow_plain_text_returns_none(self):
        """Chat without workflow_id does not auto-pick a workflow."""
        AgentWorkflowDefinition.objects.create(
            name='Project Workflow',
            project=self.project,
            is_default=True,
            status='active',
        )
        found = self.orchestrator._resolve_workflow()
        self.assertIsNone(found)

    def test_default_workflow_lookup_explicit_id(self):
        """Explicit workflow_id overrides all defaults."""
        other_wf = AgentWorkflowDefinition.objects.create(
            name='Other Workflow',
            status='active',
        )
        found = self.orchestrator._resolve_workflow(workflow_id=other_wf.id)
        self.assertEqual(found, other_wf)

    def test_executor_registry_complete(self):
        """All workflow step types have a registered executor."""
        from .executors import EXECUTOR_REGISTRY
        expected = {
            'analyze_data', 'call_dify', 'call_llm',
            'create_decision', 'create_tasks',
            'generate_miro_snapshot', 'create_miro_board',
            'await_confirmation', 'custom_api',
            'detect_columns', 'normalize_data',
            'generate_criteria',
            'loop', 'merge', 'if_else',  # Flow control step types
        }
        self.assertEqual(set(EXECUTOR_REGISTRY.keys()), expected)

    @patch('agent.services._run_analysis')
    def test_analyze_data_executor(self, mock_analysis):
        """AnalyzeDataExecutor calls _run_analysis and returns SSE events."""
        from .executors import AnalyzeDataExecutor

        mock_analysis.return_value = _test_analysis_data()

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
        )
        step = self.workflow.steps.get(order=1)
        executor = AnalyzeDataExecutor(step, run, self.orchestrator)
        result = executor.execute({'spreadsheet_data': {'name': 'test', 'sheets': []}})

        self.assertTrue(result.success)
        self.assertIn('analysis_result', result.output_data)
        self.assertEqual(len(result.sse_events), 1)
        self.assertEqual(result.sse_events[0]['type'], 'analysis')
        mock_analysis.assert_called_once()

    @patch('agent.services._run_analysis')
    def test_await_confirmation_pauses_workflow(self, mock_analysis):
        """Workflow pauses at await_confirmation step and updates run status."""
        mock_analysis.return_value = _test_analysis_data()

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
            current_step_order=1,
        )

        chunks = list(self.orchestrator._execute_steps(
            run, {'spreadsheet_data': {'name': 'test', 'sheets': []}}
        ))

        run.refresh_from_db()
        self.assertEqual(run.status, 'awaiting_confirmation')
        self.assertEqual(run.current_step_order, 3)

        # Should have: step_progress(1), analysis, step_progress(2), confirmation_request
        types = [c.get('type') for c in chunks]
        self.assertIn('analysis', types)
        self.assertIn('confirmation_request', types)

    @patch('agent.services._run_analysis')
    def test_resume_workflow_continues_from_pause(self, mock_analysis):
        """Resume picks up from where workflow paused."""
        mock_analysis.return_value = _test_analysis_data()

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
            current_step_order=1,
        )

        # Execute until first pause
        list(self.orchestrator._execute_steps(
            run, {'spreadsheet_data': {'name': 'test', 'sheets': []}}
        ))
        run.refresh_from_db()
        self.assertEqual(run.status, 'awaiting_confirmation')

        # Resume — should run create_tasks (step 3) and complete the workflow
        chunks = list(self.orchestrator._resume_workflow(run))
        run.refresh_from_db()
        types = [c.get('type') for c in chunks]
        self.assertIn('task_created', types)
        self.assertEqual(run.status, 'completed')

    def test_create_decisions_action_commits_from_analysis_for_workflow_run(self):
        """create_decisions action should commit decision tree from stored analysis."""
        from decision.models import Decision

        self.session.approval_required = False
        self.session.save(update_fields=['approval_required'])

        analysis = _test_analysis_data()
        analysis['recommended_decision_tree'] = {
            'nodes': [
                {
                    'ref': 'root',
                    'layer': 0,
                    'title': 'Shift budget to search?',
                    'parent_refs': [],
                },
            ],
        }
        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='awaiting_confirmation',
            current_step_order=7,
            analysis_result=analysis,
            created_decisions=[],
        )

        chunks = list(self.orchestrator.handle_message(
            'create_decisions',
            action='create_decisions',
        ))
        types = [c.get('type') for c in chunks]
        self.assertIn('decision_draft', types)

        run.refresh_from_db()
        self.assertTrue(run.created_decisions)
        self.assertTrue(
            Decision.objects.filter(
                project=self.project,
                title='Shift budget to search?',
                created_by_agent=True,
            ).exists()
        )

    def test_create_tasks_action_commits_from_analysis_for_workflow_run(self):
        """create_tasks action should commit tasks from stored analysis, not pause again."""
        from task.models import Task

        self.session.approval_required = False
        self.session.save(update_fields=['approval_required'])

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='awaiting_confirmation',
            current_step_order=3,
            analysis_result=_test_analysis_data(),
            created_tasks=[],
        )

        chunks = list(self.orchestrator.handle_message(
            'create_tasks',
            action='create_tasks',
        ))
        types = [c.get('type') for c in chunks]
        self.assertIn('task_created', types)

        run.refresh_from_db()
        self.assertTrue(run.created_tasks)
        self.assertEqual(run.status, 'completed')
        self.assertTrue(Task.objects.filter(project=self.project).exists())

    def test_create_tasks_action_uses_completed_step_analysis_when_run_field_empty(self):
        """create_tasks should fall back to the latest completed step output."""
        from task.models import Task

        self.session.approval_required = False
        self.session.save(update_fields=['approval_required'])

        analysis = _test_analysis_data()
        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='awaiting_confirmation',
            current_step_order=3,
            analysis_result=None,
            created_tasks=[],
        )
        AgentStepExecution.objects.create(
            workflow_run=run,
            step=self.workflow.steps.get(order=1),
            step_order=1,
            step_name='Analyze Data',
            status='completed',
            input_data={},
            output_data={'analysis_result': analysis},
        )

        chunks = list(self.orchestrator.handle_message(
            'create_tasks',
            action='create_tasks',
        ))
        types = [c.get('type') for c in chunks]
        self.assertIn('task_created', types)

        run.refresh_from_db()
        self.assertEqual(run.analysis_result, analysis)
        self.assertTrue(run.created_tasks)
        self.assertTrue(Task.objects.filter(project=self.project).exists())

    @patch('agent.services._run_analysis')
    def test_step_execution_records_created(self, mock_analysis):
        """Each executed step creates an AgentStepExecution record."""
        mock_analysis.return_value = _test_analysis_data()

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
            current_step_order=1,
        )

        list(self.orchestrator._execute_steps(
            run, {'spreadsheet_data': {'name': 'test', 'sheets': []}}
        ))

        executions = AgentStepExecution.objects.filter(workflow_run=run)
        # Should have 2 executions: step 1 (analyze) + step 2 (await)
        self.assertEqual(executions.count(), 2)
        self.assertEqual(executions.filter(status='completed').count(), 2)

    @patch('agent.services._run_analysis')
    def test_workflow_failure_handling(self, mock_analysis):
        """Failed step marks execution and run as failed."""
        mock_analysis.side_effect = RuntimeError("API unavailable")

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
            current_step_order=1,
        )

        chunks = list(self.orchestrator._execute_steps(
            run, {'spreadsheet_data': {'name': 'test', 'sheets': []}}
        ))

        run.refresh_from_db()
        self.assertEqual(run.status, 'failed')

        types = [c.get('type') for c in chunks]
        self.assertIn('error', types)

        failed_exec = AgentStepExecution.objects.filter(
            workflow_run=run, status='failed'
        )
        self.assertEqual(failed_exec.count(), 1)

    def test_legacy_backward_compat(self):
        """Runs without workflow_definition use legacy logic."""
        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(orchestrator.handle_message("hello"))
        types = [c['type'] for c in chunks]
        self.assertIn('text', types)
        self.assertIn('done', types)


class ColumnDetectionTests(TestCase):
    """AGENT-13: Column detection and data normalization tests."""

    # ------------------------------------------------------------------ #
    # Rule-based detection                                                #
    # ------------------------------------------------------------------ #

    def test_detect_known_meta_ads_columns(self):
        """Standard Meta Ads headers are matched by DB template or rule without calling LLM."""
        from .column_registry import detect_columns

        headers = ['Campaign name', 'Amount spent', 'Impressions', 'Clicks', 'CTR']
        result = detect_columns(headers)

        # schema_key is either 'meta_ads' (rule) or a UUID string (db_template)
        self.assertIsNotNone(result.schema_key)
        self.assertIn(result.source, ('rule', 'db_template'))
        self.assertGreaterEqual(result.confidence, 0.8)
        self.assertEqual(result.mappings['Campaign name'], 'campaign_name')
        self.assertEqual(result.mappings['Amount spent'], 'amount_spent')
        self.assertEqual(result.mappings['Impressions'], 'impressions')
        self.assertEqual(result.mappings['Clicks'], 'clicks')
        self.assertEqual(result.mappings['CTR'], 'ctr')

    def test_detect_columns_case_insensitive(self):
        """Header matching ignores case differences."""
        from .column_registry import detect_columns

        headers = ['IMPRESSIONS', 'AMOUNT SPENT', 'cpc', 'campaign_name']
        result = detect_columns(headers)

        self.assertIn(result.source, ('rule', 'db_template'))
        self.assertEqual(result.mappings['IMPRESSIONS'], 'impressions')
        self.assertEqual(result.mappings['AMOUNT SPENT'], 'amount_spent')
        self.assertEqual(result.mappings['cpc'], 'cpc')

    def test_detect_columns_alias_variants(self):
        """Alias variants map to the same canonical name."""
        from .column_registry import detect_columns

        # 'spend' is an alias for 'amount_spent'
        headers = ['Campaign name', 'spend', 'Reach', 'Purchases']
        result = detect_columns(headers)

        self.assertEqual(result.mappings['spend'], 'amount_spent')
        self.assertEqual(result.mappings['Reach'], 'reach')
        self.assertEqual(result.mappings['Purchases'], 'purchases')

    def test_detect_columns_unrecognized_labeled_unknown(self):
        """Headers that do not match any alias are labeled 'unknown'."""
        from .column_registry import detect_columns

        headers = ['Campaign name', 'Amount spent', 'My Custom Metric']
        result = detect_columns(headers)

        self.assertEqual(result.mappings['My Custom Metric'], 'unknown')
        self.assertIn('My Custom Metric', result.unrecognized)

    def test_detect_columns_empty_headers(self):
        """Empty header list returns an unknown result without error."""
        from .column_registry import detect_columns

        result = detect_columns([])

        self.assertIsNone(result.schema_key)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.mappings, {})

    def test_detect_columns_below_threshold_falls_through(self):
        """Headers that match < 50% of a known schema fall through to LLM path."""
        from .column_registry import detect_columns, _try_rule_match

        # Only 1 out of 5 headers matches — confidence 0.2 < threshold 0.5
        headers = ['Campaign name', 'Revenue', 'Sessions', 'Bounce Rate', 'Goal Completions']
        result = _try_rule_match(headers)
        self.assertIsNone(result)

    def test_to_dict_serializable(self):
        """ColumnDetectionResult.to_dict() returns a plain dict."""
        from .column_registry import detect_columns

        headers = ['Campaign name', 'Amount spent']
        result = detect_columns(headers)
        d = result.to_dict()

        self.assertIsInstance(d, dict)
        self.assertIn('schema_key', d)
        self.assertIn('mappings', d)
        self.assertIn('confidence', d)
        self.assertIn('source', d)
        self.assertIn('unrecognized', d)

    # ------------------------------------------------------------------ #
    # Data normalization                                                  #
    # ------------------------------------------------------------------ #

    def test_normalize_spreadsheet_renames_columns(self):
        """normalize_spreadsheet renames columns and row keys according to mapping."""
        from .column_registry import normalize_spreadsheet

        data = {
            "name": "Test Report",
            "sheets": [{
                "name": "Sheet1",
                "columns": ["Campaign name", "Amount spent", "Impressions"],
                "rows": [
                    {"Campaign name": "Summer Sale", "Amount spent": 500.0, "Impressions": 10000},
                ],
            }],
        }
        mapping = {
            "Campaign name": "campaign_name",
            "Amount spent": "amount_spent",
            "Impressions": "impressions",
        }

        normalized = normalize_spreadsheet(data, mapping)

        sheet = normalized["sheets"][0]
        self.assertEqual(sheet["columns"], ["campaign_name", "amount_spent", "impressions"])
        row = sheet["rows"][0]
        self.assertIn("campaign_name", row)
        self.assertIn("amount_spent", row)
        self.assertEqual(row["campaign_name"], "Summer Sale")
        self.assertEqual(row["amount_spent"], 500.0)

    def test_normalize_spreadsheet_unknown_columns_keep_original_name(self):
        """Columns mapped to 'unknown' retain their original header name."""
        from .column_registry import normalize_spreadsheet

        data = {
            "name": "Test",
            "sheets": [{
                "name": "Sheet1",
                "columns": ["Campaign name", "My Custom Metric"],
                "rows": [{"Campaign name": "X", "My Custom Metric": 42}],
            }],
        }
        mapping = {"Campaign name": "campaign_name", "My Custom Metric": "unknown"}

        normalized = normalize_spreadsheet(data, mapping)
        sheet = normalized["sheets"][0]
        self.assertIn("campaign_name", sheet["columns"])
        self.assertIn("My Custom Metric", sheet["columns"])
        row = sheet["rows"][0]
        self.assertIn("My Custom Metric", row)

    def test_normalize_spreadsheet_empty_mapping_passthrough(self):
        """normalize_spreadsheet with no mapping returns data unchanged."""
        from .column_registry import normalize_spreadsheet

        data = {"name": "X", "sheets": [{"name": "S", "columns": ["A"], "rows": [{"A": 1}]}]}
        result = normalize_spreadsheet(data, {})
        self.assertEqual(result, data)

    # ------------------------------------------------------------------ #
    # Executor integration                                                #
    # ------------------------------------------------------------------ #

    def setUp(self):
        self.org = Organization.objects.create(name='Test Org CD', slug='test-org-cd')
        self.user = CustomUser.objects.create_user(
            email='cd@test.com',
            username='cduser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Test Project CD',
            organization=self.org,
            owner=self.user,
        )
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        self.workflow = AgentWorkflowDefinition.objects.create(
            name='Column Detection Workflow',
            is_default=False,
            is_system=False,
            status='active',
        )
        AgentWorkflowStep.objects.create(
            workflow=self.workflow,
            name='Detect Columns',
            step_type='detect_columns',
            order=1,
        )
        AgentWorkflowStep.objects.create(
            workflow=self.workflow,
            name='Await Column Confirmation',
            step_type='await_confirmation',
            order=2,
            config={'message': 'Please confirm or correct the detected columns.'},
        )
        AgentWorkflowStep.objects.create(
            workflow=self.workflow,
            name='Normalize Data',
            step_type='normalize_data',
            order=3,
        )
        self.orchestrator = AgentOrchestrator(self.user, self.project, self.session)

    def test_detect_columns_executor_emits_column_mapping_event(self):
        """DetectColumnsExecutor emits a column_mapping SSE event with detection data."""
        from .executors import DetectColumnsExecutor

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
        )
        step = self.workflow.steps.get(order=1)
        executor = DetectColumnsExecutor(step, run, self.orchestrator)

        input_data = {
            'spreadsheet_data': {
                'name': 'Meta Report',
                'sheets': [{
                    'name': 'Sheet1',
                    'columns': ['Campaign name', 'Amount spent', 'Impressions'],
                    'rows': [{'Campaign name': 'Test', 'Amount spent': 100, 'Impressions': 500}],
                }],
            }
        }
        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(len(result.sse_events), 1)
        self.assertEqual(result.sse_events[0]['type'], 'column_mapping')
        self.assertIn('column_mapping', result.output_data)
        self.assertIn('column_detection', result.output_data)

    def test_normalize_data_executor_renames_columns(self):
        """NormalizeDataExecutor renames columns using column_mapping from input."""
        from .executors import NormalizeDataExecutor

        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='analyzing',
        )
        step = self.workflow.steps.get(order=3)
        executor = NormalizeDataExecutor(step, run, self.orchestrator)

        input_data = {
            'spreadsheet_data': {
                'name': 'Report',
                'sheets': [{
                    'name': 'Sheet1',
                    'columns': ['Campaign name', 'Amount spent'],
                    'rows': [{'Campaign name': 'X', 'Amount spent': 100}],
                }],
            },
            'column_mapping': {
                'Campaign name': 'campaign_name',
                'Amount spent': 'amount_spent',
            },
        }
        result = executor.execute(input_data)

        self.assertTrue(result.success)
        sheet = result.output_data['spreadsheet_data']['sheets'][0]
        self.assertIn('campaign_name', sheet['columns'])
        self.assertIn('amount_spent', sheet['columns'])

    def test_confirm_columns_action_resumes_workflow_with_mapping(self):
        """confirm_columns action resumes the paused workflow with the user's mapping."""
        run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=self.workflow,
            status='awaiting_confirmation',
            current_step_order=3,
        )
        # Simulate the last completed step (detect_columns at order=1)
        step1 = self.workflow.steps.get(order=1)
        AgentStepExecution.objects.create(
            workflow_run=run,
            step=step1,
            step_order=1,
            step_name='Detect Columns',
            status='completed',
            output_data={
                'spreadsheet_data': {
                    'name': 'Report',
                    'sheets': [{
                        'name': 'Sheet1',
                        'columns': ['Campaign name', 'Amount spent'],
                        'rows': [{'Campaign name': 'Y', 'Amount spent': 200}],
                    }],
                },
                'column_mapping': {'Campaign name': 'campaign_name', 'Amount spent': 'amount_spent'},
            },
        )

        user_mapping = {'Campaign name': 'campaign_name', 'Amount spent': 'spend_custom'}
        chunks = list(self.orchestrator.handle_message(
            '',
            action='confirm_columns',
            column_mapping=user_mapping,
        ))

        types = [c.get('type') for c in chunks]
        self.assertIn('done', types)
        # The normalize step should have emitted a text event
        self.assertIn('text', types)


class AnalysisPreprocessTests(TestCase):
    """Ensure analysis receives row data after column normalization."""

    def test_resolve_analysis_columns_maps_display_names_to_canonical(self):
        sheet_columns = ['campaign_name', 'amount_spent', 'roas']
        key_cols = ['Campaign Name', 'Amount Spent (USD)', 'ROAS']
        mapping = {
            'Campaign Name': 'campaign_name',
            'Amount Spent (USD)': 'amount_spent',
            'ROAS': 'roas',
        }
        resolved = _resolve_analysis_columns(key_cols, sheet_columns, mapping)
        self.assertEqual(resolved, ['campaign_name', 'amount_spent', 'roas'])

    def test_preprocess_includes_rows_when_criteria_uses_display_headers(self):
        spreadsheet_data = {
            'name': 'test.csv',
            'sheets': [{
                'name': 'Sheet1',
                'columns': ['amount_spent', 'roas'],
                'rows': [
                    {'amount_spent': 100, 'roas': 0.3},
                    {'amount_spent': 200, 'roas': 5.0},
                ],
            }],
        }
        success_criteria = {
            'schema_type': 'Meta Ads Performance',
            'key_columns': ['Amount Spent (USD)', 'ROAS'],
            'criteria': [],
        }
        column_mapping = {
            'Amount Spent (USD)': 'amount_spent',
            'ROAS': 'roas',
        }

        _summary, cleaned_data, _criteria_text = _preprocess_spreadsheet(
            spreadsheet_data,
            success_criteria,
            column_mapping=column_mapping,
        )

        parsed = json.loads(cleaned_data)
        self.assertEqual(len(parsed), 2)
        self.assertIn('amount_spent', parsed[0])
        self.assertIn('roas', parsed[0])


class AgentWorkflowStepAPITest(APITestCase):
    """POST /workflows/{id}/steps/ without order should auto-assign."""

    def setUp(self):
        from core.models import Organization, Project, ProjectMember

        self.client = APIClient()
        self.org = Organization.objects.create(name='Step Test Org')
        self.user = CustomUser.objects.create_user(
            email='stepapi@test.com',
            username='stepapiuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Step Test Project',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            role='owner',
            is_active=True,
        )
        self.workflow = AgentWorkflowDefinition.objects.create(
            name='API Test Workflow',
            project=self.project,
            is_system=False,
            status='draft',
            created_by=self.user,
        )
        self.client.force_authenticate(user=self.user)

    def test_create_step_without_order_auto_assigns(self):
        url = f'/api/agent/workflows/{self.workflow.id}/steps/'
        response = self.client.post(
            url,
            {'name': 'Analyze Data', 'step_type': 'analyze_data'},
            format='json',
            QUERY_STRING=f'project_id={self.project.id}',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['order'], 1)
        self.assertEqual(response.data['step_type'], 'analyze_data')


class ChatInputSerializerUserContextTests(TestCase):
    def test_user_context_absent_is_valid(self):
        from .serializers import ChatInputSerializer
        s = ChatInputSerializer(data={'message': 'hello'})
        self.assertTrue(s.is_valid(), s.errors)

    def test_user_context_blank_is_valid(self):
        from .serializers import ChatInputSerializer
        s = ChatInputSerializer(data={'message': 'hello', 'user_context': ''})
        self.assertTrue(s.is_valid(), s.errors)

    def test_user_context_null_is_valid(self):
        from .serializers import ChatInputSerializer
        s = ChatInputSerializer(data={'message': 'hello', 'user_context': None})
        self.assertTrue(s.is_valid(), s.errors)

    def test_user_context_valid_string(self):
        from .serializers import ChatInputSerializer
        s = ChatInputSerializer(data={'message': 'hello', 'user_context': 'Focus on ROAS'})
        self.assertTrue(s.is_valid(), s.errors)
        self.assertEqual(s.validated_data['user_context'], 'Focus on ROAS')

    def test_user_context_exceeds_500_chars_is_invalid(self):
        from .serializers import ChatInputSerializer
        s = ChatInputSerializer(data={'message': 'hello', 'user_context': 'x' * 501})
        self.assertFalse(s.is_valid())
        self.assertIn('user_context', s.errors)

    def test_user_context_exactly_500_chars_is_valid(self):
        from .serializers import ChatInputSerializer
        s = ChatInputSerializer(data={'message': 'hello', 'user_context': 'x' * 500})
        self.assertTrue(s.is_valid(), s.errors)


class ChatViewUserContextNormalizationTests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Ctx Org', slug='ctx-org')
        self.user = CustomUser.objects.create_user(
            email='ctx@test.com', username='ctxuser', password='pass'
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Ctx Project', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(
            user=self.user, project=self.project,
        )
        self.client.force_authenticate(user=self.user)

    def _post_chat(self, user_context_value, **extra):
        payload = {'message': 'hi', **extra}
        if user_context_value is not None:
            payload['user_context'] = user_context_value
        return payload

    @patch('agent.views.AgentOrchestrator')
    def test_empty_string_normalised_to_none(self, MockOrch):
        mock_instance = MagicMock()
        mock_instance.handle_message.return_value = iter([{'type': 'done'}])
        MockOrch.return_value = mock_instance

        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            self._post_chat(''),
            format='json',
        )
        b''.join(response.streaming_content)
        _, kwargs = mock_instance.handle_message.call_args
        self.assertIsNone(kwargs.get('user_context'))

    @patch('agent.views.AgentOrchestrator')
    def test_none_stays_none(self, MockOrch):
        mock_instance = MagicMock()
        mock_instance.handle_message.return_value = iter([{'type': 'done'}])
        MockOrch.return_value = mock_instance

        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            self._post_chat(None),
            format='json',
        )
        b''.join(response.streaming_content)
        _, kwargs = mock_instance.handle_message.call_args
        self.assertIsNone(kwargs.get('user_context'))

    @patch('agent.views.AgentOrchestrator')
    def test_valid_context_passed_through(self, MockOrch):
        mock_instance = MagicMock()
        mock_instance.handle_message.return_value = iter([{'type': 'done'}])
        MockOrch.return_value = mock_instance

        response = self.client.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            self._post_chat('Focus on ROAS'),
            format='json',
        )
        b''.join(response.streaming_content)
        _, kwargs = mock_instance.handle_message.call_args
        self.assertEqual(kwargs.get('user_context'), 'Focus on ROAS')


class OrchestratorUserContextThreadingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Orch Org', slug='orch-org')
        self.user = CustomUser.objects.create_user(
            email='orch@test.com', username='orchuser', password='pass',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Orch Project', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(
            user=self.user, project=self.project,
        )

    @patch('agent.services.cache')
    @patch('agent.services._run_analysis')
    @patch('agent.services.file_parser.parse_file_to_json')
    @patch('agent.services.data_service._get_csv_dir')
    @patch('os.path.isfile')
    def test_start_workflow_stores_user_context_on_run(
        self, mock_isfile, mock_csv_dir, mock_parse, mock_analysis, mock_cache
    ):
        from agent.models import ImportedCSVFile, AgentWorkflowDefinition, AgentWorkflowStep, AgentWorkflowRun
        from agent.services import AgentOrchestrator

        mock_isfile.return_value = True
        mock_csv_dir.return_value = '/tmp'
        mock_parse.return_value = {'name': 'test.csv', 'sheets': []}
        mock_analysis.return_value = {'anomalies': [], 'recommended_tasks': []}

        csv_file = ImportedCSVFile.objects.create(
            filename='test.csv',
            original_filename='test.csv',
            user=self.user,
            project=self.project,
        )
        wf = AgentWorkflowDefinition.objects.create(
            name='Test WF', is_default=True, is_system=True, status='active',
        )
        AgentWorkflowStep.objects.create(
            workflow=wf, name='Analyze', step_type='analyze_data', order=1,
        )

        orch = AgentOrchestrator(user=self.user, project=self.project, session=self.session)
        list(orch.handle_message(
            '', file_id=str(csv_file.id), user_context='Focus on ROAS efficiency',
        ))

        run = AgentWorkflowRun.objects.filter(session=self.session).latest('created_at')
        mock_cache.set.assert_called_once_with(
            f"agent:context:{run.id}", 'Focus on ROAS efficiency', 3600
        )

    @patch('agent.services.cache')
    @patch('agent.services._run_analysis')
    @patch('agent.services.file_parser.parse_file_to_json')
    @patch('agent.services.data_service._get_csv_dir')
    @patch('os.path.isfile')
    def test_start_workflow_stores_empty_string_when_context_none(
        self, mock_isfile, mock_csv_dir, mock_parse, mock_analysis, mock_cache
    ):
        from agent.models import ImportedCSVFile, AgentWorkflowDefinition, AgentWorkflowStep
        from agent.services import AgentOrchestrator

        mock_isfile.return_value = True
        mock_csv_dir.return_value = '/tmp'
        mock_parse.return_value = {'name': 'test.csv', 'sheets': []}
        mock_analysis.return_value = {'anomalies': [], 'recommended_tasks': []}

        csv_file = ImportedCSVFile.objects.create(
            filename='test2.csv',
            original_filename='test2.csv',
            user=self.user,
            project=self.project,
        )
        wf = AgentWorkflowDefinition.objects.create(
            name='Test WF2', is_default=True, is_system=True, status='active',
        )
        AgentWorkflowStep.objects.create(
            workflow=wf, name='Analyze', step_type='analyze_data', order=1,
        )

        orch = AgentOrchestrator(user=self.user, project=self.project, session=self.session)
        list(orch.handle_message('', file_id=str(csv_file.id), user_context=None))

        mock_cache.set.assert_not_called()


class GeminiAnalysisPromptInjectionTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        from stripe_meta.models import Plan, Subscription

        Plan.objects.all().delete()

        self.org = Organization.objects.create(name='PromptInjectionOrg', slug='prompt-injection-org')
        self.user = CustomUser.objects.create_user(
            email='injection@test.com', username='injectionuser', password='pass',
        )
        self.user.organization = self.org
        self.user.save()
        self.plan = Plan.objects.create(
            name='InjectionTestPlan',
            monthly_token_quota=10_000_000,
            max_tokens_per_call=None,
            base_price_cents=0,
        )
        Subscription.objects.create(
            organization=self.org,
            plan=self.plan,
            stripe_subscription_id=f'sub_injection_{self.org.id}',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=365 * 10),
            is_active=True,
            is_internal=True,
        )
        self.project = Project.objects.create(
            name='InjectionProject', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)

    @patch('agent.llm_client._call_gemini')
    def test_no_context_prompt_unchanged(self, mock_gemini):
        from agent.services import _call_gemini_analysis
        mock_gemini.return_value = {'text': json.dumps({'anomalies': [], 'recommended_tasks': []}), 'usage': {'input': 10, 'output': 20}}

        _call_gemini_analysis(
            {'name': 'test', 'sheets': []},
            user_id='1',
            user_context=None,
            agent_session=self.session,
        )

        call_args = mock_gemini.call_args[0]
        system_prompt = call_args[1]
        self.assertNotIn('User Context', system_prompt)

    @patch('agent.llm_client._call_gemini')
    def test_context_appended_to_system_prompt(self, mock_gemini):
        from agent.services import _call_gemini_analysis
        mock_gemini.return_value = {'text': json.dumps({'anomalies': [], 'recommended_tasks': []}), 'usage': {'input': 10, 'output': 20}}

        _call_gemini_analysis(
            {'name': 'test', 'sheets': []},
            user_id='1',
            user_context='Focus on ROAS for EU region',
            agent_session=self.session,
        )

        call_args = mock_gemini.call_args[0]
        system_prompt = call_args[1]
        self.assertIn('User Context', system_prompt)
        self.assertIn('Focus on ROAS for EU region', system_prompt)
        self.assertIn('defer to their stated goals', system_prompt)
        self.assertIn('Still surface critical anomalies', system_prompt)

    @patch('agent.llm_client._call_gemini')
    def test_empty_context_prompt_unchanged(self, mock_gemini):
        from agent.services import _call_gemini_analysis
        mock_gemini.return_value = {'text': json.dumps({'anomalies': [], 'recommended_tasks': []}), 'usage': {'input': 10, 'output': 20}}

        _call_gemini_analysis(
            {'name': 'test', 'sheets': []},
            user_id='1',
            user_context='',
            agent_session=self.session,
        )

        call_args = mock_gemini.call_args[0]
        system_prompt = call_args[1]
        self.assertNotIn('User Context', system_prompt)

    @patch('core.services.gemini_client.call_gemini_json')
    def test_decision_tree_in_system_prompt_when_requested(self, mock_gemini):
        from agent.services import _call_gemini_analysis

        mock_gemini.return_value = {
            'recommended_decision_tree': {'nodes': []},
        }

        _call_gemini_analysis(
            {'name': 'test', 'sheets': []},
            user_id='1',
            generation_outputs=['recommended_decision_tree'],
        )

        system_prompt = mock_gemini.call_args[1]['system_prompt']
        self.assertIn('recommended_decision_tree', system_prompt)
        self.assertIn('parent_refs', system_prompt)


class RunAnalysisValidationRetryTests(TestCase):
    @patch('core.services.gemini_client._get_api_key', return_value='fake-key')
    @patch('agent.services._call_gemini_analysis')
    def test_retries_on_validation_error_then_succeeds(self, mock_call, _mock_key):
        from agent.services import _run_analysis

        invalid = {
            'recommended_decision_tree': {
                'nodes': [{'ref': 'a', 'layer': 0, 'title': 'Root'}],
            },
        }
        valid = {
            'recommended_decision_tree': {
                'nodes': [{'ref': 'a', 'layer': 0, 'title': 'Root', 'parent_refs': []}],
            },
        }
        mock_call.side_effect = [invalid, valid]

        result = _run_analysis(
            {'name': 'test', 'sheets': []},
            generation_outputs=['recommended_decision_tree'],
        )

        self.assertEqual(mock_call.call_count, 2)
        self.assertIsNone(mock_call.call_args_list[0].kwargs.get('validation_feedback'))
        self.assertIn(
            'parent_refs',
            mock_call.call_args_list[1].kwargs['validation_feedback'],
        )
        self.assertEqual(result['recommended_decision_tree']['nodes'][0]['parent_refs'], [])

    @patch('core.services.gemini_client._get_api_key', return_value='fake-key')
    @patch('agent.services._call_gemini_analysis')
    def test_raises_after_max_validation_retries(self, mock_call, _mock_key):
        from agent.generation_registry import GenerationValidationError
        from agent.services import _ANALYSIS_VALIDATION_MAX_ATTEMPTS, _run_analysis

        invalid = {
            'recommended_decision_tree': {
                'nodes': [{'ref': 'a', 'layer': 0, 'title': 'Root'}],
            },
        }
        mock_call.return_value = invalid

        with self.assertRaises(GenerationValidationError):
            _run_analysis(
                {'name': 'test', 'sheets': []},
                generation_outputs=['recommended_decision_tree'],
            )

        self.assertEqual(mock_call.call_count, _ANALYSIS_VALIDATION_MAX_ATTEMPTS)

    @patch('core.services.gemini_client._get_api_key', return_value='fake-key')
    @patch('agent.services._call_gemini_analysis')
    def test_retries_on_recommended_tasks_validation_error_then_succeeds(self, mock_call, _mock_key):
        from agent.services import _run_analysis

        invalid = {
            'recommended_tasks': [
                {'type': 'bogus_type', 'summary': 'Bad', 'priority': 'HIGH'},
            ],
        }
        valid = {
            'recommended_tasks': [
                {'type': 'alert', 'summary': 'Fix issue', 'priority': 'HIGH'},
            ],
        }
        mock_call.side_effect = [invalid, valid]

        result = _run_analysis(
            {'name': 'test', 'sheets': []},
            generation_outputs=['recommended_tasks'],
        )

        self.assertEqual(mock_call.call_count, 2)
        self.assertIn(
            'type is invalid',
            mock_call.call_args_list[1].kwargs['validation_feedback'],
        )
        self.assertEqual(result['recommended_tasks'][0]['type'], 'alert')


class SpreadsheetInsightsValidationRetryTests(TestCase):
    @patch('core.services.gemini_client._get_api_key', return_value='fake-key')
    @patch('agent.services._call_gemini_spreadsheet_insights')
    def test_retries_on_recommended_tasks_validation_then_succeeds(self, mock_call, _mock_key):
        from agent.services import _run_spreadsheet_insights

        invalid = {
            'summary': 'Overview',
            'recommendations': [],
            'anomalies': [],
            'recommended_tasks': [
                {'type': 'invalid', 'summary': 'Bad', 'priority': 'HIGH'},
            ],
        }
        valid = {
            'summary': 'Overview',
            'recommendations': [],
            'anomalies': [],
            'recommended_tasks': [
                {'type': 'Alert', 'summary': 'Fix issue', 'priority': 'High'},
            ],
        }
        mock_call.side_effect = [invalid, valid]

        result = _run_spreadsheet_insights({'name': 'test', 'sheets': []})

        self.assertEqual(mock_call.call_count, 2)
        self.assertIsNone(mock_call.call_args_list[0].kwargs.get('validation_feedback'))
        self.assertIn(
            'type is invalid',
            mock_call.call_args_list[1].kwargs['validation_feedback'],
        )
        self.assertEqual(result['recommended_tasks'][0]['type'], 'alert')
        self.assertEqual(result['recommended_tasks'][0]['priority'], 'HIGH')

    @patch('core.services.gemini_client._get_api_key', return_value='fake-key')
    @patch('agent.services._call_gemini_spreadsheet_insights')
    def test_raises_after_max_insights_validation_retries(self, mock_call, _mock_key):
        from agent.generation_registry import GenerationValidationError
        from agent.services import _ANALYSIS_VALIDATION_MAX_ATTEMPTS, _run_spreadsheet_insights

        invalid = {
            'summary': 'Overview',
            'recommendations': [],
            'anomalies': [],
            'recommended_tasks': [
                {'type': 'invalid', 'summary': 'Bad', 'priority': 'HIGH'},
            ],
        }
        mock_call.return_value = invalid

        with self.assertRaises(GenerationValidationError):
            _run_spreadsheet_insights({'name': 'test', 'sheets': []})

        self.assertEqual(mock_call.call_count, _ANALYSIS_VALIDATION_MAX_ATTEMPTS)

    @patch('core.services.gemini_client._get_api_key', return_value='fake-key')
    @patch('agent.services._call_gemini_spreadsheet_insights')
    def test_retries_on_out_of_bounds_anomaly_location_then_succeeds(self, mock_call, _mock_key):
        from agent.services import _run_spreadsheet_insights

        spreadsheet_data = {
            'name': 'test',
            'sheets': [
                {'id': 1, 'name': 'S', 'columns': ['A', 'B'], 'rows': [{'A': 1}, {'A': 2}]},
            ],
        }
        out_of_bounds = {
            'summary': 'Overview',
            'recommendations': [],
            'anomalies': [
                {'title': 'Ghost cell', 'locations': [{'row': 9, 'col': 0}]},
            ],
            'recommended_tasks': [],
        }
        clean = {
            'summary': 'Overview',
            'recommendations': [],
            'anomalies': [
                {'title': 'Real cell', 'locations': [{'row': 1, 'col': 0}]},
            ],
            'recommended_tasks': [],
        }
        mock_call.side_effect = [out_of_bounds, clean]

        result = _run_spreadsheet_insights(spreadsheet_data, sheet_id=1)

        self.assertEqual(mock_call.call_count, 2)
        feedback = mock_call.call_args_list[1].kwargs['validation_feedback']
        self.assertIn('outside the analysed data', feedback)
        self.assertEqual(result['anomalies'][0]['locations'][0]['row'], 1)


class AnalyzeDataExecutorUserContextTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Exec Org', slug='exec-org')
        self.user = CustomUser.objects.create_user(
            email='exec@test.com', username='execuser', password='pass',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Exec Project', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(
            user=self.user, project=self.project,
        )

    @patch('agent.executors.cache')
    @patch('agent.services._run_analysis')
    def test_executor_passes_user_context_to_run_analysis(self, mock_analysis, mock_cache):
        from agent.executors import AnalyzeDataExecutor
        from agent.models import AgentWorkflowDefinition, AgentWorkflowStep, AgentWorkflowRun

        mock_analysis.return_value = {'anomalies': [], 'recommended_tasks': []}
        mock_cache.get.return_value = 'Prioritize high-spend campaigns'

        wf = AgentWorkflowDefinition.objects.create(name='WF', status='active')
        step = AgentWorkflowStep.objects.create(
            workflow=wf, name='Analyze', step_type='analyze_data', order=1,
        )
        run = AgentWorkflowRun.objects.create(
            session=self.session, workflow_definition=wf,
        )

        class FakeOrch:
            user = self.user
            session = None

        executor = AnalyzeDataExecutor(step=step, workflow_run=run, orchestrator=FakeOrch())
        executor.execute({'spreadsheet_data': {'name': 'test', 'sheets': [{'columns': [], 'rows': []}]}})

        mock_cache.get.assert_called_once_with(f"agent:context:{run.id}")
        _, kwargs = mock_analysis.call_args
        self.assertEqual(kwargs.get('user_context'), 'Prioritize high-spend campaigns')

    @patch('agent.executors.cache')
    @patch('agent.services._run_analysis')
    def test_executor_passes_none_when_context_empty(self, mock_analysis, mock_cache):
        from agent.executors import AnalyzeDataExecutor
        from agent.models import AgentWorkflowDefinition, AgentWorkflowStep, AgentWorkflowRun

        mock_analysis.return_value = {'anomalies': [], 'recommended_tasks': []}
        mock_cache.get.return_value = None

        wf = AgentWorkflowDefinition.objects.create(name='WF3', status='active')
        step = AgentWorkflowStep.objects.create(
            workflow=wf, name='Analyze', step_type='analyze_data', order=1,
        )
        run = AgentWorkflowRun.objects.create(
            session=self.session, workflow_definition=wf,
        )

        class FakeOrch:
            user = self.user
            session = None

        executor = AnalyzeDataExecutor(step=step, workflow_run=run, orchestrator=FakeOrch())
        executor.execute({'spreadsheet_data': {'name': 'test', 'sheets': []}})

        _, kwargs = mock_analysis.call_args
        self.assertIsNone(kwargs.get('user_context'))


class SpreadsheetInsightsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Insights Org', slug='insights-org')
        self.user = CustomUser.objects.create_user(
            email='insights@test.com',
            username='insightsuser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Insights Project',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            role='owner',
            is_active=True,
        )
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )

    def _create_spreadsheet_with_sheets(self):
        from spreadsheet.models import Spreadsheet, Sheet

        spreadsheet = Spreadsheet.objects.create(project=self.project, name='Budget')
        grant_ai_consent(self.user, spreadsheet)
        sheet1 = Sheet.objects.create(spreadsheet=spreadsheet, name='Sheet1', position=0)
        sheet2 = Sheet.objects.create(spreadsheet=spreadsheet, name='Sheet2', position=1)
        return spreadsheet, sheet1, sheet2

    def test_provider_payload_filters_by_sheet_id(self):
        from spreadsheet.providers import SpreadsheetDataProvider

        spreadsheet, sheet1, sheet2 = self._create_spreadsheet_with_sheets()
        provider = SpreadsheetDataProvider(self.user)

        data = provider.get_analysis_payload(spreadsheet.id, sheet_id=sheet1.id)
        self.assertEqual(len(data['sheets']), 1)
        self.assertEqual(data['sheets'][0]['id'], sheet1.id)
        self.assertEqual(data['sheets'][0]['name'], 'Sheet1')

        all_data = provider.get_analysis_payload(spreadsheet.id)
        self.assertEqual(len(all_data['sheets']), 2)

    def test_normalize_spreadsheet_insights_result(self):
        from agent.services import _normalize_spreadsheet_insights_result

        raw = {
            'summary': 'Two columns of sales data.',
            'recommendations': ['Review outliers'],
            'anomalies': [
                {
                    'title': 'Negative revenue',
                    'severity': 'critical',
                    'description': 'Row 3 has negative revenue.',
                    'locations': [{'row': 2, 'col': 1, 'a1': 'B3'}],
                }
            ],
            'recommended_tasks': [
                {
                    'type': 'report',
                    'summary': 'Audit revenue row',
                    'description': 'Investigate negative values.',
                    'priority': 'HIGH',
                }
            ],
        }
        result = _normalize_spreadsheet_insights_result(raw, sheet_id=42)
        self.assertEqual(result['summary'], 'Two columns of sales data.')
        self.assertTrue(result['anomalies_confirmed'])
        self.assertEqual(len(result['anomalies']), 1)
        self.assertEqual(result['anomalies'][0]['id'], 'anom_0')
        self.assertEqual(result['anomalies'][0]['locations'][0]['sheet_id'], 42)
        self.assertEqual(len(result['recommended_tasks']), 1)

    def test_normalize_rejects_blank_or_non_string_summary(self):
        from agent.services import _normalize_spreadsheet_insights_result

        for bad_summary in ('', '   \n\t', None):
            with self.assertRaises(ValueError):
                _normalize_spreadsheet_insights_result({'summary': bad_summary})
        with self.assertRaises(ValueError):
            _normalize_spreadsheet_insights_result({'summary': {'text': 'x'}})
        with self.assertRaises(ValueError):
            _normalize_spreadsheet_insights_result('not a dict')

    def test_normalize_rejects_non_integer_or_negative_location(self):
        from agent.services import _normalize_spreadsheet_insights_result

        base = {'summary': 'ok', 'anomalies': [{'title': 'A', 'locations': []}]}

        base['anomalies'][0]['locations'] = [{'row': 'x', 'col': 1}]
        with self.assertRaises(ValueError):
            _normalize_spreadsheet_insights_result(base)

        base['anomalies'][0]['locations'] = [{'row': -1, 'col': 0}]
        with self.assertRaises(ValueError):
            _normalize_spreadsheet_insights_result(base)

    def test_normalize_rejects_out_of_bounds_location(self):
        from agent.services import _normalize_spreadsheet_insights_result

        raw = {
            'summary': 'ok',
            'anomalies': [{'title': 'A', 'locations': [{'row': 2, 'col': 0}]}],
        }
        # Row 2 is fine within a 5-row / 3-col sample...
        ok = _normalize_spreadsheet_insights_result(raw, row_count=5, col_count=3)
        self.assertEqual(len(ok['anomalies'][0]['locations']), 1)

        # ...but not when the sample only had 2 rows.
        with self.assertRaises(ValueError):
            _normalize_spreadsheet_insights_result(raw, row_count=2, col_count=3)

        raw['anomalies'][0]['locations'] = [{'row': 0, 'col': 9}]
        with self.assertRaises(ValueError):
            _normalize_spreadsheet_insights_result(raw, row_count=5, col_count=3)

    def test_normalize_skips_bounds_check_when_dimensions_unknown(self):
        from agent.services import _normalize_spreadsheet_insights_result

        raw = {
            'summary': 'ok',
            'anomalies': [{'title': 'A', 'locations': [{'row': 999, 'col': 999}]}],
        }
        result = _normalize_spreadsheet_insights_result(raw)
        self.assertEqual(result['anomalies'][0]['locations'][0]['row'], 999)

    def test_spreadsheet_insights_sample_bounds(self):
        from agent.services import _spreadsheet_insights_sample_bounds

        data = {
            'sheets': [
                {'columns': ['A', 'B', 'C'], 'rows': [{'A': 1}, {'A': 2}]},
                {'columns': ['C', 'D'], 'rows': [{'D': 3}]},
            ]
        }
        rows, cols = _spreadsheet_insights_sample_bounds(data)
        self.assertEqual(rows, 3)
        self.assertEqual(cols, 4)  # A, B, C, D deduped
        self.assertEqual(_spreadsheet_insights_sample_bounds({'sheets': []}), (0, 0))

    @patch('agent.services._run_spreadsheet_insights')
    def test_analyze_spreadsheet_insights_yields_summary_and_anomalies(self, mock_insights):
        spreadsheet, sheet1, _sheet2 = self._create_spreadsheet_with_sheets()
        mock_insights.return_value = {
            'summary': '## Overview\nSales look stable.',
            'recommendations': ['Monitor Q2'],
            'anomalies': [
                {
                    'id': 'anom_0',
                    'title': 'Spike',
                    'severity': 'warning',
                    'description': 'Unusual value in B3',
                    'locations': [{'row': 2, 'col': 1, 'a1': 'B3', 'sheet_id': sheet1.id}],
                    'metric': 'Spike',
                    'movement': 'UNEXPECTED_SPIKE',
                    'current_value': '',
                    'previous_value': '',
                    'change_percent': 0,
                }
            ],
            'recommended_tasks': [
                {
                    'type': 'report',
                    'summary': 'Review spike',
                    'description': 'Check the flagged cell.',
                    'priority': 'MEDIUM',
                }
            ],
            'anomalies_confirmed': True,
        }

        orchestrator = AgentOrchestrator(self.user, self.project, self.session)
        chunks = list(
            orchestrator.handle_message(
                'Analyze sheet',
                spreadsheet_id=spreadsheet.id,
                sheet_id=sheet1.id,
                action='analyze_spreadsheet_insights',
            )
        )
        types = [chunk['type'] for chunk in chunks]
        self.assertIn('spreadsheet_summary', types)
        self.assertIn('spreadsheet_anomalies', types)
        self.assertIn('done', types)

        summary_chunk = next(c for c in chunks if c['type'] == 'spreadsheet_summary')
        self.assertIn('Overview', summary_chunk['content'])
        self.assertIn('Monitor Q2', summary_chunk['content'])

        anomalies_chunk = next(c for c in chunks if c['type'] == 'spreadsheet_anomalies')
        self.assertEqual(anomalies_chunk['data']['spreadsheet_id'], spreadsheet.id)
        self.assertEqual(anomalies_chunk['data']['sheet_id'], sheet1.id)
        self.assertTrue(anomalies_chunk['data']['anomalies_confirmed'])

        workflow_run = AgentWorkflowRun.objects.filter(session=self.session).latest('created_at')
        self.assertIsNotNone(workflow_run.analysis_result)
        self.assertEqual(len(workflow_run.analysis_result.get('recommended_tasks', [])), 1)


class FileUploadAnalyzeSpreadsheetImportTests(APITestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Upload Org', slug='upload-org')
        self.user = CustomUser.objects.create_user(
            email='upload@test.com',
            username='uploaduser',
            password='testpass123',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='Upload Project',
            organization=self.org,
            owner=self.user,
        )
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        # The upload-and-analyze path records per-spreadsheet consent itself for
        # the spreadsheet it creates, so no pre-grant is needed here.
        self.session = AgentSession.objects.create(
            user=self.user,
            project=self.project,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch('agent.views.AgentOrchestrator')
    def test_upload_analyze_creates_spreadsheet_and_emits_link(self, MockOrch):
        import csv
        import os
        from django.core.files.uploadedfile import SimpleUploadedFile
        from spreadsheet.models import Spreadsheet
        from agent.models import ImportedCSVFile

        csv_dir = tempfile.mkdtemp()
        with self.settings(AGENT_CSV_DIR=csv_dir):
            mock_instance = MagicMock()
            mock_instance.handle_message.return_value = iter([{'type': 'done'}])
            MockOrch.return_value = mock_instance

            csv_path = os.path.join(tempfile.mkdtemp(), 'perf.csv')
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Campaign', 'Spend'])
                writer.writerow(['Alpha', '100'])

            with open(csv_path, 'rb') as f:
                upload = SimpleUploadedFile('perf.csv', f.read(), content_type='text/csv')

            response = self.client.post(
                '/api/agent/upload-analyze/',
                {
                    'file': upload,
                    'session_id': str(self.session.id),
                    'generation_outputs': '["recommended_tasks"]',
                },
                format='multipart',
                HTTP_X_PROJECT_ID=str(self.project.id),
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)

            content = b''.join(response.streaming_content).decode()
            self.assertIn('"type": "file_uploaded"', content)
            self.assertIn('"spreadsheet_id"', content)

            file_event = None
            for line in content.splitlines():
                if not line.startswith('data: '):
                    continue
                payload = json.loads(line[6:])
                if payload.get('type') == 'file_uploaded':
                    file_event = payload
                    break
            self.assertIsNotNone(file_event)
            spreadsheet_id = file_event['data']['spreadsheet_id']
            self.assertTrue(
                Spreadsheet.objects.filter(id=spreadsheet_id, project=self.project).exists()
            )

            user_msg = AgentMessage.objects.filter(session=self.session, role='user').latest('created_at')
            self.assertEqual(user_msg.metadata.get('spreadsheet_id'), spreadsheet_id)
            self.assertIn('url', user_msg.metadata)

            imported = ImportedCSVFile.objects.latest('created_at')
            self.assertEqual(imported.spreadsheet_id, spreadsheet_id)

            _, kwargs = mock_instance.handle_message.call_args
            self.assertEqual(kwargs.get('spreadsheet_id'), spreadsheet_id)

            # The upload is itself the user's per-spreadsheet AI consent.
            from spreadsheet.models import SpreadsheetAiConsent

            self.assertTrue(
                SpreadsheetAiConsent.objects.filter(
                    user=self.user, spreadsheet_id=spreadsheet_id
                ).exists()
            )

    @patch('agent.services.file_parser.parse_file_to_json')
    @patch('agent.services.data_service._get_csv_dir')
    @patch('os.path.isfile')
    def test_start_workflow_links_spreadsheet_when_both_ids_provided(
        self, mock_isfile, mock_csv_dir, mock_parse
    ):
        from agent.models import ImportedCSVFile, AgentWorkflowDefinition, AgentWorkflowStep, AgentWorkflowRun
        from spreadsheet.models import Spreadsheet
        from agent.services import AgentOrchestrator

        mock_isfile.return_value = True
        mock_csv_dir.return_value = '/tmp'
        mock_parse.return_value = {'name': 'test.csv', 'sheets': []}

        spreadsheet = Spreadsheet.objects.create(project=self.project, name='Linked Sheet')
        csv_file = ImportedCSVFile.objects.create(
            filename='test.csv',
            original_filename='test.csv',
            user=self.user,
            project=self.project,
        )
        wf = AgentWorkflowDefinition.objects.create(
            name='Link WF', is_default=True, is_system=True, status='active',
        )
        AgentWorkflowStep.objects.create(
            workflow=wf, name='Analyze', step_type='analyze_data', order=1,
        )

        with patch('agent.services._run_analysis', return_value={'anomalies': [], 'recommended_tasks': []}):
            orch = AgentOrchestrator(user=self.user, project=self.project, session=self.session)
            list(orch.handle_message(
                '',
                file_id=str(csv_file.id),
                spreadsheet_id=spreadsheet.id,
            ))

        run = AgentWorkflowRun.objects.filter(session=self.session).latest('created_at')
        self.assertEqual(run.spreadsheet_id, spreadsheet.id)

