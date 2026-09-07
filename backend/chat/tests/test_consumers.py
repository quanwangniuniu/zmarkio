import asyncio
import logging
import json
import pytest
from contextlib import suppress
from datetime import timedelta
from unittest.mock import patch
from channels.testing import WebsocketCommunicator
from channels.routing import URLRouter
from channels.layers import channel_layers
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from core.models import Project, Organization, Team, TeamMember, ProjectMember
from chat.models import Chat, ChatParticipant, Message, MessageAttachment, MessageStatus, ChatType
from chat.consumers import ChatConsumer
from chat.services import OnlineStatusService
from chat.routing import websocket_urlpatterns
from asset.middleware import JWTAuthMiddleware
from rest_framework_simplejwt.tokens import AccessToken
pytestmark = pytest.mark.django_db
User = get_user_model()
logger = logging.getLogger(__name__)
TEST_CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
TEST_CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'chat-consumer-tests'}}

async def _disconnect_communicators(*communicators):
    for communicator in communicators:
        if communicator is None:
            continue
        with suppress(Exception):
            await communicator.disconnect(timeout=2)
        with suppress(Exception):
            communicator.stop(exceptions=False)
    await asyncio.sleep(0)

def _reset_channel_layers():
    # Just evict the cached layer instances so the next test gets a fresh one
    # bound to its own event loop.  Calling async_to_sync(layer.close)() from
    # a sync fixture that runs inside (or just after) an asyncio event loop can
    # deadlock because InMemoryChannelLayer's asyncio.Queue objects are bound to
    # the loop that created them.  Clearing the registry is sufficient — Python
    # GC reclaims the old layer and its queues once all references are dropped.
    cache = getattr(channel_layers, '_layers', None)
    if isinstance(cache, dict):
        cache.clear()

@pytest.fixture(autouse=True)
def reset_channel_layer_cache(settings):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS
    settings.CACHES = TEST_CACHES
    old_grace_seconds = OnlineStatusService.OFFLINE_GRACE_SECONDS
    OnlineStatusService.OFFLINE_GRACE_SECONDS = 0
    _reset_channel_layers()
    cache.clear()
    # Force all OnlineStatusService Redis calls to raise NotImplementedError so
    # they fall back to the LocMemCache set above.  This prevents tests from
    # hitting a real Redis server (or failing when Redis is unavailable) and
    # eliminates cross-worker data contamination when pytest-xdist is active.
    with patch('chat.services.get_redis_connection', side_effect=NotImplementedError):
        yield
    OnlineStatusService.OFFLINE_GRACE_SECONDS = old_grace_seconds
    cache.clear()
    _reset_channel_layers()

