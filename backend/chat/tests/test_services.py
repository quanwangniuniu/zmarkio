import pytest
import logging
from unittest.mock import patch
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from core.models import Organization, Project, ProjectMember
from chat.models import Chat, ChatParticipant, ChatType
from chat.serializers import ChatCreateSerializer
from chat.models import Message, MessageStatus
from chat.services import (
    ChatService,
    MessageService,
    OnlineStatusService,
    UnsupportedAttachmentMimeType,
    claim_recipients_for_delivery,
    release_unpublished_recipients,
    validate_attachment_mime_type,
)
pytestmark = pytest.mark.django_db

User = get_user_model()


def _message_with_pending_recipient():
    organization = Organization.objects.create(name='Claim Org')
    project = Project.objects.create(name='Claim Project', organization=organization)
    chat = Chat.objects.create(project=project, type=ChatType.GROUP)
    sender = User.objects.create_user(
        email='claim_sender@example.com', username='claim_sender', password='pass12345'
    )
    recipient = User.objects.create_user(
        email='claim_recipient@example.com', username='claim_recipient', password='pass12345'
    )
    for user in (sender, recipient):
        ChatParticipant.objects.create(chat=chat, user=user, is_active=True)
    message = Message.objects.create(chat=chat, sender=sender, content='claim test')
    MessageStatus.objects.create(message=message, user=recipient, status='sent')
    return message, recipient


def test_delivery_claim_is_taken_once():
    """Only the first claimer of a recipient may publish.

    All three delivery paths — realtime fan-out, offline delivery and reconnect
    recovery — claim by winning the sent -> delivered transition, so a second
    claimer must come back empty rather than publishing a duplicate.
    """
    message, recipient = _message_with_pending_recipient()

    assert claim_recipients_for_delivery(message.id, [recipient.id]) == [recipient.id]
    assert claim_recipients_for_delivery(message.id, [recipient.id]) == []

    status = MessageStatus.objects.get(message=message, user=recipient)
    assert status.status == 'delivered'
    assert status.delivered_at is not None


def test_releasing_an_unpublished_claim_makes_it_claimable_again():
    """A claim handed back must be available to the next delivery attempt."""
    message, recipient = _message_with_pending_recipient()

    claim_recipients_for_delivery(message.id, [recipient.id])
    assert release_unpublished_recipients(message.id, [recipient.id]) == 1

    status = MessageStatus.objects.get(message=message, user=recipient)
    assert status.status == 'sent'
    assert status.delivered_at is None
    assert claim_recipients_for_delivery(message.id, [recipient.id]) == [recipient.id]


def test_joinable_chats_come_from_active_membership_only():
    """Chat-group entitlement must follow the database, not anything else.

    Joining `chat_<id>` is what lets a connection receive that chat, so this is
    the authorisation decision: a chat the user was removed from, or never
    joined, must not appear.
    """
    from chat.services import get_joinable_chat_ids

    organization = Organization.objects.create(name='Groups Org')
    project = Project.objects.create(name='Groups Project', organization=organization)
    member = User.objects.create_user(
        email='groups_member@example.com', username='groups_member', password='pass12345'
    )
    outsider = User.objects.create_user(
        email='groups_outsider@example.com', username='groups_outsider', password='pass12345'
    )

    joined = Chat.objects.create(project=project, type=ChatType.GROUP)
    removed_from = Chat.objects.create(project=project, type=ChatType.GROUP)
    never_joined = Chat.objects.create(project=project, type=ChatType.GROUP)

    ChatParticipant.objects.create(chat=joined, user=member, is_active=True)
    ChatParticipant.objects.create(chat=removed_from, user=member, is_active=False)
    ChatParticipant.objects.create(chat=never_joined, user=outsider, is_active=True)

    entitled = get_joinable_chat_ids(member.id)

    assert joined.id in entitled
    assert removed_from.id not in entitled, 'an inactive participant must lose the chat'
    assert never_joined.id not in entitled, 'a chat the user never joined must not appear'
    assert get_joinable_chat_ids(outsider.id) == [never_joined.id]


