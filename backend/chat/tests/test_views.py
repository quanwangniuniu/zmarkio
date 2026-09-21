import pytest
import logging
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from core.models import Project, Organization, Team, TeamMember, ProjectMember
from chat.models import Chat, ChatOutboxEvent, ChatParticipant, ChatStar, Message, MessageAttachment, MessageStatus, ChatType, ChannelVisibility
from chat.services import MessageService
from chat.serializers import MessageSerializer
from notifications.models import Notification, NotificationEventType
from django.core.files.uploadedfile import SimpleUploadedFile
pytestmark = pytest.mark.django_db
User = get_user_model()
logger = logging.getLogger(__name__)

class TestChatAPI:
    """Test Chat API endpoints"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.user1 = User.objects.create_user(email='user1@example.com', username='user1', password='testpass123')
        self.user2 = User.objects.create_user(email='user2@example.com', username='user2', password='testpass123')
        self.user3 = User.objects.create_user(email='user3@example.com', username='user3', password='testpass123')
        self.organization = Organization.objects.create(name='Test Organization')
        self.team = Team.objects.create(organization=self.organization, name='Test Team')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        TeamMember.objects.create(user=self.user1, team=self.team)
        TeamMember.objects.create(user=self.user2, team=self.team)
        ProjectMember.objects.create(user=self.user1, project=self.project, role='Team Leader', is_active=True)
        ProjectMember.objects.create(user=self.user2, project=self.project, role='member', is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def test_create_private_chat(self):
        """Test creating a private chat"""
        url = reverse('chat-list')
        data = {'project': self.project.id, 'type': ChatType.PRIVATE, 'participant_ids': [self.user2.id]}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['type'] == ChatType.PRIVATE
        assert response.data['project'] == self.project.id
        assert len(response.data['participants']) == 2
        chat = Chat.objects.get(id=response.data['id'])
        assert chat.type == ChatType.PRIVATE
        assert chat.participants.filter(is_active=True).count() == 2

    def test_create_group_chat(self):
        """Test creating a group chat"""
        ProjectMember.objects.create(user=self.user3, project=self.project, role='member', is_active=True)
        url = reverse('chat-list')
        data = {'project': self.project.id, 'type': ChatType.GROUP, 'name': 'Test Group', 'participant_ids': [self.user2.id, self.user3.id]}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['type'] == ChatType.GROUP
        assert response.data['name'] == 'Test Group'
        assert len(response.data['participants']) == 3

    def test_create_group_chat_without_name(self):
        """Test creating a group chat without a name fails"""
        url = reverse('chat-list')
        data = {'project': self.project.id, 'type': ChatType.GROUP, 'participant_ids': [self.user2.id]}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'name' in str(response.data)

    def test_create_private_chat_with_non_project_member(self):
        """Test creating a private chat with user not in project fails"""
        url = reverse('chat-list')
        data = {'project': self.project.id, 'type': ChatType.PRIVATE, 'participant_ids': [self.user3.id]}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_chats(self):
        """Test listing user's chats"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-list')
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) >= 1

    def test_retrieve_chat(self):
        """Test retrieving a specific chat"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-detail', kwargs={'slug': chat.slug})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == chat.id
        assert len(response.data['participants']) == 2

    def test_retrieve_chat_excludes_inactive_participants(self):
        """Chat details should not return removed participants."""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group')
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user3, is_active=False)
        url = reverse('chat-detail', kwargs={'slug': chat.slug})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        participant_user_ids = {p['user']['id'] for p in response.data['participants']}
        assert participant_user_ids == {self.user1.id, self.user2.id}

    def test_retrieve_chat_without_permission(self):
        """Test retrieving a chat user is not part of fails"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user3, is_active=True)
        url = reverse('chat-detail', kwargs={'slug': chat.slug})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_add_participant_to_group_chat(self):
        """Test adding a participant to a group chat"""
        ProjectMember.objects.create(user=self.user3, project=self.project, role='member', is_active=True)
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group')
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-add-participant', kwargs={'slug': chat.slug})
        data = {'user_id': self.user3.id}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert chat.participants.filter(is_active=True).count() == 3

    def test_add_participant_to_private_chat_fails(self):
        """Test adding a participant to a private chat fails"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-add-participant', kwargs={'slug': chat.slug})
        data = {'user_id': self.user3.id}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_remove_participant_from_group_chat(self):
        """Test removing a participant from a group chat"""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group')
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user3, is_active=True)
        url = reverse('chat-remove-participant', kwargs={'slug': chat.slug})
        data = {'user_id': self.user3.id}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_204_NO_CONTENT
        participant = ChatParticipant.objects.get(chat=chat, user=self.user3)
        assert not participant.is_active

    def test_manager_can_promote_and_demote_channel_manager(self):
        """Managers can manage the channel manager list."""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group', created_by=self.user1)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True, is_manager=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-set-manager', kwargs={'slug': chat.slug})
        promote = self.client.patch(url, {'user_id': self.user2.id, 'is_manager': True}, format='json')
        assert promote.status_code == status.HTTP_200_OK
        assert ChatParticipant.objects.get(chat=chat, user=self.user2).is_manager
        demote = self.client.patch(url, {'user_id': self.user2.id, 'is_manager': False}, format='json')
        assert demote.status_code == status.HTTP_200_OK
        assert not ChatParticipant.objects.get(chat=chat, user=self.user2).is_manager

    def test_legacy_manager_can_demote_then_remove_member(self):
        """Fallback managers should stay explicit after assigning managers."""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Legacy Group')
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        manager_url = reverse('chat-set-manager', kwargs={'slug': chat.slug})
        promote = self.client.patch(manager_url, {'user_id': self.user2.id, 'is_manager': True}, format='json')
        assert promote.status_code == status.HTTP_200_OK
        assert ChatParticipant.objects.get(chat=chat, user=self.user1).is_manager
        assert ChatParticipant.objects.get(chat=chat, user=self.user2).is_manager
        demote = self.client.patch(manager_url, {'user_id': self.user2.id, 'is_manager': False}, format='json')
        assert demote.status_code == status.HTTP_200_OK
        assert ChatParticipant.objects.get(chat=chat, user=self.user1).is_manager
        assert not ChatParticipant.objects.get(chat=chat, user=self.user2).is_manager
        remove_url = reverse('chat-remove-participant', kwargs={'slug': chat.slug})
        remove = self.client.post(remove_url, {'user_id': self.user2.id}, format='json')
        assert remove.status_code == status.HTTP_204_NO_CONTENT
        assert not ChatParticipant.objects.get(chat=chat, user=self.user2).is_active

    def test_non_manager_cannot_promote_channel_manager(self):
        """Regular channel members cannot see/use manager controls server-side."""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group', created_by=self.user1)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True, is_manager=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        self.client.force_authenticate(user=self.user2)
        url = reverse('chat-set-manager', kwargs={'slug': chat.slug})
        response = self.client.patch(url, {'user_id': self.user2.id, 'is_manager': True}, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_manager_invite_channel_only_allows_managers_to_add_members(self):
        """Restricted channels let managers add people, but block regular members."""
        ProjectMember.objects.create(user=self.user3, project=self.project, role='member', is_active=True)
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Restricted Group', created_by=self.user1, visibility=ChannelVisibility.MANAGER_INVITE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True, is_manager=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-add-participant', kwargs={'slug': chat.slug})
        self.client.force_authenticate(user=self.user2)
        blocked = self.client.post(url, {'user_id': self.user3.id}, format='json')
        assert blocked.status_code == status.HTTP_400_BAD_REQUEST
        self.client.force_authenticate(user=self.user1)
        allowed = self.client.post(url, {'user_id': self.user3.id}, format='json')
        assert allowed.status_code == status.HTTP_201_CREATED

    def test_browse_channels_only_returns_public_project_channels(self):
        """Browse exposes only public project channels."""
        public_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Public Group', visibility=ChannelVisibility.PUBLIC)
        hidden_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Hidden Group', visibility=ChannelVisibility.MEMBER_INVITE)
        ChatParticipant.objects.create(chat=public_chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=hidden_chat, user=self.user1, is_active=True)
        url = reverse('chat-browse')
        response = self.client.get(url, {'project_id': self.project.slug})
        assert response.status_code == status.HTTP_200_OK
        names = {row['name'] for row in response.data}
        assert 'Public Group' in names
        assert 'Hidden Group' not in names
        public_row = next(row for row in response.data if row['name'] == 'Public Group')
        assert public_row['slug'] == public_chat.slug
        self.client.force_authenticate(user=self.user3)
        forbidden = self.client.get(url, {'project_id': self.project.slug})
        assert forbidden.status_code == status.HTTP_403_FORBIDDEN

    def test_leave_chat(self):
        """Test user leaving a chat"""
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Test Group')
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-detail', kwargs={'slug': chat.slug})
        response = self.client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        participant = ChatParticipant.objects.get(chat=chat, user=self.user1)
        assert not participant.is_active

    def test_leave_private_chat(self):
        """Test user leaving a private chat"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        url = reverse('chat-detail', kwargs={'slug': chat.slug})
        response = self.client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        user1_participant = ChatParticipant.objects.get(chat=chat, user=self.user1)
        user2_participant = ChatParticipant.objects.get(chat=chat, user=self.user2)
        assert not user1_participant.is_active
        assert user2_participant.is_active

    def test_mark_chat_as_read(self):
        """Test marking all messages in a chat as read"""
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        message = Message.objects.create(chat=chat, sender=self.user2, content='Test message')
        MessageStatus.objects.create(message=message, user=self.user1, status='sent')
        url = reverse('chat-mark-as-read', kwargs={'slug': chat.slug})
        response = self.client.post(url, {}, format='json')
        assert response.status_code == status.HTTP_200_OK
        msg_status = MessageStatus.objects.get(message=message, user=self.user1)
        assert msg_status.status == 'read'
        assert msg_status.read_at is not None