@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestChatConsumer:
    """Test ChatConsumer WebSocket functionality"""

    async def test_websocket_connect_authenticated(self, db):
        """Test WebSocket connection with authenticated user"""
        user = await self._create_user('testuser', 'test@example.com')
        token = str(AccessToken.for_user(user))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            connected, _ = await communicator.connect()
            assert connected
            snapshot = await communicator.receive_json_from(timeout=5)
            assert snapshot['type'] == 'presence_snapshot'
        finally:
            await _disconnect_communicators(communicator)

    async def test_websocket_connect_unauthenticated(self, db):
        """Test WebSocket connection without authentication fails"""
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, '/ws/chat/999/')
        try:
            connected, _ = await communicator.connect()
            assert not connected
        finally:
            await _disconnect_communicators(communicator)

    async def test_websocket_send_message(self, db):
        """Legacy WebSocket creation is rejected so REST/outbox cannot be bypassed."""
        user1 = await self._create_user('user1', 'user1@example.com')
        user2 = await self._create_user('user2', 'user2@example.com')
        org = await self._create_organization('Test Org')
        team = await self._create_team(org, 'Test Team')
        project = await self._create_project(org, 'Test Project')
        await self._add_team_member(user1, team, 'owner')
        await self._add_team_member(user2, team, 'member')
        await self._add_project_member(user1, project, 'owner')
        await self._add_project_member(user2, project, 'member')
        chat = await self._create_chat(project, ChatType.PRIVATE)
        await self._add_chat_participant(chat, user1)
        await self._add_chat_participant(chat, user2)
        token1 = str(AccessToken.for_user(user1))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator1 = WebsocketCommunicator(application, f'/ws/chat/{user1.id}/?token={token1}')
        try:
            connected, _ = await communicator1.connect()
            assert connected
            snapshot = await communicator1.receive_json_from(timeout=5)
            assert snapshot['type'] == 'presence_snapshot'
            await communicator1.send_json_to({'type': 'chat_message', 'chat_id': chat.id, 'content': 'Hello, this is a test message!'})
            response = await communicator1.receive_json_from(timeout=5)
            assert response == {
                'type': 'error',
                'message': 'WebSocket message creation is disabled; send messages through the REST API.',
            }
        finally:
            await _disconnect_communicators(communicator1)

    async def test_websocket_typing_indicator(self, db):
        """Test typing indicator via WebSocket"""
        user1 = await self._create_user('user1', 'user1@example.com')
        user2 = await self._create_user('user2', 'user2@example.com')
        org = await self._create_organization('Test Org')
        team = await self._create_team(org, 'Test Team')
        project = await self._create_project(org, 'Test Project')
        await self._add_team_member(user1, team, 'owner')
        await self._add_team_member(user2, team, 'member')
        await self._add_project_member(user1, project, 'owner')
        await self._add_project_member(user2, project, 'member')
        chat = await self._create_chat(project, ChatType.PRIVATE)
        await self._add_chat_participant(chat, user1)
        await self._add_chat_participant(chat, user2)
        token1 = str(AccessToken.for_user(user1))
        token2 = str(AccessToken.for_user(user2))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator1 = WebsocketCommunicator(application, f'/ws/chat/{user1.id}/?token={token1}')
        communicator2 = WebsocketCommunicator(application, f'/ws/chat/{user2.id}/?token={token2}')
        try:
            await communicator1.connect()
            snapshot1 = await communicator1.receive_json_from(timeout=5)
            assert snapshot1['type'] == 'presence_snapshot'
            await communicator2.connect()
            snapshot2 = await communicator2.receive_json_from(timeout=5)
            assert snapshot2['type'] == 'presence_snapshot'
            await communicator1.send_json_to({'type': 'typing_start', 'chat_id': chat.id})
            response = await communicator2.receive_json_from(timeout=5)
            assert response['type'] == 'typing_indicator'
            assert response['chat_id'] == chat.id
            assert response['user_id'] == user1.id
            assert response['is_typing'] is True
            await communicator1.send_json_to({'type': 'typing_stop', 'chat_id': chat.id})
            response = await communicator2.receive_json_from(timeout=5)
            assert response['type'] == 'typing_indicator'
            assert response['is_typing'] is False
        finally:
            await _disconnect_communicators(communicator1, communicator2)

    async def test_websocket_heartbeat(self, db):
        """Test heartbeat to keep connection alive"""
        user = await self._create_user('testuser', 'test@example.com')
        token = str(AccessToken.for_user(user))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            await communicator.connect()
            snapshot = await communicator.receive_json_from(timeout=5)
            assert snapshot['type'] == 'presence_snapshot'
            await communicator.send_json_to({'type': 'heartbeat'})
            response = await communicator.receive_json_from(timeout=5)
            assert response['type'] == 'pong'
            assert 'timestamp' in response
        finally:
            await _disconnect_communicators(communicator)

    async def test_multiple_websocket_connections_keep_user_online_until_last_disconnect(self, db):
        """Closing one tab should not mark the user offline while another tab is connected."""
        user = await self._create_user('multiuser', 'multi@example.com')
        token = str(AccessToken.for_user(user))
        await self._clear_presence_cache()
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator1 = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        communicator2 = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            connected1, _ = await communicator1.connect()
            assert connected1
            snapshot1 = await communicator1.receive_json_from(timeout=5)
            assert snapshot1['type'] == 'presence_snapshot'
            connected2, _ = await communicator2.connect()
            assert connected2
            snapshot2 = await communicator2.receive_json_from(timeout=5)
            assert snapshot2['type'] == 'presence_snapshot'
            assert await self._is_online(user.id)
            await _disconnect_communicators(communicator1)
            communicator1 = None
            assert await self._is_online(user.id)
            await _disconnect_communicators(communicator2)
            communicator2 = None
            assert not await self._is_online(user.id)
        finally:
            await _disconnect_communicators(communicator1, communicator2)

    async def test_presence_update_broadcasts_to_shared_chat_participants(self, db):
        """Shared chat participants should receive live online/offline updates."""
        user1 = await self._create_user('presence1', 'presence1@example.com')
        user2 = await self._create_user('presence2', 'presence2@example.com')
        org = await self._create_organization('Presence Org')
        project = await self._create_project(org, 'Presence Project')
        chat = await self._create_chat(project, ChatType.PRIVATE)
        await self._add_chat_participant(chat, user1)
        await self._add_chat_participant(chat, user2)
        await self._clear_presence_cache()
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator2 = WebsocketCommunicator(application, f'/ws/chat/{user2.id}/?token={str(AccessToken.for_user(user2))}')
        communicator1 = WebsocketCommunicator(application, f'/ws/chat/{user1.id}/?token={str(AccessToken.for_user(user1))}')
        try:
            connected2, _ = await communicator2.connect()
            assert connected2
            snapshot = await communicator2.receive_json_from(timeout=5)
            assert snapshot['type'] == 'presence_snapshot'
            connected1, _ = await communicator1.connect()
            assert connected1
            snapshot1 = await communicator1.receive_json_from(timeout=5)
            assert snapshot1['type'] == 'presence_snapshot'
            online_event = await communicator2.receive_json_from(timeout=5)
            assert online_event['type'] == 'presence_update'
            assert online_event['user_id'] == user1.id
            assert online_event['is_online'] is True
            await _disconnect_communicators(communicator1)
            communicator1 = None
            offline_event = await communicator2.receive_json_from(timeout=5)
            assert offline_event['type'] == 'presence_update'
            assert offline_event['user_id'] == user1.id
            assert offline_event['is_online'] is False
        finally:
            await _disconnect_communicators(communicator1, communicator2)

    async def test_presence_snapshot_includes_already_online_shared_users(self, db):
        """A newly connected user should immediately learn existing shared-user presence."""
        user1 = await self._create_user('snapshot1', 'snapshot1@example.com')
        user2 = await self._create_user('snapshot2', 'snapshot2@example.com')
        org = await self._create_organization('Snapshot Org')
        project = await self._create_project(org, 'Snapshot Project')
        chat = await self._create_chat(project, ChatType.PRIVATE)
        await self._add_chat_participant(chat, user1)
        await self._add_chat_participant(chat, user2)
        await self._clear_presence_cache()
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator1 = WebsocketCommunicator(application, f'/ws/chat/{user1.id}/?token={str(AccessToken.for_user(user1))}')
        communicator2 = WebsocketCommunicator(application, f'/ws/chat/{user2.id}/?token={str(AccessToken.for_user(user2))}')
        try:
            connected1, _ = await communicator1.connect()
            assert connected1
            snapshot1 = await communicator1.receive_json_from(timeout=5)
            assert snapshot1['type'] == 'presence_snapshot'
            connected2, _ = await communicator2.connect()
            assert connected2
            snapshot2 = await communicator2.receive_json_from(timeout=5)
            assert snapshot2['type'] == 'presence_snapshot'
            assert any((user['user_id'] == user1.id and user['is_online'] is True for user in snapshot2['users']))
        finally:
            await _disconnect_communicators(communicator1, communicator2)

    @staticmethod
    async def _create_user(username, email):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return User.objects.create_user(username=username, email=email, password='testpass123')
        return await create()

    @staticmethod
    async def _clear_presence_cache():
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def clear():
            cache.clear()
        await clear()

    @staticmethod
    async def _is_online(user_id):
        from channels.db import database_sync_to_async
        return await database_sync_to_async(OnlineStatusService.is_online)(user_id)

    @staticmethod
    async def _create_organization(name):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return Organization.objects.create(name=name)
        return await create()

    @staticmethod
    async def _create_team(org, name):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return Team.objects.create(organization=org, name=name)
        return await create()

    @staticmethod
    async def _create_project(org, name):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return Project.objects.create(organization=org, name=name)
        return await create()

    @staticmethod
    async def _add_team_member(user, team, role):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return TeamMember.objects.create(user=user, team=team)
        return await create()

    @staticmethod
    async def _add_project_member(user, project, role):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return ProjectMember.objects.create(user=user, project=project, role=role, is_active=True)
        return await create()

    @staticmethod
    async def _create_chat(project, chat_type):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return Chat.objects.create(project=project, type=chat_type)
        return await create()

    @staticmethod
    async def _add_chat_participant(chat, user):
        from channels.db import database_sync_to_async

        @database_sync_to_async
        def create():
            return ChatParticipant.objects.create(chat=chat, user=user, is_active=True)
        return await create()

    async def test_failed_chat_group_join_closes_the_socket(self, db, settings):
        """A socket that could not join its chat groups must be refused.

        While chat groups carry the messages, a connection that failed to join
        receives nothing — but it would go on to be marked online, so the
        fan-out claims it and marks messages delivered to somewhere
        unreachable. Refusing the connection is recoverable; a healthy-looking
        socket that silently drops messages is not.
        """
        settings.CHAT_CHANNEL_GROUPS_ENABLED = True

        user = await self._create_user('joinfail', 'joinfail@example.com')
        org = await self._create_organization('Join Fail Org')
        project = await self._create_project(org, 'Join Fail Project')
        chat = await self._create_chat(project, ChatType.GROUP)
        await self._add_chat_participant(chat, user)

        token = str(AccessToken.for_user(user))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            with patch(
                'chat.consumers.get_joinable_chat_ids',
                side_effect=RuntimeError('redis unavailable'),
            ):
                # The join happens before accept, so a failure refuses the
                # handshake outright rather than accepting a socket that cannot
                # receive the chats it is entitled to.
                connected, _ = await communicator.connect()
                assert not connected, (
                    'a socket that failed to join its chat groups was still accepted'
                )
        finally:
            await _disconnect_communicators(communicator)

    async def test_chat_group_access_is_revoked_on_removal(self, db, settings):
        """A socket that loses channel access must stop receiving it immediately.

        This is the security property behind chat-level groups: membership of
        the group *is* the entitlement, and a WebSocket can stay open for
        hours, so a removal that only takes effect on reconnect means the
        removed user keeps reading the channel until then.
        """
        from asgiref.sync import sync_to_async
        from channels.db import database_sync_to_async
        from channels.layers import get_channel_layer
        from chat.services import ChatService, chat_group_name

        settings.CHAT_CHANNEL_GROUPS_ENABLED = True

        user = await self._create_user('revokeduser', 'revoked@example.com')
        org = await self._create_organization('Revoke Org')
        project = await self._create_project(org, 'Revoke Project')
        chat = await self._create_chat(project, ChatType.GROUP)
        participant = await self._add_chat_participant(chat, user)

        token = str(AccessToken.for_user(user))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            connected, _ = await communicator.connect()
            assert connected
            await communicator.receive_json_from(timeout=5)  # presence_snapshot

            channel_layer = get_channel_layer()
            group = chat_group_name(chat.id)

            # While a member: the chat group reaches this socket.
            await channel_layer.group_send(
                group, {'type': 'chat_message', 'message': {'id': 1, 'content': 'before'}}
            )
            received = await communicator.receive_json_from(timeout=5)
            assert received['message']['content'] == 'before'

            # Remove them. Every membership mutator funnels through this hook,
            # which is what tells the live socket to re-derive its groups.
            @database_sync_to_async
            def remove():
                participant.is_active = False
                participant.save(update_fields=['is_active'])
                ChatService.invalidate_presence_recipients_for_chat(
                    chat, extra_user_ids=[user.id]
                )

            await remove()
            # Let the membership event reach the consumer and be acted on.
            await asyncio.sleep(0.5)

            # After removal the same publish must not reach this socket.
            await channel_layer.group_send(
                group, {'type': 'chat_message', 'message': {'id': 2, 'content': 'after'}}
            )
            assert await communicator.receive_nothing(timeout=2), (
                'a removed member still received the channel'
            )
        finally:
            await _disconnect_communicators(communicator)

    async def test_outbox_digest_returns_committed_client_message_ids(self, db):
        """Reconnect outbox_digest should ack server-committed client message ids."""
        from asgiref.sync import sync_to_async
        from chat.services import MessageService

        user = await self._create_user('outboxuser', 'outbox@example.com')
        org = await self._create_organization('Outbox Org')
        team = await self._create_team(org, 'Outbox Team')
        project = await self._create_project(org, 'Outbox Project')
        await self._add_team_member(user, team, 'owner')
        await self._add_project_member(user, project, 'owner')
        chat = await self._create_chat(project, ChatType.PRIVATE)
        await self._add_chat_participant(chat, user)
        client_message_id = 'ws-outbox-client-001'

        message, created = await sync_to_async(MessageService.create_message_with_attachments)(
            sender=user,
            chat=chat,
            content='Already committed',
            client_message_id=client_message_id,
        )
        assert created is True

        token = str(AccessToken.for_user(user))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            connected, _ = await communicator.connect()
            assert connected
            snapshot = await communicator.receive_json_from(timeout=5)
            assert snapshot['type'] == 'presence_snapshot'
            await communicator.send_json_to({
                'type': 'outbox_digest',
                'client_message_ids': [client_message_id, 'unknown-id'],
            })
            ack = await communicator.receive_json_from(timeout=5)
            assert ack['type'] == 'outbox_ack'
            assert len(ack['committed']) == 1
            assert ack['committed'][0]['client_message_id'] == client_message_id
            assert ack['committed'][0]['message_id'] == message.id
            # Solution 1: the ack embeds the fully-serialized message body so the
            # client can hydrate its outbox without a follow-up REST fetch per id.
            embedded = ack['committed'][0]['message']
            assert embedded['id'] == message.id
            assert embedded['content'] == 'Already committed'
        finally:
            await _disconnect_communicators(communicator)

    async def test_outbox_digest_caps_and_coerces_ids(self, db):
        """An oversized / non-string client_message_ids payload is bounded and
        coerced to strings, and the committed id (within the cap) is still acked."""
        from asgiref.sync import sync_to_async
        from chat.services import MessageService
        from chat.consumers import MAX_OUTBOX_DIGEST_IDS

        user = await self._create_user('capuser', 'cap@example.com')
        org = await self._create_organization('Cap Org')
        team = await self._create_team(org, 'Cap Team')
        project = await self._create_project(org, 'Cap Project')
        await self._add_team_member(user, team, 'owner')
        await self._add_project_member(user, project, 'owner')
        chat = await self._create_chat(project, ChatType.PRIVATE)
        await self._add_chat_participant(chat, user)
        client_message_id = 'ws-cap-client-001'

        message, created = await sync_to_async(MessageService.create_message_with_attachments)(
            sender=user, chat=chat, content='Committed', client_message_id=client_message_id,
        )
        assert created is True

        token = str(AccessToken.for_user(user))
        application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))
        communicator = WebsocketCommunicator(application, f'/ws/chat/{user.id}/?token={token}')
        try:
            connected, _ = await communicator.connect()
            assert connected
            snapshot = await communicator.receive_json_from(timeout=5)
            assert snapshot['type'] == 'presence_snapshot'
            # Committed id first, a non-string id, then more filler ids than the cap.
            oversized = [client_message_id, 12345] + [
                f'filler-{i}' for i in range(MAX_OUTBOX_DIGEST_IDS + 50)
            ]
            await communicator.send_json_to({
                'type': 'outbox_digest',
                'client_message_ids': oversized,
            })
            ack = await communicator.receive_json_from(timeout=5)
            assert ack['type'] == 'outbox_ack'
            assert any(
                c['client_message_id'] == client_message_id for c in ack['committed']
            )
        finally:
            await _disconnect_communicators(communicator)