def test_membership_change_notifies_affected_users(settings):
    """Every membership mutator must trigger a re-sync of live connections.

    A connection holding a group it is no longer entitled to keeps receiving
    that chat until it closes, which can be hours, so the revocation signal is
    the part that has to be reliable.
    """
    settings.CHAT_CHANNEL_GROUPS_ENABLED = True

    organization = Organization.objects.create(name='Revoke Org')
    project = Project.objects.create(name='Revoke Project', organization=organization)
    chat = Chat.objects.create(project=project, type=ChatType.GROUP)
    staying = User.objects.create_user(
        email='revoke_stay@example.com', username='revoke_stay', password='pass12345'
    )
    leaving = User.objects.create_user(
        email='revoke_leave@example.com', username='revoke_leave', password='pass12345'
    )
    ChatParticipant.objects.create(chat=chat, user=staying, is_active=True)

    with patch('chat.services.broadcast_event_to_user_groups_sync') as broadcast:
        from chat.services import notify_chat_membership_changed

        notify_chat_membership_changed(chat.id, [staying.id, leaving.id])

    broadcast.assert_called_once()
    notified_user_ids = broadcast.call_args.args[1]
    event = broadcast.call_args.args[2]
    # The user who lost access must be told too, or their socket keeps the group.
    assert set(notified_user_ids) == {staying.id, leaving.id}
    assert event == {
        'type': 'chat_membership_changed',
        'chat_id': chat.id,
        'chat_slug': chat.slug,
        'project_id': project.id,
        'project_slug': project.slug,
    }


def test_membership_change_is_silent_while_chat_groups_are_disabled(settings):
    """The revocation broadcast is dead weight until the feature is on."""
    settings.CHAT_CHANNEL_GROUPS_ENABLED = False

    with patch('chat.services.broadcast_event_to_user_groups_sync') as broadcast:
        from chat.services import notify_chat_membership_changed

        notify_chat_membership_changed(1, [1, 2, 3])

    broadcast.assert_not_called()


def test_claim_ignores_recipients_already_delivered():
    """Releasing is scoped to what this caller claimed, not everything delivered."""
    message, recipient = _message_with_pending_recipient()

    claim_recipients_for_delivery(message.id, [recipient.id])
    # A user this caller never claimed must not be resurrected.
    assert release_unpublished_recipients(message.id, [999_999]) == 0
    assert MessageStatus.objects.get(message=message, user=recipient).status == 'delivered'

