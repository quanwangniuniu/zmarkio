"""Tests for Draft -> Agent context (AGENT-7, read-only).

The Agent reads a draft by reusing notion_editor's DraftViewSet.get_queryset()
in-process (its user-scoped permissions) and _html_to_plain_text for rendering.
These tests use the real ORM (Django TestCase) and only mock the LLM call, so
the permission boundary is exercised for real. The critical guarantee is that
the LLM is never called when the user may not access the draft.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Organization, Project
from agent.models import AgentSession
from agent.services import AgentOrchestrator
from notion_editor.models import Draft

User = get_user_model()


def _draft(user, title, html="Some content."):
    return Draft.objects.create(
        user=user,
        title=title,
        content_blocks=[{"type": "rich_text", "content": {"html": html}}],
    )


class AnswerDraftQuestionTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Draft Org')
        self.user = User.objects.create_user(
            email='draft@test.com', username='draftuser', password='pw',
        )
        self.user.organization = self.org
        self.user.save()
        self.other = User.objects.create_user(
            email='other-draft@test.com', username='otherdraft', password='pw',
        )
        self.project = Project.objects.create(
            name='Draft Project', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)
        self.orch = AgentOrchestrator(self.user, self.project, self.session)
        self.draft = _draft(self.user, "Q3 Campaign Plan", "Target EU region, raise ROAS.")

    @patch('core.services.gemini_client._get_api_key', return_value='key')
    @patch('core.services.gemini_client.call_gemini')
    def test_user_can_read_own_draft(self, mock_gemini, _mock_key):
        mock_gemini.return_value = "Your draft focuses on the EU region."
        chunks = list(self.orch.answer_draft_question(
            "What is this draft about?", {"draftId": self.draft.slug},
        ))
        self.assertIn('text', [c['type'] for c in chunks])
        self.assertNotIn('error', [c['type'] for c in chunks])
        # Real draft content (rendered via notion_editor) reached the LLM prompt.
        user_prompt = mock_gemini.call_args.kwargs['user_prompt']
        self.assertIn("Target EU region", user_prompt)
        self.assertIn("Q3 Campaign Plan", user_prompt)

    @patch('core.services.gemini_client._get_api_key', return_value='key')
    @patch('core.services.gemini_client.call_gemini')
    def test_resolves_draft_by_numeric_pk(self, mock_gemini, _mock_key):
        mock_gemini.return_value = "ok"
        chunks = list(self.orch.answer_draft_question(
            "Summarize", {"draftId": str(self.draft.pk)},
        ))
        self.assertIn('text', [c['type'] for c in chunks])

    @patch('core.services.gemini_client.call_gemini')
    def test_cannot_read_other_users_draft_and_llm_never_called(self, mock_gemini):
        # Owned by another user -> DraftViewSet.get_queryset() excludes it.
        foreign = _draft(self.other, "Secret", "top secret content")
        chunks = list(self.orch.answer_draft_question(
            "Read it", {"draftId": foreign.slug},
        ))
        self.assertEqual(chunks[0]['type'], 'error')
        mock_gemini.assert_not_called()

    @patch('core.services.gemini_client.call_gemini')
    def test_soft_deleted_draft_is_inaccessible(self, mock_gemini):
        self.draft.is_deleted = True
        self.draft.save(update_fields=['is_deleted'])
        chunks = list(self.orch.answer_draft_question("read it", {"draftId": self.draft.slug}))
        self.assertEqual(chunks[0]['type'], 'error')
        mock_gemini.assert_not_called()

    @patch('core.services.gemini_client.call_gemini')
    def test_nonexistent_slug_yields_clean_error(self, mock_gemini):
        chunks = list(self.orch.answer_draft_question("read it", {"draftId": "does-not-exist"}))
        self.assertEqual(chunks[0]['type'], 'error')
        mock_gemini.assert_not_called()

    @patch('core.services.gemini_client.call_gemini')
    def test_missing_draft_ref_yields_error(self, mock_gemini):
        chunks = list(self.orch.answer_draft_question("hi", {}))
        self.assertEqual(chunks[0]['type'], 'error')
        mock_gemini.assert_not_called()

    @patch('core.services.gemini_client._get_api_key', return_value='key')
    @patch('core.services.gemini_client.call_gemini')
    def test_handle_message_routes_draft_context(self, mock_gemini, _mock_key):
        mock_gemini.return_value = "answer"
        chunks = list(self.orch.handle_message(
            "What's here?", draft_context={"draftId": self.draft.slug},
        ))
        types = [c['type'] for c in chunks]
        self.assertIn('text', types)
        self.assertIn('done', types)
