"""
Regression test for the ChatView StreamingHttpResponse tenant-context bug.

Root cause (see agent/views.py ChatView.post): StreamingHttpResponse(event_stream(), ...)
only *constructs* the lazy generator -- its body (session/message persistence,
AgentOrchestrator.handle_message, RAG retrieval) doesn't run until Django
actually drains the stream, which happens after the view -- and therefore
TenantSchemaMiddleware's `finally: SET search_path TO public` -- has already
returned. Every DB call inside the generator used to run against the empty
`public` schema instead of the tenant's, so RAG retrieval silently returned
zero chunks for every question, regardless of what was actually indexed.

This test reproduces that exact lifecycle without depending on the full
JWT/middleware stack (irrelevant to what's being verified): it drives
ChatView directly, manually toggling search_path around the call exactly the
way TenantSchemaMiddleware does, then -- critically -- only *consumes* the
StreamingHttpResponse's generator afterward, mirroring how Django's real
response handling drains a streaming body outside the middleware chain.

No live Gemini/embedding calls: rag.retrieval.embed_query and
agent.llm_client.call_llm are mocked to deterministic values, per this
project's existing test convention (see test_project_rag.py).
"""
from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from psycopg2 import sql
from rest_framework.test import APIRequestFactory, force_authenticate

from agent.models import AgentSession
from agent.views import ChatView
from core.models import Organization, Project
from core.services.tenant import slug_to_schema_name
from core.tenant_context import current_tenant_schema, tenant_schema_context
from rag.models import DocumentChunk, DocumentSourceType

User = get_user_model()

_FAKE_QUERY_VECTOR = [0.1] * 768
_RETRO_ANSWER_TEXT = "The session-timeout fix was deployed on Nov 20 [1]."