class TestOnlineStatusService:

    @pytest.fixture(autouse=True)
    def _setup(self):
        cache.clear()
        self.user = User.objects.create_user(username='presenceuser', email='presence@example.com', password='testpass123')
        yield
        cache.clear()

    def test_presence_snapshot_matches_per_user_lookups(self):
        """Batched snapshot must return exactly what the per-user reads return.

        The version lookup used to run once per user inside the comprehension;
        it is now fetched with one batched call, so pin the equivalence for a
        mix of online, offline and never-seen users.
        """
        online = User.objects.create_user(
            username='snap_online', email='snap_online@example.com', password='testpass123'
        )
        offline = User.objects.create_user(
            username='snap_offline', email='snap_offline@example.com', password='testpass123'
        )
        unknown_id = 99_123_456  # never connected, so no cache entries at all

        OnlineStatusService.connection_opened(online.id, 'conn-snap')
        OnlineStatusService.next_presence_version(offline.id)

        user_ids = [online.id, offline.id, unknown_id]
        snapshot = OnlineStatusService.presence_snapshot(user_ids)

        assert [entry['user_id'] for entry in snapshot] == user_ids
        for entry in snapshot:
            assert entry['is_online'] is OnlineStatusService.is_online(entry['user_id'])
            assert entry['version'] == OnlineStatusService.get_presence_version(entry['user_id'])

        assert snapshot[0]['is_online'] is True
        assert snapshot[1]['is_online'] is False
        assert snapshot[2] == {'user_id': unknown_id, 'is_online': False, 'version': 0}

    def test_multiple_connections_keep_user_online_until_last_disconnect(self):
        count, should_broadcast, version = OnlineStatusService.connection_opened(self.user.id, 'conn-1')
        assert count == 1
        assert should_broadcast
        assert isinstance(version, int)
        assert OnlineStatusService.is_online(self.user.id)
        assert OnlineStatusService.connection_opened(self.user.id, 'conn-2') == (2, False, None)
        assert OnlineStatusService.is_online(self.user.id)
        assert OnlineStatusService.connection_closed(self.user.id, 'conn-1') == (1, None)
        assert OnlineStatusService.is_online(self.user.id)
        remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'conn-2')
        assert remaining == 0
        assert offline_token is not None
        assert OnlineStatusService.is_online(self.user.id)
        offline_version = OnlineStatusService.finalize_offline_if_still_disconnected(self.user.id, offline_token)
        assert isinstance(offline_version, int)
        assert not OnlineStatusService.is_online(self.user.id)

    def test_non_redis_cache_backend_tracks_connections(self):
        with patch.object(OnlineStatusService, '_redis', side_effect=NotImplementedError('raw redis unavailable')):
            count, should_broadcast, version = OnlineStatusService.connection_opened(self.user.id, 'conn-1')
            assert count == 1
            assert should_broadcast
            assert isinstance(version, int)
            assert OnlineStatusService.is_online(self.user.id)
            assert OnlineStatusService.connection_opened(self.user.id, 'conn-2') == (2, False, None)
            assert OnlineStatusService.connection_closed(self.user.id, 'conn-1') == (1, None)
            assert OnlineStatusService.is_online(self.user.id)
            remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'conn-2')
            assert remaining == 0
            assert offline_token is not None
            assert OnlineStatusService.is_online(self.user.id)
            offline_version = OnlineStatusService.finalize_offline_if_still_disconnected(self.user.id, offline_token)
            assert isinstance(offline_version, int)
            assert not OnlineStatusService.is_online(self.user.id)

    def test_heartbeat_refreshes_presence_without_incrementing_connections(self):
        count, should_broadcast, version = OnlineStatusService.connection_opened(self.user.id, 'conn-1')
        assert count == 1
        assert should_broadcast
        assert isinstance(version, int)
        OnlineStatusService.heartbeat(self.user.id, 'conn-1')
        remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'conn-1')
        assert remaining == 0
        assert offline_token is not None
        assert isinstance(OnlineStatusService.finalize_offline_if_still_disconnected(self.user.id, offline_token), int)
        assert not OnlineStatusService.is_online(self.user.id)

    def test_pending_offline_is_canceled_by_reconnect(self):
        OnlineStatusService.connection_opened(self.user.id, 'old-conn')
        remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'old-conn')
        assert remaining == 0
        assert offline_token is not None
        count, should_broadcast, version = OnlineStatusService.connection_opened(self.user.id, 'new-conn')
        assert count == 1
        assert should_broadcast
        assert isinstance(version, int)
        assert OnlineStatusService.finalize_offline_if_still_disconnected(self.user.id, offline_token) is None
        assert OnlineStatusService.is_online(self.user.id)

    def test_presence_version_uses_timestamp_seed_after_existing_small_counter(self):
        key = OnlineStatusService._presence_version_key(self.user.id)
        cache.set(key, 5, timeout=OnlineStatusService.PRESENCE_VERSION_TIMEOUT)
        version = OnlineStatusService.next_presence_version(self.user.id)
        assert isinstance(version, int)
        assert version > 1000000000000

    def test_missing_presence_version_skips_online_broadcast(self):
        with patch.object(OnlineStatusService, 'next_presence_version', return_value=None):
            count, should_broadcast, version = OnlineStatusService.connection_opened(self.user.id, 'conn-1')
        assert count == 1
        assert not should_broadcast
        assert version is None

    def test_suppressed_online_broadcast_is_logged_for_monitoring(self, caplog):
        with patch.object(OnlineStatusService, 'next_presence_version', return_value=None):
            with caplog.at_level(logging.WARNING, logger='chat.services'):
                OnlineStatusService.connection_opened(self.user.id, 'conn-1')
        assert any(
            'presence_broadcast_skipped reason=no_version' in r.message
            for r in caplog.records
        )

    def test_invalidate_presence_recipients_clears_cached_lists(self):
        key = OnlineStatusService._presence_recipients_key(self.user.id)
        cache.set(key, [1, 2, 3], timeout=OnlineStatusService.PRESENCE_RECIPIENTS_TIMEOUT)
        OnlineStatusService.invalidate_presence_recipients([self.user.id])
        assert cache.get(key) is None

    def test_reconnect_during_finalize_keeps_user_online(self):
        OnlineStatusService.connection_opened(self.user.id, 'old-conn')
        remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'old-conn')
        assert remaining == 0
        assert offline_token is not None
        with patch.object(OnlineStatusService, '_connection_count', side_effect=[0, 1]):
            assert OnlineStatusService.finalize_offline_if_still_disconnected(self.user.id, offline_token) is None
        assert OnlineStatusService.is_online(self.user.id)

    def test_connection_count_failure_does_not_finalize_offline(self):
        OnlineStatusService.connection_opened(self.user.id, 'old-conn')
        remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'old-conn')
        assert remaining == 0
        assert offline_token is not None
        with patch.object(OnlineStatusService, '_connection_count', return_value=None):
            assert OnlineStatusService.finalize_offline_if_still_disconnected(self.user.id, offline_token) is None
        assert OnlineStatusService.is_online(self.user.id)

    def test_redis_add_connection_failure_degrades_presence_without_blocking_connect(self):
        with patch.object(OnlineStatusService, '_add_connection', side_effect=ConnectionError('redis down')):
            count, should_broadcast, version = OnlineStatusService.connection_opened(self.user.id, 'conn-1')
        assert count == 1
        assert not should_broadcast
        assert version is None

    def test_redis_remove_connection_failure_degrades_presence_without_crashing_disconnect(self):
        with patch.object(OnlineStatusService, '_remove_connection', side_effect=ConnectionError('redis down')):
            remaining, offline_token = OnlineStatusService.connection_closed(self.user.id, 'conn-1')
        assert remaining == 0
        assert offline_token is None