class TestMessageAPI:
    """Test Message API endpoints"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.user1 = User.objects.create_user(email='user1@example.com', username='user1', password='testpass123')
        self.user2 = User.objects.create_user(email='user2@example.com', username='user2', password='testpass123')
        self.organization = Organization.objects.create(name='Test Organization')
        self.team = Team.objects.create(organization=self.organization, name='Test Team')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        TeamMember.objects.create(user=self.user1, team=self.team)
        TeamMember.objects.create(user=self.user2, team=self.team)
        ProjectMember.objects.create(user=self.user1, project=self.project, role='Team Leader', is_active=True)
        ProjectMember.objects.create(user=self.user2, project=self.project, role='member', is_active=True)
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=self.chat, user=self.user2, is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def test_send_message(self):
        """Test sending a message"""
        url = reverse('message-list')
        data = {'chat': self.chat.id, 'content': 'Hello, this is a test message!'}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['content'] == 'Hello, this is a test message!'
        assert response.data['sender']['id'] == self.user1.id
        assert response.data['chat'] == self.chat.id
        message = Message.objects.get(id=response.data['id'])
        assert message.content == 'Hello, this is a test message!'
        assert message.sender == self.user1
        msg_status = MessageStatus.objects.filter(message=message, user=self.user2)
        # Recipient fan-out is intentionally deferred to the durable realtime
        # outbox task so the HTTP write cost is independent of channel size.
        assert msg_status.count() == 0
        assert ChatOutboxEvent.objects.filter(aggregate_id=message.id).count() == 2

    def test_send_message_is_scoped_throttled(self):
        """Message writes should be rate-limited without throttling reads."""
        cache.clear()
        url = reverse('message-list')
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'chat_message_write': '1/minute', 'chat_reaction': '120/minute'}):
            first = self.client.post(url, {'chat': self.chat.id, 'content': 'First'}, format='json')
            second = self.client.post(url, {'chat': self.chat.id, 'content': 'Second'}, format='json')
            read = self.client.get(url, {'chat_id': self.chat.id})
        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert read.status_code == status.HTTP_200_OK

    def test_send_rich_message_with_mention_queues_notification_fanout(self, capture_on_commit_callbacks):
        """Sending a rich @mention stores mention data and queues async notification fanout."""
        url = reverse('message-list')
        rich_body = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Hi '}, {'type': 'mention', 'attrs': {'id': self.user2.id, 'label': self.user2.username}}]}]}
        data = {'chat': self.chat.id, 'content': '', 'rich_body': rich_body, 'mention_ids': [self.user2.id]}
        with capture_on_commit_callbacks(execute=True):
            response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['rich_body'] == rich_body
        assert response.data['content'] == 'Hi @user2'
        assert response.data['mentioned_user_ids'] == [self.user2.id]
        message = Message.objects.get(id=response.data['id'])
        assert message.mentions.get().mentioned_user_id == self.user2.id
        assert set(
            ChatOutboxEvent.objects.filter(aggregate_id=message.id).values_list(
                'event_type', flat=True
            )
        ) == {
            ChatOutboxEvent.EVENT_MESSAGE_REALTIME,
            ChatOutboxEvent.EVENT_MESSAGE_NOTIFICATIONS,
        }

    def test_notify_message_recipients_creates_chat_mention_notification(self):
        """The async fanout task creates persisted mention notifications."""
        from chat.tasks import notify_message_recipients
        message = Message.objects.create(chat=self.chat, sender=self.user1, content='Hi @user2')
        message.mentions.create(mentioned_user=self.user2)
        notify_message_recipients.run(message.id)
        mention_notification = Notification.objects.get(recipient=self.user2, event_type=NotificationEventType.CHAT_MENTION)
        assert mention_notification.metadata['chat_id'] == self.chat.id
        assert mention_notification.metadata['message_id'] == message.id
        assert f'messageId={message.id}' in mention_notification.action_url

    def test_notify_message_recipients_ignores_mentions_outside_chat(self):
        """Mention fanout must not notify users who are not active chat participants."""
        from chat.tasks import notify_message_recipients
        outsider = User.objects.create_user(email='outsider@example.com', username='outsider', password='testpass123')
        message = Message.objects.create(chat=self.chat, sender=self.user1, content='Hi @outsider')
        message.mentions.create(mentioned_user=outsider)
        notify_message_recipients.run(message.id)
        assert not Notification.objects.filter(recipient=outsider, event_type=NotificationEventType.CHAT_MENTION).exists()


    def test_send_message_dedupes_by_client_message_id(self, capture_on_commit_callbacks):
        """Retried REST sends with the same client_message_id must not duplicate side effects."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import Message, MessageAttachment, MessageStatus

        attachment = MessageAttachment.objects.create(
            uploader=self.user1,
            file=SimpleUploadedFile('retry.txt', b'payload'),
            file_type='document',
            file_size=7,
            original_filename='retry.txt',
            mime_type='text/plain',
            message=None,
        )
        client_message_id = 'view-client-msg-001'
        url = reverse('message-list')
        payload = {
            'chat': self.chat.id,
            'content': 'Retry me',
            'attachment_ids': [attachment.id],
            'client_message_id': client_message_id,
        }
        with capture_on_commit_callbacks(execute=True):
            first = self.client.post(url, payload, format='json')
            second = self.client.post(url, payload, format='json')
        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_200_OK
        assert second.data['id'] == first.data['id']
        assert len(second.data['attachments']) == 1
        assert Message.objects.filter(chat=self.chat, sender=self.user1).count() == 1
        assert MessageStatus.objects.filter(message_id=first.data['id']).count() == 0
        assert ChatOutboxEvent.objects.filter(aggregate_id=first.data['id']).count() == 2
        assert Message.objects.get(
            sender=self.user1,
            client_message_id=client_message_id,
        ).id == first.data['id']

    def test_send_empty_message_fails(self):
        """Test sending an empty message fails"""
        url = reverse('message-list')
        data = {'chat': self.chat.id, 'content': ''}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_send_message_to_unauthorized_chat(self):
        """Test sending a message to a chat user is not part of fails"""
        other_chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=other_chat, user=self.user2, is_active=True)
        url = reverse('message-list')
        data = {'chat': other_chat.id, 'content': 'Hello!'}
        response = self.client.post(url, data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_messages(self):
        """Test listing messages for a chat"""
        Message.objects.create(chat=self.chat, sender=self.user1, content='Message 1')
        Message.objects.create(chat=self.chat, sender=self.user2, content='Message 2')
        Message.objects.create(chat=self.chat, sender=self.user1, content='Message 3')
        url = reverse('message-list')
        response = self.client.get(url, {'chat_id': self.chat.id})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 3

    def test_list_messages_without_chat_id(self):
        """Test listing messages without chat_id fails"""
        url = reverse('message-list')
        response = self.client.get(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_messages_with_cursor_pagination(self):
        """Test cursor-based pagination for messages"""
        msg1 = Message.objects.create(chat=self.chat, sender=self.user1, content='Message 1')
        msg2 = Message.objects.create(chat=self.chat, sender=self.user2, content='Message 2')
        msg3 = Message.objects.create(chat=self.chat, sender=self.user1, content='Message 3')
        url = reverse('message-list')
        response = self.client.get(url, {'chat_id': self.chat.id, 'before': msg3.created_at.isoformat(), 'page_size': 2})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) == 2

    def test_search_messages_supports_keyset_cursor(self):
        """Search pagination should use next_cursor without duplicating rows."""
        older = Message.objects.create(chat=self.chat, sender=self.user1, content='older')
        middle = Message.objects.create(chat=self.chat, sender=self.user1, content='middle')
        newer = Message.objects.create(chat=self.chat, sender=self.user1, content='newer')
        url = reverse('message-search')
        first = self.client.get(url, {'from_user': self.user1.username, 'limit': 2})
        assert first.status_code == status.HTTP_200_OK
        assert [row['id'] for row in first.data['results']] == [newer.id, middle.id]
        assert first.data['next_cursor'] is not None
        second = self.client.get(url, {'from_user': self.user1.username, 'limit': 2, 'cursor': first.data['next_cursor']})
        assert second.status_code == status.HTTP_200_OK
        assert [row['id'] for row in second.data['results']] == [older.id]
        assert second.data['next_cursor'] is None

    def test_search_messages_rejects_invalid_cursor(self):
        """Bad cursors should fail clearly instead of falling back to offset."""
        url = reverse('message-search')
        response = self.client.get(url, {'from_user': self.user1.username, 'limit': 2, 'cursor': 'not-a-cursor'})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == 'Invalid cursor'

    def test_retrieve_message(self):
        """Test retrieving a specific message"""
        message = Message.objects.create(chat=self.chat, sender=self.user1, content='Test message')
        url = reverse('message-detail', kwargs={'pk': message.id})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == message.id
        assert response.data['content'] == 'Test message'

    def test_delete_message_soft_deletes_and_returns_tombstone(self):
        """Deleting a message keeps a timeline tombstone instead of removing the row."""
        message = Message.objects.create(chat=self.chat, sender=self.user1, content='Secret launch plan', rich_body={'type': 'doc', 'content': [{'type': 'paragraph'}]}, has_attachments=True)
        MessageAttachment.objects.create(uploader=self.user1, message=message, file=SimpleUploadedFile('secret.txt', b'content'), file_type='document', file_size=7, original_filename='secret.txt', mime_type='text/plain')
        url = reverse('message-detail', kwargs={'pk': message.id})
        response = self.client.delete(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'deleted'
        assert response.data['message']['id'] == message.id
        assert response.data['message']['is_deleted']
        assert response.data['message']['content'] == ''
        assert response.data['message']['rich_body'] is None
        assert response.data['message']['attachments'] == []
        assert response.data['message']['attachment_count'] == 0
        message.refresh_from_db()
        assert message.is_deleted
        assert message.content == ''
        assert message.rich_body is None
        assert message.deleted_at is not None

    def test_list_messages_includes_deleted_tombstone(self):
        """Message history should still include soft-deleted messages."""
        deleted_message = Message.objects.create(chat=self.chat, sender=self.user1, content='', is_deleted=True, deleted_at=timezone.now())
        Message.objects.create(chat=self.chat, sender=self.user2, content='Still here')
        url = reverse('message-list')
        response = self.client.get(url, {'chat_id': self.chat.id})
        assert response.status_code == status.HTTP_200_OK
        ids = [message['id'] for message in response.data['results']]
        assert deleted_message.id in ids
        tombstone = next((message for message in response.data['results'] if message['id'] == deleted_message.id))
        assert tombstone['is_deleted']
        assert tombstone['content'] == ''
        assert tombstone['attachments'] == []

    def test_mark_message_as_read(self):
        """Test marking a message as read"""
        message = Message.objects.create(chat=self.chat, sender=self.user2, content='Test message')
        MessageStatus.objects.create(message=message, user=self.user1, status='sent')
        url = reverse('message-mark-as-read', kwargs={'pk': message.id})
        response = self.client.post(url, {}, format='json')
        assert response.status_code == status.HTTP_200_OK
        msg_status = MessageStatus.objects.get(message=message, user=self.user1)
        assert msg_status.status == 'read'
        assert msg_status.read_at is not None

    def test_thread_replies_endpoint_filters_cross_chat_rows(self):
        """Defensive filter: a corrupted cross-chat parent link must not leak into a thread."""
        root = Message.objects.create(chat=self.chat, sender=self.user1, content='Root')
        same_chat_reply = Message.objects.create(chat=self.chat, sender=self.user2, content='Visible reply', parent_message=root)
        other_chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=other_chat, user=self.user2, is_active=True)
        Message.objects.create(chat=other_chat, sender=self.user2, content='Should not leak', parent_message=root)
        url = reverse('message-thread-replies', kwargs={'pk': root.id})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        ids = [message['id'] for message in response.data['results']]
        assert ids == [same_chat_reply.id]

    def test_get_unread_count(self):
        """Test getting unread message count"""
        msg1 = Message.objects.create(chat=self.chat, sender=self.user2, content='Message 1')
        msg2 = Message.objects.create(chat=self.chat, sender=self.user2, content='Message 2')
        MessageStatus.objects.create(message=msg1, user=self.user1, status='sent')
        MessageStatus.objects.create(message=msg2, user=self.user1, status='sent')
        url = reverse('message-unread-count')
        response = self.client.get(url, {'chat_id': self.chat.id})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['unread_count'] == 2

    def test_forward_batch_success_multi_messages_multi_targets(self):
        """Test forwarding multiple messages to existing chat + member target."""
        user3 = User.objects.create_user(email='user3@example.com', username='user3', password='testpass123')
        TeamMember.objects.create(user=user3, team=self.team)
        ProjectMember.objects.create(user=user3, project=self.project, role='member', is_active=True)
        target_group = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Target Group')
        ChatParticipant.objects.create(chat=target_group, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=target_group, user=self.user2, is_active=True)
        source_msg_1 = Message.objects.create(chat=self.chat, sender=self.user2, content='Source 1')
        source_msg_2 = Message.objects.create(chat=self.chat, sender=self.user1, content='Source 2')
        url = reverse('message-forward-batch')
        payload = {'source_chat_id': self.chat.id, 'source_message_ids': [source_msg_1.id, source_msg_2.id], 'target_chat_ids': [target_group.id], 'target_user_ids': [user3.id]}
        response = self.client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert response.data['summary']['requested_messages'] == 2
        assert response.data['summary']['forwardable_messages'] == 2
        assert response.data['summary']['target_chats'] == 2
        assert response.data['summary']['succeeded_sends'] == 4
        assert response.data['summary']['failed_sends'] == 0
        assert response.data['resolved']['skipped_message_ids'] == []
        group_messages = list(Message.objects.filter(chat=target_group, sender=self.user1).order_by('created_at'))
        assert len(group_messages) == 2
        assert group_messages[0].content == 'Source 1'
        assert group_messages[1].content == 'Source 2'
        assert group_messages[0].forwarded_from_message_id == source_msg_1.id
        assert group_messages[1].forwarded_from_message_id == source_msg_2.id
        assert group_messages[0].forwarded_from_sender_display == self.user2.username
        assert group_messages[1].forwarded_from_sender_display == self.user1.username
        assert group_messages[0].forwarded_from_created_at is not None
        assert group_messages[1].forwarded_from_created_at is not None
        user3_private_chat = Chat.objects.filter(project=self.project, type=ChatType.PRIVATE, participants__user=self.user1, participants__is_active=True).filter(participants__user=user3, participants__is_active=True).distinct().first()
        assert user3_private_chat is not None
        assert Message.objects.filter(chat=user3_private_chat, sender=self.user1).count() == 2
        assert ChatOutboxEvent.objects.filter(
            event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
        ).count() == 4

    def test_forward_batch_partial_success_with_invalid_target_chat(self):
        """Test partial success when one target chat is invalid for the sender."""
        valid_target = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Valid Target')
        ChatParticipant.objects.create(chat=valid_target, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=valid_target, user=self.user2, is_active=True)
        invalid_target = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Invalid Target')
        ChatParticipant.objects.create(chat=invalid_target, user=self.user2, is_active=True)
        source_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Forward me')
        url = reverse('message-forward-batch')
        payload = {'source_chat_id': self.chat.id, 'source_message_ids': [source_message.id], 'target_chat_ids': [valid_target.id, invalid_target.id], 'target_user_ids': []}
        response = self.client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'partial_success'
        assert response.data['summary']['succeeded_sends'] == 1
        assert response.data['summary']['failed_sends'] >= 1
        failure_reasons = {item['reason'] for item in response.data['failures']}
        assert 'not_participant' in failure_reasons
        assert Message.objects.filter(chat=valid_target, sender=self.user1).exists()
        assert ChatOutboxEvent.objects.filter(
            event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
        ).count() == 1

    def test_forward_batch_forwards_text_and_attachment_messages(self):
        """Text and attachment messages should both be forwardable."""
        target_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Target Group')
        ChatParticipant.objects.create(chat=target_chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=target_chat, user=self.user2, is_active=True)
        text_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Forward this text')
        attachment_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Has attachment')
        source_attachment = MessageAttachment.objects.create(uploader=self.user2, message=attachment_message, file=SimpleUploadedFile('proof.txt', b'proof'), file_type='document', file_size=5, original_filename='proof.txt', mime_type='text/plain')
        url = reverse('message-forward-batch')
        payload = {'source_chat_id': self.chat.id, 'source_message_ids': [text_message.id, attachment_message.id], 'target_chat_ids': [target_chat.id], 'target_user_ids': []}
        response = self.client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert response.data['summary']['forwardable_messages'] == 2
        assert response.data['summary']['succeeded_sends'] == 2
        assert response.data['resolved']['skipped_message_ids'] == []
        forwarded_messages = list(Message.objects.filter(chat=target_chat, sender=self.user1).order_by('created_at'))
        assert len(forwarded_messages) == 2
        assert forwarded_messages[0].content == 'Forward this text'
        assert forwarded_messages[1].content == 'Has attachment'
        assert not forwarded_messages[0].has_attachments
        assert forwarded_messages[1].has_attachments
        copied_attachment = MessageAttachment.objects.get(message=forwarded_messages[1])
        assert copied_attachment.original_filename == source_attachment.original_filename
        assert copied_attachment.file_size == source_attachment.file_size
        assert copied_attachment.mime_type == source_attachment.mime_type
        assert copied_attachment.uploader_id == self.user1.id
        assert copied_attachment.file.name != source_attachment.file.name
        assert ChatOutboxEvent.objects.filter(
            event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
        ).count() == 2

    def test_forward_batch_forwards_attachment_only_message(self):
        """Attachment-only message should forward successfully."""
        target_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Attachment Target')
        ChatParticipant.objects.create(chat=target_chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=target_chat, user=self.user2, is_active=True)
        attachment_only_message = Message.objects.create(chat=self.chat, sender=self.user2, content='')
        MessageAttachment.objects.create(uploader=self.user2, message=attachment_only_message, file=SimpleUploadedFile('diagram.png', b'fakepng'), file_type='image', file_size=7, original_filename='diagram.png', mime_type='image/png')
        response = self.client.post(reverse('message-forward-batch'), {'source_chat_id': self.chat.id, 'source_message_ids': [attachment_only_message.id], 'target_chat_ids': [target_chat.id], 'target_user_ids': []}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert response.data['summary']['forwardable_messages'] == 1
        assert response.data['resolved']['skipped_message_ids'] == []
        forwarded_message = Message.objects.get(chat=target_chat, sender=self.user1)
        assert forwarded_message.content == ''
        assert forwarded_message.has_attachments
        assert forwarded_message.attachments.count() == 1
        assert ChatOutboxEvent.objects.filter(
            event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
        ).count() == 1

    def test_forward_batch_attachment_copy_failure_returns_partial_success(self):
        """A single attachment copy failure should fail only that send unit."""
        target_chat_a = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Target A')
        target_chat_b = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Target B')
        for target_chat in (target_chat_a, target_chat_b):
            ChatParticipant.objects.create(chat=target_chat, user=self.user1, is_active=True)
            ChatParticipant.objects.create(chat=target_chat, user=self.user2, is_active=True)
        source_message = Message.objects.create(chat=self.chat, sender=self.user2, content='With attachment')
        MessageAttachment.objects.create(uploader=self.user2, message=source_message, file=SimpleUploadedFile('copyfail.txt', b'copy-fail'), file_type='document', file_size=9, original_filename='copyfail.txt', mime_type='text/plain')
        original_copy = MessageService._copy_file_field_for_forward
        invocation_count = {'count': 0}

        def flaky_copy(*args, **kwargs):
            invocation_count['count'] += 1
            if invocation_count['count'] == 1:
                raise MessageService.AttachmentCopyError('simulated copy failure')
            return original_copy(*args, **kwargs)
        with patch('chat.services.MessageService._copy_file_field_for_forward', side_effect=flaky_copy):
            response = self.client.post(reverse('message-forward-batch'), {'source_chat_id': self.chat.id, 'source_message_ids': [source_message.id], 'target_chat_ids': [target_chat_a.id, target_chat_b.id], 'target_user_ids': []}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'partial_success'
        assert response.data['summary']['attempted_sends'] == 2
        assert response.data['summary']['succeeded_sends'] == 1
        assert response.data['summary']['failed_sends'] == 1
        assert 'attachment_copy_failed' in {f['reason'] for f in response.data['failures']}
        assert Message.objects.filter(chat=target_chat_a, sender=self.user1).count() == 0
        assert Message.objects.filter(chat=target_chat_b, sender=self.user1).count() == 1
        assert ChatOutboxEvent.objects.filter(
            event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
        ).count() == 1

    def test_forward_batch_skips_empty_messages(self):
        """Messages with no text and no attachments should still be skipped."""
        target_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Skip Empty Target')
        ChatParticipant.objects.create(chat=target_chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=target_chat, user=self.user2, is_active=True)
        text_message = Message.objects.create(chat=self.chat, sender=self.user2, content='This should forward')
        empty_message = Message.objects.create(chat=self.chat, sender=self.user2, content='   ')
        response = self.client.post(reverse('message-forward-batch'), {'source_chat_id': self.chat.id, 'source_message_ids': [text_message.id, empty_message.id], 'target_chat_ids': [target_chat.id], 'target_user_ids': []}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'partial_success'
        assert response.data['summary']['forwardable_messages'] == 1
        assert empty_message.id in response.data['resolved']['skipped_message_ids']
        assert Message.objects.filter(chat=target_chat, sender=self.user1).count() == 1
        assert ChatOutboxEvent.objects.filter(
            event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME
        ).count() == 1

    def test_forward_batch_requires_source_participant(self):
        """Test that non-participants of source chat cannot forward messages."""
        outsider = User.objects.create_user(email='outsider@example.com', username='outsider', password='testpass123')
        ProjectMember.objects.create(user=outsider, project=self.project, role='member', is_active=True)
        self.client.force_authenticate(user=outsider)
        target_chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Target Group')
        ChatParticipant.objects.create(chat=target_chat, user=outsider, is_active=True)
        ChatParticipant.objects.create(chat=target_chat, user=self.user2, is_active=True)
        source_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Forbidden source')
        url = reverse('message-forward-batch')
        response = self.client.post(url, {'source_chat_id': self.chat.id, 'source_message_ids': [source_message.id], 'target_chat_ids': [target_chat.id], 'target_user_ids': []}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'not a participant of the source chat' in str(response.data)

    def test_message_serializer_returns_forward_metadata(self):
        """Forwarded messages should expose structured forward metadata."""
        source_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Source message')
        forwarded_message = Message.objects.create(chat=self.chat, sender=self.user1, content='Forwarded body', forwarded_from_message=source_message, forwarded_from_sender_display=self.user2.username, forwarded_from_created_at=source_message.created_at)
        data = MessageSerializer(forwarded_message).data
        assert data['is_forwarded']
        assert data['forwarded_from'] is not None
        assert data['forwarded_from']['message_id'] == source_message.id
        assert data['forwarded_from']['sender_display'] == self.user2.username
        assert data['forwarded_from']['created_at'] is not None

    def test_message_serializer_keeps_forward_snapshot_when_source_deleted(self):
        """Forwarded message should preserve snapshot metadata after source delete."""
        source_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Source message')
        forwarded_message = Message.objects.create(chat=self.chat, sender=self.user1, content='Forwarded body', forwarded_from_message=source_message, forwarded_from_sender_display=self.user2.username, forwarded_from_created_at=source_message.created_at)
        source_message.delete()
        forwarded_message.refresh_from_db()
        data = MessageSerializer(forwarded_message).data
        assert data['is_forwarded']
        assert data['forwarded_from'] is not None
        assert data['forwarded_from']['message_id'] is None
        assert data['forwarded_from']['sender_display'] == self.user2.username
        assert data['forwarded_from']['created_at'] is not None

    def test_message_serializer_returns_non_forward_message_fields(self):
        """Regular messages should not expose forward metadata."""
        regular_message = Message.objects.create(chat=self.chat, sender=self.user1, content='Normal message')
        data = MessageSerializer(regular_message).data
        assert not data['is_forwarded']
        assert data['forwarded_from'] is None

class TestAttachmentAPI:
    """Test Attachment API endpoints"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import MessageAttachment
        self.user1 = User.objects.create_user(email='user1@example.com', username='user1', password='testpass123')
        self.user2 = User.objects.create_user(email='user2@example.com', username='user2', password='testpass123')
        self.organization = Organization.objects.create(name='Test Organization')
        self.team = Team.objects.create(organization=self.organization, name='Test Team')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        TeamMember.objects.create(user=self.user1, team=self.team)
        TeamMember.objects.create(user=self.user2, team=self.team)
        ProjectMember.objects.create(user=self.user1, project=self.project, role='owner', is_active=True)
        ProjectMember.objects.create(user=self.user2, project=self.project, role='member', is_active=True)
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=self.chat, user=self.user2, is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def test_upload_attachment(self):
        """Test uploading an attachment"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        test_file = SimpleUploadedFile(
            'test.pdf',
            b'%PDF-1.4 test content',
            content_type='application/pdf'
        )
        
        url = reverse('attachment-list')
        response = self.client.post(url, {'file': test_file}, format='multipart')
        
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["original_filename"] == "test.pdf"
        assert response.data["file_type"] == "document"
        assert "file_url" in response.data
        assert "file_size_display" in response.data

    def test_upload_image(self):
        """Test uploading an image attachment"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        image_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        image_file = SimpleUploadedFile('test.png', image_content, content_type='image/png')
        url = reverse('attachment-list')
        response = self.client.post(url, {'file': image_file}, format='multipart')
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['file_type'] == 'image'

    def test_get_attachment(self):
        """Test retrieving an attachment"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import MessageAttachment
        attachment = MessageAttachment.objects.create(uploader=self.user1, file=SimpleUploadedFile('test.txt', b'content'), file_type='document', file_size=7, original_filename='test.txt', mime_type='text/plain')
        url = reverse('attachment-detail', kwargs={'pk': attachment.id})
        response = self.client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == attachment.id

    def test_delete_unlinked_attachment(self):
        """Test deleting an unlinked attachment"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import MessageAttachment
        attachment = MessageAttachment.objects.create(uploader=self.user1, file=SimpleUploadedFile('test.txt', b'content'), file_type='document', file_size=7, original_filename='test.txt', mime_type='text/plain', message=None)
        url = reverse('attachment-detail', kwargs={'pk': attachment.id})
        response = self.client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not MessageAttachment.objects.filter(id=attachment.id).exists()

    def test_cannot_delete_linked_attachment(self):
        """Test that linked attachments cannot be deleted"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import MessageAttachment
        message = Message.objects.create(chat=self.chat, sender=self.user1, content='Test')
        attachment = MessageAttachment.objects.create(uploader=self.user1, message=message, file=SimpleUploadedFile('test.txt', b'content'), file_type='document', file_size=7, original_filename='test.txt', mime_type='text/plain')
        url = reverse('attachment-detail', kwargs={'pk': attachment.id})
        response = self.client.delete(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert MessageAttachment.objects.filter(id=attachment.id).exists()

    def test_send_message_with_attachments(self):
        """Test sending a message with attachments"""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from chat.models import MessageAttachment
        attachment = MessageAttachment.objects.create(uploader=self.user1, file=SimpleUploadedFile('test.txt', b'content'), file_type='document', file_size=7, original_filename='test.txt', mime_type='text/plain', message=None)
        url = reverse('message-list')
        response = self.client.post(url, {'chat': self.chat.id, 'content': 'Message with attachment', 'attachment_ids': [attachment.id]}, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        attachment.refresh_from_db()
        assert attachment.message.id == response.data['id']
        assert len(response.data['attachments']) == 1

    def test_list_accessible_files_requires_project_id(self):
        url = reverse('attachment-files')
        r = self.client.get(url)
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_accessible_files_scopes_to_project_and_participation(self):
        """
        User should see linked attachments from chats they participate in within the project,
        and should not see attachments from other projects or chats they are not in.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        msg_allowed = Message.objects.create(chat=self.chat, sender=self.user2, content='Has file')
        allowed_attachment = MessageAttachment.objects.create(uploader=self.user2, message=msg_allowed, file=SimpleUploadedFile('allowed.txt', b'allowed'), file_type='document', file_size=7, original_filename='allowed.txt', mime_type='text/plain')
        outsider = User.objects.create_user(email='outsider@example.com', username='outsider', password='testpass123')
        ProjectMember.objects.create(user=outsider, project=self.project, role='member', is_active=True)
        chat_forbidden = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat_forbidden, user=self.user2, is_active=True)
        ChatParticipant.objects.create(chat=chat_forbidden, user=outsider, is_active=True)
        msg_forbidden = Message.objects.create(chat=chat_forbidden, sender=self.user2, content='Forbidden file')
        MessageAttachment.objects.create(uploader=self.user2, message=msg_forbidden, file=SimpleUploadedFile('forbidden.txt', b'forbidden'), file_type='document', file_size=9, original_filename='forbidden.txt', mime_type='text/plain')
        other_project = Project.objects.create(name='Other Project', organization=self.organization)
        ProjectMember.objects.create(user=self.user1, project=other_project, role='owner', is_active=True)
        ProjectMember.objects.create(user=self.user2, project=other_project, role='member', is_active=True)
        other_chat = Chat.objects.create(project=other_project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=other_chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=other_chat, user=self.user2, is_active=True)
        other_msg = Message.objects.create(chat=other_chat, sender=self.user2, content='Other project file')
        MessageAttachment.objects.create(uploader=self.user2, message=other_msg, file=SimpleUploadedFile('other.txt', b'other'), file_type='document', file_size=5, original_filename='other.txt', mime_type='text/plain')
        url = reverse('attachment-files')
        r = self.client.get(url, {'project_id': self.project.slug, 'page': 1, 'page_size': 25})
        assert r.status_code == status.HTTP_200_OK
        assert 'results' in r.data
        ids = {row['id'] for row in r.data['results']}
        assert allowed_attachment.id in ids
        assert ids == {allowed_attachment.id}
        row = r.data['results'][0]
        assert 'uploader' in row
        assert row['uploader']['id'] == self.user2.id
        assert 'chat' in row
        assert row['chat']['id'] == self.chat.id
        assert row['message_id'] == msg_allowed.id

    def test_list_accessible_files_excludes_forward_copies_after_source_delete(self):
        """
        Forwarded file copies should not become standalone Files-tab rows after
        the original message is deleted.
        """
        source_msg = Message.objects.create(chat=self.chat, sender=self.user2, content='Original file')
        source_attachment = MessageAttachment.objects.create(uploader=self.user2, message=source_msg, file=SimpleUploadedFile('original.txt', b'original'), file_type='document', file_size=8, original_filename='original.txt', mime_type='text/plain')
        forwarded_msg = Message.objects.create(chat=self.chat, sender=self.user1, content='Original file', forwarded_from_message=source_msg, forwarded_from_sender_display=self.user2.username, forwarded_from_created_at=source_msg.created_at, has_attachments=True)
        forwarded_attachment = MessageAttachment.objects.create(uploader=self.user1, message=forwarded_msg, file=SimpleUploadedFile('original-copy.txt', b'original'), file_type='document', file_size=8, original_filename='original.txt', mime_type='text/plain')
        url = reverse('attachment-files')
        before_delete = self.client.get(url, {'project_id': self.project.slug})
        assert before_delete.status_code == status.HTTP_200_OK
        before_ids = {row['id'] for row in before_delete.data['results']}
        assert before_ids == {source_attachment.id}
        source_msg.delete()
        after_delete = self.client.get(url, {'project_id': self.project.slug})
        assert after_delete.status_code == status.HTTP_200_OK
        assert after_delete.data['results'] == []
        assert MessageAttachment.objects.filter(id=forwarded_attachment.id).exists()
        forwarded_msg.refresh_from_db()
        assert not forwarded_msg.has_attachments

    def test_list_accessible_files_excludes_legacy_orphan_forward_rows(self):
        """Older forwarded copies with a missing source FK should stay out of Files."""
        orphan_forward = Message.objects.create(chat=self.chat, sender=self.user1, content='Forwarded file', forwarded_from_sender_display=self.user2.username, forwarded_from_created_at=None, has_attachments=True)
        MessageAttachment.objects.create(uploader=self.user1, message=orphan_forward, file=SimpleUploadedFile('stale-copy.txt', b'stale'), file_type='document', file_size=5, original_filename='stale-copy.txt', mime_type='text/plain')
        response = self.client.get(reverse('attachment-files'), {'project_id': self.project.slug})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'] == []

    def test_list_accessible_files_includes_thread_root_for_reply_attachments(self):
        """Files attached to thread replies should deep-link through their root timeline message."""
        root_message = Message.objects.create(chat=self.chat, sender=self.user2, content='Root')
        thread_reply = Message.objects.create(chat=self.chat, sender=self.user1, content='Reply with file', parent_message=root_message)
        attachment = MessageAttachment.objects.create(uploader=self.user1, message=thread_reply, file=SimpleUploadedFile('reply.txt', b'reply'), file_type='document', file_size=5, original_filename='reply.txt', mime_type='text/plain')
        response = self.client.get(reverse('attachment-files'), {'project_id': self.project.slug})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'][0]['id'] == attachment.id
        assert response.data['results'][0]['message_id'] == thread_reply.id
        assert response.data['results'][0]['thread_root_message_id'] == root_message.id

class TestStarredChatAPI:
    """Test starred chat API."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.user1 = User.objects.create_user(email='star1@example.com', username='star1', password='testpass123')
        self.user2 = User.objects.create_user(email='star2@example.com', username='star2', password='testpass123')
        self.user3 = User.objects.create_user(email='star3@example.com', username='star3', password='testpass123')
        self.organization = Organization.objects.create(name='Star Org')
        self.team = Team.objects.create(organization=self.organization, name='Star Team')
        self.project = Project.objects.create(name='Star Project', organization=self.organization)
        TeamMember.objects.create(user=self.user1, team=self.team)
        TeamMember.objects.create(user=self.user2, team=self.team)
        ProjectMember.objects.create(user=self.user1, project=self.project, role='Team Leader', is_active=True)
        ProjectMember.objects.create(user=self.user2, project=self.project, role='member', is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def _private_chat(self):
        chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        return chat

    def _group_chat(self):
        chat = Chat.objects.create(project=self.project, type=ChatType.GROUP, name='Starred Group')
        ChatParticipant.objects.create(chat=chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=self.user2, is_active=True)
        return chat

    def test_list_starred_requires_project_id(self):
        url = reverse('chat-starred-list')
        r = self.client.get(url)
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_star_list_unstar_reorder(self):
        c1 = self._private_chat()
        c2 = self._group_chat()
        list_url = reverse('chat-starred-list')
        r = self.client.get(list_url, {'project_id': self.project.slug})
        assert r.status_code == status.HTTP_200_OK
        assert r.data == []
        r = self.client.post(reverse('chat-starred-list'), {'chat_id': c1.id}, format='json')
        assert r.status_code == status.HTTP_201_CREATED
        assert r.data['chat']['id'] == c1.id
        r = self.client.post(reverse('chat-starred-list'), {'chat_id': c2.id}, format='json')
        assert r.status_code == status.HTTP_201_CREATED
        r = self.client.get(list_url, {'project_id': self.project.slug})
        assert r.status_code == status.HTTP_200_OK
        assert len(r.data) == 2
        assert r.data[0]['chat']['id'] == c1.id
        assert r.data[1]['chat']['id'] == c2.id
        reorder_url = reverse('chat-starred-reorder')
        r = self.client.post(reorder_url, {'project_id': self.project.id, 'chat_ids': [c2.id, c1.id]}, format='json')
        assert r.status_code == status.HTTP_200_OK
        r = self.client.get(list_url, {'project_id': self.project.slug})
        assert r.data[0]['chat']['id'] == c2.id
        assert r.data[1]['chat']['id'] == c1.id
        r = self.client.delete(reverse('chat-starred-detail', kwargs={'pk': c1.id}))
        assert r.status_code == status.HTTP_204_NO_CONTENT
        r = self.client.get(list_url, {'project_id': self.project.slug})
        assert len(r.data) == 1
        assert r.data[0]['chat']['id'] == c2.id

    def test_star_idempotent(self):
        c = self._private_chat()
        r1 = self.client.post(reverse('chat-starred-list'), {'chat_id': c.id}, format='json')
        assert r1.status_code == status.HTTP_201_CREATED
        r2 = self.client.post(reverse('chat-starred-list'), {'chat_id': c.id}, format='json')
        assert r2.status_code == status.HTTP_200_OK
        assert ChatStar.objects.filter(user=self.user1, chat=c).count() == 1

    def test_star_not_participant_forbidden(self):
        c = self._private_chat()
        client3 = APIClient()
        client3.force_authenticate(user=self.user3)
        r = client3.post(reverse('chat-starred-list'), {'chat_id': c.id}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_reorder_rejects_incomplete_list(self):
        c1 = self._private_chat()
        c2 = self._private_chat()
        self.client.post(reverse('chat-starred-list'), {'chat_id': c1.id}, format='json')
        self.client.post(reverse('chat-starred-list'), {'chat_id': c2.id}, format='json')
        r = self.client.post(reverse('chat-starred-reorder'), {'project_id': self.project.id, 'chat_ids': [c1.id]}, format='json')
        assert r.status_code == status.HTTP_400_BAD_REQUEST


class AttachmentMimeUploadAPITest(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(
            email='mime-user1@example.com',
            username='mime-user1',
            password='testpass123',
        )
        self.user2 = User.objects.create_user(
            email='mime-user2@example.com',
            username='mime-user2',
            password='testpass123',
        )
        self.organization = Organization.objects.create(name='MIME Test Organization')
        self.team = Team.objects.create(organization=self.organization, name='MIME Test Team')
        self.project = Project.objects.create(name='MIME Test Project', organization=self.organization)
        TeamMember.objects.create(user=self.user1, team=self.team)
        TeamMember.objects.create(user=self.user2, team=self.team)
        ProjectMember.objects.create(user=self.user1, project=self.project, role='owner', is_active=True)
        ProjectMember.objects.create(user=self.user2, project=self.project, role='member', is_active=True)
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user1, is_active=True)
        ChatParticipant.objects.create(chat=self.chat, user=self.user2, is_active=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user1)

    def test_rejects_unsupported_mime_before_attachment_save(self):
        initial_count = MessageAttachment.objects.count()
        test_file = SimpleUploadedFile(
            'archive.zip',
            b'PK\x03\x04',
            content_type='application/zip',
        )

        response = self.client.post(reverse('attachment-list'), {'file': test_file}, format='multipart')

        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
        self.assertEqual(response.data['code'], 'unsupported_mime_type')
        self.assertEqual(response.data['mime_type'], 'application/zip')
        self.assertIn('Unsupported MIME type', response.data['error'])
        self.assertEqual(MessageAttachment.objects.count(), initial_count)


class TestMessageCreateQueryScaling:
    """The send endpoint must not scale its query count with channel size."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.organization = Organization.objects.create(name='Send Scaling Organization')
        self.project = Project.objects.create(
            name='Send Scaling Project',
            organization=self.organization,
        )
        self.client = APIClient()

    def _build_channel(self, label, member_count):
        chat = Chat.objects.create(
            project=self.project,
            type=ChatType.GROUP,
            name=f'send-scaling-{label}',
        )
        members = []
        for index in range(member_count):
            user = User.objects.create_user(
                email=f'scale-{label}-{index}@example.com',
                username=f'scale-{label}-{index}',
                password='testpass123',
            )
            members.append(user)
            ChatParticipant.objects.create(chat=chat, user=user, is_active=True)
        return chat, members

    def _send_query_count(self, chat, sender):
        self.client.force_authenticate(user=sender)
        payload = {'chat': chat.id, 'content': 'scaling probe'}
        # Warm anything cached per sender so only the steady-state cost is compared.
        self.client.post(reverse('message-list'), payload, format='json')

        with CaptureQueriesContext(connection) as captured:
            response = self.client.post(reverse('message-list'), payload, format='json')

        assert response.status_code == status.HTTP_201_CREATED
        return len(captured)

    def test_send_query_count_does_not_grow_with_channel_size(self):
        """The response embeds one status per recipient, each with a nested user.

        Without prefetching that user the send request costs an extra query per
        recipient, which is what made large channels slow under concurrency.
        """
        small_chat, small_members = self._build_channel('small', 4)
        large_chat, large_members = self._build_channel('large', 20)

        small_queries = self._send_query_count(small_chat, small_members[0])
        large_queries = self._send_query_count(large_chat, large_members[0])

        assert large_queries == small_queries
