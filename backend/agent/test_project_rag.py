"""Tests for AgentOrchestrator.answer_project_question, the generic
project-document RAG fallback (MED-264 agent integration).

Follows the same pattern as test_draft_context.py: real ORM (Django
TestCase), mock only the retrieval call and the LLM call, never hit Gemini.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from agent.models import AgentMessage, AgentSession
from agent.services import AgentOrchestrator
from core.models import Organization, Project
from rag.retrieval import RetrievedChunk

User = get_user_model()


def _chunk(source_id='1', chunk_index=0, content='chunk text', similarity=0.9, source_type='meeting'):
    return RetrievedChunk(
        content=content,
        source_type=source_type,
        source_id=source_id,
        chunk_index=chunk_index,
        citation_metadata={'title': f'Doc {source_id}'},
        distance=1.0 - similarity,
    )


def _llm_ok(text='answer'):
    return {'text': text, 'usage': {'input': 1, 'output': 1}}


class AnswerProjectQuestionTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='RAG Org')
        self.user = User.objects.create_user(
            email='rag@test.com', username='raguser', password='pw',
        )
        self.project = Project.objects.create(
            name='RAG Project', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)
        self.orch = AgentOrchestrator(self.user, self.project, self.session)
        self.when = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def _add_message(self, role, content, *, minutes=0):
        message = AgentMessage.objects.create(
            session=self.session,
            role=role,
            content=content,
            message_type='text',
        )
        AgentMessage.objects.filter(pk=message.pk).update(
            created_at=self.when + timedelta(minutes=minutes),
        )
        return message

    def _seed_follow_up(self, current_question='Why was that?'):
        """Earlier completed turn plus the current question already persisted."""
        self._add_message('user', 'Which vendor did we pick?', minutes=0)
        self._add_message('assistant', 'Vendor A.', minutes=1)
        self._add_message('user', current_question, minutes=2)
        return current_question

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_retrieval_is_invoked_with_project_id_and_question(self, mock_retrieve, mock_llm):
        """retrieve_chunks is called exactly once with (project.id, message),
        and the LLM is only called after retrieval has returned -- proven by
        a shared call-order list both mocks append to.
        """
        call_order = []

        def _retrieve(*a, **k):
            call_order.append('retrieve')
            return [_chunk()]

        def _llm(*a, **k):
            call_order.append('llm')
            return {'text': 'answer', 'usage': {'input': 1, 'output': 1}}

        mock_retrieve.side_effect = _retrieve
        mock_llm.side_effect = _llm

        list(self.orch.answer_project_question("What happened in the last meeting?"))

        mock_retrieve.assert_called_once_with(
            self.project.id, "What happened in the last meeting?"
        )
        mock_llm.assert_called_once()
        self.assertEqual(call_order, ['retrieve', 'llm'])

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_retrieved_context_is_included_in_llm_prompt(self, mock_retrieve, mock_llm):
        mock_retrieve.return_value = [
            _chunk(source_id='42', content='Q3 launch date moved to October.'),
        ]
        mock_llm.return_value = {'text': 'answer', 'usage': {'input': 1, 'output': 1}}

        list(self.orch.answer_project_question("When does Q3 launch?"))

        user_prompt = mock_llm.call_args.kwargs['user_prompt']
        self.assertIn('Q3 launch date moved to October.', user_prompt)
        self.assertIn('meeting:42#0', user_prompt)
        self.assertIn('When does Q3 launch?', user_prompt)

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_citations_match_retrieved_chunks_and_rank(self, mock_retrieve, mock_llm):
        mock_retrieve.return_value = [
            _chunk(source_id='a', chunk_index=0, content='first', similarity=0.9, source_type='meeting'),
            _chunk(source_id='b', chunk_index=2, content='second', similarity=0.4, source_type='notion_draft'),
        ]
        mock_llm.return_value = {'text': 'answer citing [1] and [2]', 'usage': {'input': 1, 'output': 1}}

        chunks = list(self.orch.answer_project_question("q"))

        text_chunk = next(c for c in chunks if c['type'] == 'text')
        citations = text_chunk['data']['citations']
        self.assertEqual(len(citations), 2)
        self.assertEqual(citations[0], {
            'n': 1,
            'source_type': 'meeting',
            'source_id': 'a',
            'chunk_index': 0,
            'citation_metadata': {'title': 'Doc a'},
            'snippet': 'first',
            'similarity': 0.9,
        })
        self.assertEqual(citations[1], {
            'n': 2,
            'source_type': 'notion_draft',
            'source_id': 'b',
            'chunk_index': 2,
            'citation_metadata': {'title': 'Doc b'},
            'snippet': 'second',
            'similarity': 0.4,
        })

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_no_retrieved_chunks_yields_safe_response_without_llm_call(self, mock_retrieve, mock_llm):
        mock_retrieve.return_value = []

        chunks = list(self.orch.answer_project_question("anything?"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]['type'], 'text')
        self.assertEqual(chunks[0]['data']['citations'], [])
        self.assertNotIn('error', [c['type'] for c in chunks])
        mock_llm.assert_not_called()

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_retrieval_exception_yields_error_and_skips_llm_call(self, mock_retrieve, mock_llm):
        mock_retrieve.side_effect = RuntimeError("Gemini embeddings unavailable")

        chunks = list(self.orch.answer_project_question("anything?"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]['type'], 'error')
        mock_llm.assert_not_called()

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_llm_exception_yields_error(self, mock_retrieve, mock_llm):
        mock_retrieve.return_value = [_chunk()]
        mock_llm.side_effect = RuntimeError("Gemini unavailable")

        chunks = list(self.orch.answer_project_question("anything?"))

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]['type'], 'error')

    @patch('agent.services.AgentOrchestrator.answer_project_question')
    @patch('rag.retrieval.retrieve_chunks')
    def test_dedicated_route_is_not_intercepted_by_generic_rag_fallback(self, mock_retrieve, mock_answer_project):
        # action='resume_workflow' with no matching workflow_run is a fully
        # self-contained dedicated route (agent/services.py handle_message) --
        # it must return its own message and never fall through to the
        # generic project-RAG fallback.
        chunks = list(self.orch.handle_message("continue", action='resume_workflow'))

        types = [c['type'] for c in chunks]
        self.assertIn('text', types)
        self.assertIn('done', types)
        self.assertIn('already finished', chunks[0]['content'])
        mock_answer_project.assert_not_called()
        mock_retrieve.assert_not_called()

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_handle_message_routes_generic_question_to_project_rag(self, mock_retrieve, mock_llm):
        mock_retrieve.return_value = [_chunk()]
        mock_llm.return_value = {'text': 'answer', 'usage': {'input': 1, 'output': 1}}

        chunks = list(self.orch.handle_message("What is this project about?"))

        types = [c['type'] for c in chunks]
        self.assertIn('text', types)
        self.assertIn('done', types)
        mock_retrieve.assert_called_once_with(self.project.id, "What is this project about?")

    @patch('agent.query_rewrite.rewrite_project_question', return_value='Why did we pick vendor A?')
    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_rewritten_question_is_searched_and_both_questions_are_in_the_prompt(
        self, mock_retrieve, mock_llm, mock_rewrite,
    ):
        mock_retrieve.return_value = [_chunk(content='Vendor A was cheaper.')]
        mock_llm.return_value = {'text': 'answer', 'usage': {'input': 1, 'output': 1}}

        list(self.orch.answer_project_question('Why was that?'))

        mock_rewrite.assert_called_once_with(self.session, self.project, 'Why was that?')
        mock_retrieve.assert_called_once_with(self.project.id, 'Why did we pick vendor A?')
        user_prompt = mock_llm.call_args.kwargs['user_prompt']
        self.assertIn('Question to answer: Why did we pick vendor A?', user_prompt)
        self.assertIn("User's exact wording: Why was that?", user_prompt)

    @patch('agent.query_rewrite.rewrite_project_question', return_value='Why was that?')
    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_rewrite_fallback_uses_the_original_question(self, mock_retrieve, mock_llm, _rewrite):
        mock_retrieve.return_value = [_chunk()]
        mock_llm.return_value = {'text': 'answer', 'usage': {'input': 1, 'output': 1}}

        list(self.orch.answer_project_question('Why was that?'))

        mock_retrieve.assert_called_once_with(self.project.id, 'Why was that?')
        user_prompt = mock_llm.call_args.kwargs['user_prompt']
        self.assertIn('Question to answer: Why was that?', user_prompt)
        self.assertIn("User's exact wording: Why was that?", user_prompt)

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_no_history_skips_rewrite_and_searches_the_original_question(
        self, mock_retrieve, mock_llm,
    ):
        question = 'What happened in the last meeting?'
        self._add_message('user', question, minutes=0)
        mock_retrieve.return_value = [_chunk()]
        mock_llm.return_value = _llm_ok()

        chunks = list(self.orch.answer_project_question(question))

        mock_retrieve.assert_called_once_with(self.project.id, question)
        mock_llm.assert_called_once()
        self.assertEqual(mock_llm.call_args.kwargs['temperature'], 0.3)
        self.assertEqual(chunks[0]['type'], 'text')

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_history_rewrites_then_searches_and_keeps_both_questions_in_the_prompt(
        self, mock_retrieve, mock_llm,
    ):
        question = self._seed_follow_up()
        rewritten = 'Why did we pick vendor A?'
        mock_retrieve.return_value = [_chunk(content='Vendor A was cheaper.')]
        mock_llm.side_effect = [_llm_ok(rewritten), _llm_ok('Because it was cheaper.')]

        chunks = list(self.orch.answer_project_question(question))

        self.assertEqual(mock_llm.call_count, 2)
        rewrite_call, answer_call = mock_llm.call_args_list
        self.assertEqual(rewrite_call.kwargs['temperature'], 0)
        self.assertEqual(answer_call.kwargs['temperature'], 0.3)
        mock_retrieve.assert_called_once_with(self.project.id, rewritten)
        user_prompt = answer_call.kwargs['user_prompt']
        self.assertIn(f'Question to answer: {rewritten}', user_prompt)
        self.assertIn(f"User's exact wording: {question}", user_prompt)
        self.assertEqual(chunks[0]['type'], 'text')
        self.assertEqual(chunks[0]['content'], 'Because it was cheaper.')

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_rewrite_timeout_falls_back_and_still_answers(self, mock_retrieve, mock_llm):
        question = self._seed_follow_up()
        mock_retrieve.return_value = [_chunk()]
        mock_llm.side_effect = [TimeoutError('rewrite timed out'), _llm_ok('fallback answer')]

        chunks = list(self.orch.answer_project_question(question))

        mock_retrieve.assert_called_once_with(self.project.id, question)
        self.assertEqual(mock_llm.call_count, 2)
        user_prompt = mock_llm.call_args_list[1].kwargs['user_prompt']
        self.assertIn(f'Question to answer: {question}', user_prompt)
        self.assertIn(f"User's exact wording: {question}", user_prompt)
        self.assertEqual(chunks[0]['type'], 'text')
        self.assertEqual(chunks[0]['content'], 'fallback answer')

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.retrieve_chunks')
    def test_empty_rewrite_falls_back_and_still_answers(self, mock_retrieve, mock_llm):
        question = self._seed_follow_up()
        mock_retrieve.return_value = [_chunk()]
        mock_llm.side_effect = [_llm_ok(''), _llm_ok('fallback answer')]

        chunks = list(self.orch.answer_project_question(question))

        mock_retrieve.assert_called_once_with(self.project.id, question)
        self.assertEqual(mock_llm.call_count, 2)
        self.assertEqual(chunks[0]['type'], 'text')
        self.assertEqual(chunks[0]['content'], 'fallback answer')