class TestPresenceRecipientCacheInvalidation:

    @pytest.fixture(autouse=True)
    def _setup(self):
        cache.clear()
        self.org = Organization.objects.create(name='Acme')
        self.project = Project.objects.create(organization=self.org, name='Project')
        self.user_a = User.objects.create_user(username='a', email='a@example.com', password='x')
        self.user_b = User.objects.create_user(username='b', email='b@example.com', password='x')
        for user in (self.user_a, self.user_b):
            ProjectMember.objects.create(user=user, project=self.project, role='Member', is_active=True)
        self.chat = Chat.objects.create(project=self.project, type=ChatType.GROUP)
        ChatParticipant.objects.create(chat=self.chat, user=self.user_a, is_active=True)
        ChatParticipant.objects.create(chat=self.chat, user=self.user_b, is_active=True)
        yield
        cache.clear()

    def test_get_presence_recipient_ids_caches_result(self):
        recipients = ChatService.get_presence_recipient_ids(self.user_a.id)
        assert recipients == [self.user_b.id]
        cached = cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id))
        assert cached == [self.user_b.id]

    def test_leaving_chat_invalidates_cached_recipient_lists(self, capture_on_commit_callbacks):
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        assert ChatService.get_presence_recipient_ids(self.user_b.id) == [self.user_a.id]
        with capture_on_commit_callbacks(execute=True):
            ChatService.leave_chat(self.chat, self.user_b)
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_b.id)) is None
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == []

    def test_direct_participant_deactivation_signal_invalidates_cache(self, capture_on_commit_callbacks):
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        assert ChatService.get_presence_recipient_ids(self.user_b.id) == [self.user_a.id]

        participant = ChatParticipant.objects.get(chat=self.chat, user=self.user_b)
        with capture_on_commit_callbacks(execute=True):
            participant.is_active = False
            participant.save(update_fields=['is_active'])

        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_b.id)) is None

    def test_participant_delete_signal_invalidates_cache(self, capture_on_commit_callbacks):
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        assert ChatService.get_presence_recipient_ids(self.user_b.id) == [self.user_a.id]

        participant = ChatParticipant.objects.get(chat=self.chat, user=self.user_b)
        with capture_on_commit_callbacks(execute=True):
            participant.delete()

        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_b.id)) is None

    def test_non_membership_save_does_not_bust_visibility_cache(self, capture_on_commit_callbacks):
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        key = OnlineStatusService._presence_recipients_key(self.user_a.id)
        participant = ChatParticipant.objects.get(chat=self.chat, user=self.user_b)

        with capture_on_commit_callbacks(execute=True):
            participant.is_muted = True
            participant.save(update_fields=['is_muted'])

        assert cache.get(key) == [self.user_b.id]

    def test_adding_participant_invalidates_existing_members_cache(self, capture_on_commit_callbacks):
        user_c = User.objects.create_user(username='c', email='c@example.com', password='x')
        ProjectMember.objects.create(user=user_c, project=self.project, role='Member', is_active=True)
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        with capture_on_commit_callbacks(execute=True):
            ChatService.add_participant(self.chat, user_c, added_by=self.user_a)
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None
        assert sorted(ChatService.get_presence_recipient_ids(self.user_a.id)) == sorted([self.user_b.id, user_c.id])

    def test_serializer_chat_create_invalidates_presence_cache(self, capture_on_commit_callbacks):
        user_c = User.objects.create_user(username='serializer-c', email='serializer-c@example.com', password='x')
        ProjectMember.objects.create(user=user_c, project=self.project, role='Member', is_active=True)
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        serializer = ChatCreateSerializer(data={'project': self.project.id, 'type': ChatType.GROUP, 'name': 'serializer channel', 'participant_ids': [user_c.id]}, context={'request': SimpleNamespace(user=self.user_a)})
        assert serializer.is_valid(), serializer.errors
        with capture_on_commit_callbacks(execute=True):
            serializer.save()
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None
        assert sorted(ChatService.get_presence_recipient_ids(self.user_a.id)) == sorted([self.user_b.id, user_c.id])

    def test_agent_private_chat_create_invalidates_presence_cache(self, capture_on_commit_callbacks):
        from agent.services import _get_or_create_bot_private_chat
        bot = User.objects.create_user(username=f'agent-bot-{self.user_a.id}', email=f'agent-bot-{self.user_a.id}@example.com', password='x')
        cache.set(OnlineStatusService._presence_recipients_key(bot.id), [self.user_b.id])
        cache.set(OnlineStatusService._presence_recipients_key(self.user_a.id), [self.user_b.id])
        with capture_on_commit_callbacks(execute=True):
            chat, created = _get_or_create_bot_private_chat(bot, self.user_a, self.project)
        assert created
        assert chat.type == ChatType.PRIVATE
        assert cache.get(OnlineStatusService._presence_recipients_key(bot.id)) is None
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None

    def test_agent_private_chat_reactivation_invalidates_presence_cache(self, capture_on_commit_callbacks):
        from agent.services import _get_or_create_bot_private_chat
        bot = User.objects.create_user(username=f'inactive-agent-bot-{self.user_a.id}', email=f'inactive-agent-bot-{self.user_a.id}@example.com', password='x')
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=bot, is_active=False)
        ChatParticipant.objects.create(chat=chat, user=self.user_a, is_active=False)
        cache.set(OnlineStatusService._presence_recipients_key(bot.id), [self.user_b.id])
        cache.set(OnlineStatusService._presence_recipients_key(self.user_a.id), [self.user_b.id])
        with capture_on_commit_callbacks(execute=True):
            returned_chat, created = _get_or_create_bot_private_chat(bot, self.user_a, self.project)
        assert not created
        assert returned_chat.id == chat.id
        assert ChatParticipant.objects.get(chat=chat, user=bot).is_active
        assert ChatParticipant.objects.get(chat=chat, user=self.user_a).is_active
        assert cache.get(OnlineStatusService._presence_recipients_key(bot.id)) is None
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None

    def test_cache_invalidation_never_falls_back_to_ttl(self, capture_on_commit_callbacks):
        assert ChatService.get_presence_recipient_ids(self.user_a.id) == [self.user_b.id]
        with capture_on_commit_callbacks(execute=True):
            ChatService.leave_chat(self.chat, self.user_b)
        assert cache.get(OnlineStatusService._presence_recipients_key(self.user_a.id)) is None