class TestChatConsumerSync:
    """Synchronous tests for ChatConsumer."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='testpass123')
        self.organization = Organization.objects.create(name='Test Org')
        self.team = Team.objects.create(organization=self.organization, name='Test Team')
        self.project = Project.objects.create(organization=self.organization, name='Test Project')
        TeamMember.objects.create(user=self.user, team=self.team)
        ProjectMember.objects.create(user=self.user, project=self.project, role='Team Leader', is_active=True)
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user, is_active=True)

    def test_consumer_initialization(self):
        """Test consumer can be instantiated"""
        consumer = ChatConsumer()
        assert consumer is not None

    def test_get_chat_participants(self):
        """Test getting chat participants"""
        user2 = User.objects.create_user(username='user2', email='user2@example.com', password='testpass123')
        ChatParticipant.objects.create(chat=self.chat, user=user2, is_active=True)
        consumer = ChatConsumer()
        consumer.user = self.user
        participants = consumer.get_chat_participants(self.chat.id)
        assert len(participants) == 2
        assert self.user.id in participants
        assert user2.id in participants

    def test_get_chat_participants_exclude_user(self):
        """Test getting chat participants excluding a specific user"""
        user2 = User.objects.create_user(username='user2', email='user2@example.com', password='testpass123')
        ChatParticipant.objects.create(chat=self.chat, user=user2, is_active=True)
        consumer = ChatConsumer()
        consumer.user = self.user
        participants = consumer.get_chat_participants(self.chat.id, exclude_user_id=self.user.id)
        assert len(participants) == 1
        assert self.user.id not in participants
        assert user2.id in participants

    def test_get_queued_messages_includes_attachments_and_forward_metadata(self):
        """Queued websocket payload should include attachments and structured forward fields."""
        sender = User.objects.create_user(username='sender', email='sender@example.com', password='testpass123')
        ChatParticipant.objects.create(chat=self.chat, user=sender, is_active=True)
        source_message = Message.objects.create(chat=self.chat, sender=sender, content='Original source')
        forwarded_message = Message.objects.create(chat=self.chat, sender=sender, content='Forwarded payload', has_attachments=True, forwarded_from_message=source_message, forwarded_from_sender_display=sender.username, forwarded_from_created_at=source_message.created_at)
        MessageAttachment.objects.create(message=forwarded_message, uploader=sender, file=SimpleUploadedFile('queued.txt', b'queued content'), file_type='document', file_size=14, original_filename='queued.txt', mime_type='text/plain')
        MessageStatus.objects.create(message=forwarded_message, user=self.user, status='sent')
        consumer = ChatConsumer()
        consumer.user = self.user
        queued_messages = consumer.get_queued_messages()
        assert len(queued_messages) == 1
        payload = queued_messages[0]
        assert payload['seq'] == forwarded_message.seq
        assert payload['has_attachments']
        assert payload['attachment_count'] == 1
        assert len(payload['attachments']) == 1
        assert payload['attachments'][0]['original_filename'] == 'queued.txt'
        assert payload['is_forwarded']
        assert payload['forwarded_from'] is not None
        assert payload['forwarded_from']['sender_display'] == sender.username

    def test_recently_delivered_replay_covers_the_expiry_window(self):
        """A message marked delivered is replayed until it falls out of the window.

        Delivery is recorded when a message is handed to the channel layer, so a
        publish the channel layer later discards leaves a row saying delivered
        that no other recovery path will look at again.
        """
        sender = User.objects.create_user(
            username='sender3', email='sender3@example.com', password='testpass123'
        )
        ChatParticipant.objects.create(chat=self.chat, user=sender, is_active=True)
        consumer = ChatConsumer()
        consumer.user = self.user
        window = settings.CHAT_RECONNECT_REPLAY_SECONDS

        recent = Message.objects.create(chat=self.chat, sender=sender, content='recent')
        MessageStatus.objects.create(
            message=recent,
            user=self.user,
            status='delivered',
            delivered_at=timezone.now() - timedelta(seconds=window // 2),
        )
        stale = Message.objects.create(chat=self.chat, sender=sender, content='stale')
        MessageStatus.objects.create(
            message=stale,
            user=self.user,
            status='delivered',
            delivered_at=timezone.now() - timedelta(seconds=window + 60),
        )

        contents = [
            payload['content']
            for payload in consumer.get_recently_delivered_messages()
        ]
        assert contents == ['recent']

    def test_get_queued_messages_includes_attachment_fields_for_plain_message(self):
        """Queued payload should keep attachment fields even for plain text messages."""
        sender = User.objects.create_user(username='sender2', email='sender2@example.com', password='testpass123')
        ChatParticipant.objects.create(chat=self.chat, user=sender, is_active=True)
        message = Message.objects.create(chat=self.chat, sender=sender, content='Hello queued')
        MessageStatus.objects.create(message=message, user=self.user, status='sent')
        consumer = ChatConsumer()
        consumer.user = self.user
        queued_messages = consumer.get_queued_messages()
        assert len(queued_messages) == 1
        payload = queued_messages[0]
        assert not payload['has_attachments']
        assert payload['attachment_count'] == 0
        assert payload['attachments'] == []
        assert not payload['is_forwarded']
        assert payload['forwarded_from'] is None
