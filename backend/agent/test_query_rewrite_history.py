"""History selection for project-RAG query rewrite. No LLM calls."""
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from agent.models import AgentMessage, AgentSession
from agent.query_rewrite import recent_rewrite_turns
from core.models import Organization, Project

User = get_user_model()


class RecentRewriteTurnsTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Rewrite History Org')
        self.user = User.objects.create_user(
            email='rewrite-history@test.com', username='rewritehistory', password='pw',
        )
        self.project = Project.objects.create(
            name='Rewrite History Project', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)
        self.when = datetime(2026, 10, 19, tzinfo=timezone.utc)

    def _add(self, role, content, *, message_type='text', minutes=0):
        message = AgentMessage.objects.create(
            session=self.session,
            role=role,
            content=content,
            message_type=message_type,
        )
        AgentMessage.objects.filter(pk=message.pk).update(
            created_at=self.when + timedelta(minutes=minutes),
        )
        return message

    def test_current_user_message_is_excluded(self):
        self._add('user', 'Why did we pick vendor A?', minutes=0)
        self._add('assistant', 'Because the launch date moved.', minutes=1)
        self._add('user', 'Why was that?', minutes=2)

        self.assertEqual(
            recent_rewrite_turns(self.session),
            [('Why did we pick vendor A?', 'Because the launch date moved.')],
        )

    def test_only_the_current_question_returns_empty(self):
        self._add('user', 'What happened in the last meeting?', minutes=0)

        self.assertEqual(recent_rewrite_turns(self.session), [])

    def test_user_message_without_an_assistant_reply_is_not_a_turn(self):
        self._add('user', 'First question with no reply', minutes=0)
        self._add('user', 'Why was that?', minutes=1)

        self.assertEqual(recent_rewrite_turns(self.session), [])

    def test_keeps_only_the_latest_configured_turns(self):
        self._add('user', 'turn one question', minutes=0)
        self._add('assistant', 'turn one answer', minutes=1)
        self._add('user', 'turn two question', minutes=2)
        self._add('assistant', 'turn two answer', minutes=3)
        self._add('user', 'turn three question', minutes=4)
        self._add('assistant', 'turn three answer', minutes=5)
        self._add('user', 'current question', minutes=6)

        with override_settings(RAG_QUERY_REWRITE_HISTORY_TURNS=2):
            self.assertEqual(
                recent_rewrite_turns(self.session),
                [
                    ('turn two question', 'turn two answer'),
                    ('turn three question', 'turn three answer'),
                ],
            )

    def test_assistant_text_is_truncated(self):
        self._add('user', 'What changed?', minutes=0)
        self._add('assistant', 'abcdefghij', minutes=1)
        self._add('user', 'And then?', minutes=2)

        with override_settings(RAG_QUERY_REWRITE_ASSISTANT_MAX_CHARS=4):
            self.assertEqual(
                recent_rewrite_turns(self.session),
                [('What changed?', 'abcd')],
            )

    def test_consecutive_assistant_messages_form_one_turn(self):
        self._add('user', 'What changed?', minutes=0)
        self._add('assistant', 'Part one.', minutes=1)
        self._add('assistant', 'Part two.', minutes=2)
        self._add('user', 'Why?', minutes=3)

        self.assertEqual(
            recent_rewrite_turns(self.session),
            [('What changed?', 'Part one.\nPart two.')],
        )

    def test_assistant_text_after_the_current_question_is_not_history(self):
        self._add('user', 'What changed?', minutes=0)
        self._add('assistant', 'The date moved.', minutes=1)
        self._add('user', 'Why?', minutes=2)
        self._add('assistant', 'This reply belongs to the current question.', minutes=3)

        self.assertEqual(
            recent_rewrite_turns(self.session),
            [('What changed?', 'The date moved.')],
        )

    def test_non_text_and_system_messages_are_ignored(self):
        self._add('system', 'internal note', minutes=0)
        self._add('user', 'What changed?', minutes=1)
        self._add('assistant', 'The date moved.', message_type='analysis', minutes=2)
        self._add('assistant', 'October.', minutes=3)
        self._add('user', 'Why?', minutes=4)

        self.assertEqual(
            recent_rewrite_turns(self.session),
            [('What changed?', 'October.')],
        )