class ChatViewTenantStreamingTest(TestCase):
    """Drives the real ChatView + real rag.retrieval query against a real
    second Postgres schema -- nothing about tenant resolution is mocked,
    only the embedding call and the LLM call are.
    """

    @classmethod
    def setUpTestData(cls):
        # Organization.save() synchronously provisions a real physical schema
        # (CREATE SCHEMA + tenant migrations) -- see core/models.py, core/
        # services/tenant.py. This is a genuinely separate schema from
        # 'public', which is exactly what makes the bug observable: if the
        # generator ran against 'public' (empty) instead, the chunk below
        # would not be found. Provisioning a schema is expensive (many DDL
        # round-trips), so this is done once for the whole class via
        # setUpTestData rather than per-test in setUp -- each test still gets
        # an isolated view of any rows it creates, via TestCase's normal
        # per-test transaction rollback.
        cls.org = Organization.objects.create(name='Streaming Regression Org')
        cls.schema = slug_to_schema_name(cls.org.slug)

        cls.user = User.objects.create_user(
            email='streaming-regression@test.com',
            username='streamingregressionuser',
            password='pw',
            organization=cls.org,
        )

        with tenant_schema_context(cls.schema):
            cls.project = Project.objects.create(
                name='Streaming Regression Project', organization=cls.org, owner=cls.user,
            )
            cls.chunk = DocumentChunk.objects.create(
                project=cls.project,
                source_type=DocumentSourceType.RETROSPECTIVE,
                source_id='retro-008-equivalent',
                chunk_index=0,
                content="Aurora Retail's engineering team deployed the session-timeout fix on Nov 20.",
                embedding=_FAKE_QUERY_VECTOR,
                source_updated_at=timezone.now(),
                citation_metadata={'title': 'Sprint 20 Retrospective — Checkout Flow Fix Deployed'},
            )

        # AgentSession is a public-schema model (db_constraint=False FK to
        # the tenant-scoped Project) -- created in the default 'public'
        # search_path, exactly like the real app does.
        cls.session = AgentSession.objects.create(user=cls.user, project=cls.project)

    def _post_chat_message_across_schema_reset(self, message: str):
        """Reproduces the real request lifecycle end to end:
        1. search_path set to the tenant schema (TenantSchemaMiddleware's
           normal per-request behavior).
        2. ChatView.post() is called and returns a StreamingHttpResponse --
           at this point event_stream()'s body has NOT run yet, it's a lazy
           generator.
        3. search_path is reset to 'public' (TenantSchemaMiddleware's
           `finally` block, which -- for a real streaming response --
           already fires before anything drains the stream).
        4. Only now is the stream actually consumed, exactly mirroring how
           Django's outer response-sending layer drains a StreamingHttpResponse
           after the middleware chain has fully unwound.
        """
        factory = APIRequestFactory()
        django_request = factory.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            {'message': message},
            format='json',
        )
        force_authenticate(django_request, user=self.user)

        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.schema)))

        response = ChatView.as_view()(django_request, session_id=str(self.session.id))

        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

        body = b''.join(response.streaming_content).decode('utf-8')
        return body

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.embed_query')
    def test_tenant_scoped_chunk_is_still_visible_after_schema_reset(self, mock_embed, mock_llm):
        """The core regression: DocumentChunk seeded in the tenant schema
        must still be found by retrieve_chunks() even though search_path was
        reset to 'public' before the stream was consumed.
        """
        mock_embed.return_value = _FAKE_QUERY_VECTOR
        mock_llm.return_value = {'text': _RETRO_ANSWER_TEXT, 'usage': {'input': 1, 'output': 1}}

        body = self._post_chat_message_across_schema_reset(
            "When was Aurora Retail's engineering team's session-timeout fix finally deployed?"
        )

        self.assertIn('"citations"', body)
        self.assertIn('retro-008-equivalent', body)
        self.assertIn('Nov 20', body)

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.embed_query')
    def test_no_longer_takes_the_no_chunks_branch(self, mock_embed, mock_llm):
        """Direct assertion on the specific failure mode reported: before the
        fix, every question -- including this one -- fell into
        answer_project_question's `if not retrieved:` branch and returned the
        canned "I couldn't find anything..." text with empty citations,
        because retrieve_chunks() ran against the (empty) public schema.
        """
        mock_embed.return_value = _FAKE_QUERY_VECTOR
        mock_llm.return_value = {'text': _RETRO_ANSWER_TEXT, 'usage': {'input': 1, 'output': 1}}

        body = self._post_chat_message_across_schema_reset(
            "When was Aurora Retail's engineering team's session-timeout fix finally deployed?"
        )

        self.assertNotIn("I couldn't find anything in this project's documents", body)
        self.assertNotIn('"citations": []', body)
        mock_llm.assert_called_once()

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.embed_query')
    def test_schema_is_restored_to_public_after_stream_completes(self, mock_embed, mock_llm):
        """tenant_schema_context's own finally block must still hand control
        back to whatever the connection's schema was before event_stream()
        entered it (public, per the simulated middleware reset above) once
        the generator finishes -- verifying cleanup on normal completion.
        """
        mock_embed.return_value = _FAKE_QUERY_VECTOR
        mock_llm.return_value = {'text': _RETRO_ANSWER_TEXT, 'usage': {'input': 1, 'output': 1}}

        self._post_chat_message_across_schema_reset("anything")

        # tenant_schema_context's own restore always appends a trailing
        # ", public" (e.g. "public, public" when the previous schema already
        # was public) -- an existing, harmless quirk of that helper, not a
        # bug. SHOW search_path's raw value reflects that verbatim, so it's
        # checked by its effective (first) entry; current_tenant_schema()
        # (Postgres's own current_schema()) already does exactly that
        # normalization, so both are asserted for belt-and-suspenders
        # coverage of the same underlying fact.
        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            raw_search_path = cursor.fetchone()[0]
        self.assertEqual(raw_search_path.split(',')[0].strip(), 'public')
        self.assertEqual(current_tenant_schema(), 'public')

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.embed_query')
    def test_schema_is_restored_to_public_after_stream_raises(self, mock_embed, mock_llm):
        """Cleanup on the generator's own exception path: event_stream()'s
        inner `except Exception` still yields an error payload and returns
        normally (it doesn't re-raise), so this exercises the same `with`
        __exit__ as the happy path, but via the generator's error branch.
        """
        mock_embed.return_value = _FAKE_QUERY_VECTOR
        mock_llm.side_effect = RuntimeError('simulated LLM failure')

        body = self._post_chat_message_across_schema_reset(
            "When was Aurora Retail's engineering team's session-timeout fix finally deployed?"
        )

        self.assertIn('"type": "error"', body)

        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            raw_search_path = cursor.fetchone()[0]
        self.assertEqual(raw_search_path.split(',')[0].strip(), 'public')
        self.assertEqual(current_tenant_schema(), 'public')

    @patch('agent.llm_client.call_llm')
    @patch('rag.retrieval.embed_query')
    def test_schema_is_restored_to_public_if_stream_is_closed_early(self, mock_embed, mock_llm):
        """Cleanup on client disconnect: closing the StreamingHttpResponse's
        generator before it's exhausted raises GeneratorExit at the
        suspended yield -- the `with tenant_schema_context(...):` wrapping
        the whole generator body must still restore search_path.
        """
        mock_embed.return_value = _FAKE_QUERY_VECTOR
        mock_llm.return_value = {'text': _RETRO_ANSWER_TEXT, 'usage': {'input': 1, 'output': 1}}

        factory = APIRequestFactory()
        django_request = factory.post(
            f'/api/agent/sessions/{self.session.id}/chat/',
            {'message': 'anything'},
            format='json',
        )
        force_authenticate(django_request, user=self.user)

        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(self.schema)))

        response = ChatView.as_view()(django_request, session_id=str(self.session.id))

        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

        # Advance the generator by one item, then abandon it early --
        # emulating a client disconnecting mid-stream. streaming_content is
        # `map(self.make_bytes, self._iterator)` (Django wraps the raw
        # generator to bytes-encode each chunk), so it has no .close() of
        # its own. response.close() is Django's real cleanup hook for this
        # (it calls response._iterator.close() via _resource_closers) but
        # ALSO fires the request_finished signal, which closes the shared DB
        # connection outright (CONN_MAX_AGE=0) -- fatal to a TestCase's later
        # tests reusing that same connection. Calling close() directly on the
        # underlying generator gets the exact same GeneratorExit-at-the-
        # suspended-yield behavior we're testing, without that side effect.
        next(iter(response.streaming_content))
        response._iterator.close()

        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            raw_search_path = cursor.fetchone()[0]
        self.assertEqual(raw_search_path.split(',')[0].strip(), 'public')
        self.assertEqual(current_tenant_schema(), 'public')