class TestMessageServiceIdempotentCreate:
    @pytest.fixture(autouse=True)
    def _setup(self):
        cache.clear()
        self.org = Organization.objects.create(name='Msg Org')
        self.project = Project.objects.create(name='Msg Project', organization=self.org)
        self.sender = User.objects.create_user(username='sender', email='sender@example.com', password='testpass123')
        self.recipient = User.objects.create_user(username='recipient', email='recipient@example.com', password='testpass123')
        for user in (self.sender, self.recipient):
            ProjectMember.objects.create(user=user, project=self.project, role='Member', is_active=True)
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.sender, is_active=True)
        ChatParticipant.objects.create(chat=self.chat, user=self.recipient, is_active=True)
        yield
        cache.clear()

    @patch('agent.tasks.handle_chat_message_for_agent.delay')
    def test_resolve_client_message_commits_does_not_query_per_message(
        self,
        mock_agent_route,
        capture_on_commit_callbacks,
        django_assert_max_num_queries,
    ):
        """Embedding message bodies in the ack must be prefetch-backed, not N+1.

        A per-message serializer would issue several extra queries per row
        (reactions, mentions, thread summary, ...). Prefetch keeps the count
        bounded regardless of batch size, so 4 rows stay well under the ceiling.
        """
        from chat.services import MessageService

        client_ids = []
        with capture_on_commit_callbacks(execute=True):
            for i in range(4):
                cid = f'qcount-{i}'
                MessageService.create_message_with_attachments(
                    sender=self.sender,
                    chat=self.chat,
                    content=f'message {i}',
                    client_message_id=cid,
                )
                client_ids.append(cid)

        # Measured baseline: 14 queries for 4 rows (prefetch-backed). Without
        # prefetch it would be ~32 (≈6 extra per row). 16 leaves small headroom
        # while still catching a per-message regression.
        with django_assert_max_num_queries(16):
            result = MessageService.resolve_client_message_commits(self.sender, client_ids)

        assert len(result) == 4
        assert all(row['message']['id'] for row in result)

    @patch('agent.tasks.handle_chat_message_for_agent.delay')
    def test_create_message_with_attachments_dedupes_by_client_message_id(
        self,
        mock_agent_route,
        capture_on_commit_callbacks,
    ):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import ChatOutboxEvent, Message, MessageAttachment, MessageStatus
        from chat.services import MessageService

        attachment = MessageAttachment.objects.create(
            message=None,
            uploader=self.sender,
            file=SimpleUploadedFile('note.txt', b'hello'),
            file_type='document',
            file_size=5,
            original_filename='note.txt',
            mime_type='text/plain',
        )
        client_message_id = 'client-msg-001'

        with capture_on_commit_callbacks(execute=True):
            first, created_first = MessageService.create_message_with_attachments(
                sender=self.sender,
                chat=self.chat,
                content='With attachment',
                attachment_ids=[attachment.id],
                client_message_id=client_message_id,
            )
        assert created_first is True
        assert first.client_message_id == client_message_id
        assert first.attachments.count() == 1

        with capture_on_commit_callbacks(execute=True):
            second, created_second = MessageService.create_message_with_attachments(
                sender=self.sender,
                chat=self.chat,
                content='With attachment',
                attachment_ids=[attachment.id],
                client_message_id=client_message_id,
            )
        assert created_second is False
        assert second.id == first.id
        assert Message.objects.filter(chat=self.chat, sender=self.sender).count() == 1
        assert MessageStatus.objects.filter(message=first).count() == 0
        assert set(
            ChatOutboxEvent.objects.filter(aggregate_id=first.id).values_list(
                'event_type', flat=True
            )
        ) == {
            ChatOutboxEvent.EVENT_MESSAGE_REALTIME,
            ChatOutboxEvent.EVENT_MESSAGE_NOTIFICATIONS,
        }
        mock_agent_route.assert_not_called()

    def test_create_without_client_message_id_always_creates(
        self,
        capture_on_commit_callbacks,
    ):
        from chat.models import ChatOutboxEvent, Message
        from chat.services import MessageService

        with capture_on_commit_callbacks(execute=True):
            first, created_first = MessageService.create_message_with_attachments(
                sender=self.sender,
                chat=self.chat,
                content='First',
            )
            second, created_second = MessageService.create_message_with_attachments(
                sender=self.sender,
                chat=self.chat,
                content='Second',
            )
        assert created_first is True
        assert created_second is True
        assert first.id != second.id
        assert Message.objects.filter(chat=self.chat, sender=self.sender).count() == 2
        assert ChatOutboxEvent.objects.filter(
            aggregate_id__in=[first.id, second.id]
        ).count() == 4

    def test_dedupe_via_integrity_error(
        self,
        capture_on_commit_callbacks,
    ):
        from django.db import IntegrityError
        from chat.models import ChatOutboxEvent, Message, MessageStatus
        from chat.services import MessageService

        client_message_id = 'integrity-error-client-001'
        existing = Message.objects.create(
            chat=self.chat,
            sender=self.sender,
            content='Already committed',
            client_message_id=client_message_id,
        )
        MessageStatus.objects.create(message=existing, user=self.recipient, status='sent')

        with patch.object(Message.objects, 'create', side_effect=IntegrityError('duplicate key')):
            with capture_on_commit_callbacks(execute=True):
                message, created = MessageService.create_message_with_attachments(
                    sender=self.sender,
                    chat=self.chat,
                    content='Retry',
                    client_message_id=client_message_id,
                )

        assert created is False
        assert message.id == existing.id
        assert Message.objects.filter(chat=self.chat, sender=self.sender).count() == 1
        assert not ChatOutboxEvent.objects.filter(aggregate_id=existing.id).exists()

    def test_non_dedupe_integrity_error_is_reraised(self):
        """A non-dedupe IntegrityError (a different constraint) must propagate,
        not be silently swallowed as a dedupe hit when no message with the key exists."""
        from django.db import IntegrityError
        from chat.models import Message
        from chat.services import MessageService

        client_message_id = 'genuine-db-error-001'
        # No existing message with this key, so the except-branch lookup misses;
        # the original IntegrityError must surface instead of a masked DoesNotExist.
        with patch.object(Message.objects, 'create', side_effect=IntegrityError('some other constraint')):
            with pytest.raises(IntegrityError):
                MessageService.create_message_with_attachments(
                    sender=self.sender,
                    chat=self.chat,
                    content='Retry',
                    client_message_id=client_message_id,
                )
        assert not Message.objects.filter(
            sender=self.sender, client_message_id=client_message_id
        ).exists()


class AttachmentMimeValidationTest(TestCase):
    def test_allows_supported_mime_types(self):
        allowed_types = [
            "image/png",
            "image/jpeg",
            "image/svg+xml",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ]

        for mime_type in allowed_types:
            with self.subTest(mime_type=mime_type):
                validate_attachment_mime_type(mime_type)

    def test_rejects_unsupported_mime_types(self):
        rejected_types = [
            "video/mp4",
            "audio/webm",
            "text/plain",
            "text/csv",
            "application/x-msdownload",
            "application/x-sh",
            "application/zip",
            "text/html",
            "",
        ]

        for mime_type in rejected_types:
            with self.subTest(mime_type=mime_type):
                with self.assertRaises(UnsupportedAttachmentMimeType):
                    validate_attachment_mime_type(mime_type)
