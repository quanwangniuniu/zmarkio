import pytest
import logging
from datetime import timedelta
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from core.models import Project, Organization
from chat.models import Chat, ChatParticipant, Message, MessageAttachment, MessageMention, MessageReaction, ScheduledMessage, ChatType
from chat.serializers import MessageAttachmentSerializer, AttachmentUploadSerializer, MessageWithAttachmentsSerializer, MessageCreateWithAttachmentsSerializer, ScheduledMessageCreateSerializer, ChatSerializer, ChatListSerializer, MessageSerializer, UserSimpleSerializer
from chat.tasks import send_scheduled_message
from chat.services import OnlineStatusService
pytestmark = pytest.mark.django_db
User = get_user_model()
logger = logging.getLogger(__name__)

class TestUserSimpleSerializer:
    """Test cases for chat user serialization."""

    def test_includes_online_status(self):
        user = User.objects.create_user(email='online@example.com', username='onlineuser', password='testpass123')
        OnlineStatusService.set_online(user.id)
        data = UserSimpleSerializer(user).data
        assert data['is_online']

class TestMessageAttachmentSerializer:
    """Test cases for MessageAttachmentSerializer"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.organization = Organization.objects.create(name='Test Org')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.user = User.objects.create_user(email='test@example.com', username='testuser', password='testpass123')
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user, is_active=True)
        self.message = Message.objects.create(chat=self.chat, sender=self.user, content='Test message with attachment')
        self.attachment = MessageAttachment.objects.create(message=self.message, uploader=self.user, file=SimpleUploadedFile('test.txt', b'test content'), file_type='document', file_size=12, original_filename='test.txt', mime_type='text/plain')
        self.factory = APIRequestFactory()

    def test_serializer_fields(self):
        """Test that serializer returns correct fields"""
        request = self.factory.get('/')
        request.user = self.user
        serializer = MessageAttachmentSerializer(self.attachment, context={'request': request})
        data = serializer.data
        assert 'id' in data
        assert 'message' in data
        assert 'file_type' in data
        assert 'file_url' in data
        assert 'thumbnail_url' in data
        assert 'file_size' in data
        assert 'file_size_display' in data
        assert 'original_filename' in data
        assert 'mime_type' in data
        assert 'created_at' in data

    def test_file_url_is_absolute(self):
        """Test that file_url returns an absolute URL"""
        request = self.factory.get('/')
        request.user = self.user
        serializer = MessageAttachmentSerializer(self.attachment, context={'request': request})
        data = serializer.data
        assert data['file_url'] is not None
        assert data['file_url'].startswith('http')

    def test_file_size_display_format(self):
        """Test that file_size_display is human readable"""
        request = self.factory.get('/')
        request.user = self.user
        serializer = MessageAttachmentSerializer(self.attachment, context={'request': request})
        data = serializer.data
        assert data['file_size_display'] == '12 B'

class TestAttachmentUploadSerializer:
    """Test cases for AttachmentUploadSerializer"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.user = User.objects.create_user(email='test@example.com', username='testuser', password='testpass123')
        self.factory = APIRequestFactory()

    def test_upload_image(self):
        """Test uploading an image file"""
        image_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        image_file = SimpleUploadedFile('test.png', image_content, content_type='image/png')
        request = self.factory.post('/')
        request.user = self.user
        serializer = AttachmentUploadSerializer(data={'file': image_file}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        attachment = serializer.save()
        assert attachment.uploader == self.user
        assert attachment.file_type == 'image'
        assert attachment.original_filename == 'test.png'
        assert attachment.mime_type == 'image/png'
        assert attachment.file_size > 0

    def test_upload_document(self):
        """Test uploading a document file"""
        doc_file = SimpleUploadedFile('test.pdf', b'%PDF-1.4 test content', content_type='application/pdf')
        request = self.factory.post('/')
        request.user = self.user
        serializer = AttachmentUploadSerializer(data={'file': doc_file}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        attachment = serializer.save()
        assert attachment.file_type == 'document'
        assert attachment.mime_type == 'application/pdf'

    def test_upload_video(self):
        """Test uploading a video file"""
        video_file = SimpleUploadedFile('test.mp4', b'video content', content_type='video/mp4')
        request = self.factory.post('/')
        request.user = self.user
        serializer = AttachmentUploadSerializer(data={'file': video_file}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        attachment = serializer.save()
        assert attachment.file_type == 'video'

    def test_upload_audio_recording(self):
        """Test uploading a browser-recorded audio attachment."""
        audio_file = SimpleUploadedFile('audio-recording.webm', b'audio content', content_type='audio/webm')
        request = self.factory.post('/')
        request.user = self.user
        serializer = AttachmentUploadSerializer(data={'file': audio_file}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        attachment = serializer.save()
        assert attachment.file_type == 'document'
        assert attachment.mime_type == 'audio/webm'

class TestMessageWithAttachmentsSerializer:
    """Test cases for MessageWithAttachmentsSerializer"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.organization = Organization.objects.create(name='Test Org')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.user = User.objects.create_user(email='test@example.com', username='testuser', password='testpass123')
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user, is_active=True)
        self.message = Message.objects.create(chat=self.chat, sender=self.user, content='Test message')
        self.attachment1 = MessageAttachment.objects.create(message=self.message, uploader=self.user, file=SimpleUploadedFile('file1.txt', b'content1'), file_type='document', file_size=8, original_filename='file1.txt', mime_type='text/plain')
        self.attachment2 = MessageAttachment.objects.create(message=self.message, uploader=self.user, file=SimpleUploadedFile('file2.txt', b'content2'), file_type='document', file_size=8, original_filename='file2.txt', mime_type='text/plain')
        self.factory = APIRequestFactory()

    def test_includes_attachments(self):
        """Test that serializer includes attachments"""
        request = self.factory.get('/')
        request.user = self.user
        serializer = MessageWithAttachmentsSerializer(self.message, context={'request': request})
        data = serializer.data
        assert data['seq'] == self.message.seq
        assert 'attachments' in data
        assert len(data['attachments']) == 2

    def test_attachment_details(self):
        """Test that attachment details are correct"""
        request = self.factory.get('/')
        request.user = self.user
        serializer = MessageWithAttachmentsSerializer(self.message, context={'request': request})
        data = serializer.data
        attachment_data = data['attachments'][0]
        assert 'file_url' in attachment_data
        assert 'original_filename' in attachment_data

    def test_deleted_message_redaction_is_centralized(self):
        """Deleted message output should redact user-controlled fields in one pass."""
        reply_source = Message.objects.create(chat=self.chat, sender=self.user, content='reply source')
        forward_source = Message.objects.create(chat=self.chat, sender=self.user, content='forward source')
        deleted_message = Message.objects.create(chat=self.chat, sender=self.user, content='secret body', rich_body={'type': 'doc', 'content': [{'type': 'paragraph'}]}, is_deleted=True, deleted_at=timezone.now(), has_attachments=True, forwarded_from_message=forward_source, forwarded_from_sender_display=self.user.username, forwarded_from_created_at=forward_source.created_at, reply_to=reply_source)
        MessageAttachment.objects.create(message=deleted_message, uploader=self.user, file=SimpleUploadedFile('secret.txt', b'secret'), file_type='document', file_size=6, original_filename='secret.txt', mime_type='text/plain')
        MessageMention.objects.create(message=deleted_message, mentioned_user=self.user)
        MessageReaction.objects.create(message=deleted_message, user=self.user, emoji='ok')
        request = self.factory.get('/')
        request.user = self.user
        data = MessageWithAttachmentsSerializer(deleted_message, context={'request': request}).data
        assert data['is_deleted']
        assert data['content'] == ''
        assert data['rich_body'] is None
        assert not data['has_attachments']
        assert data['attachment_count'] == 0
        assert not data['is_forwarded']
        assert data['forwarded_from'] is None
        assert data['reply_to'] is None
        assert data['reactions'] == []
        assert not data['can_revoke']
        assert data['mentioned_user_ids'] == []
        assert data['missing_forwarded_attachments'] == []
        assert data['attachments'] == []

    def test_orphan_forwarded_message_hides_attachments(self):
        """Legacy forwarded file copies should not render after their source is gone."""
        forwarded_message = Message.objects.create(chat=self.chat, sender=self.user, content='Forwarded file', forwarded_from_sender_display='source-user', has_attachments=True)
        MessageAttachment.objects.create(message=forwarded_message, uploader=self.user, file=SimpleUploadedFile('forwarded.txt', b'content'), file_type='document', file_size=7, original_filename='forwarded.txt', mime_type='text/plain')
        request = self.factory.get('/')
        request.user = self.user
        data = MessageWithAttachmentsSerializer(forwarded_message, context={'request': request}).data
        assert data['attachments'] == []
        assert not data['has_attachments']
        assert data['attachment_count'] == 0
        assert len(data['missing_forwarded_attachments']) == 1
        assert data['missing_forwarded_attachments'][0]['kind'] == 'document'
        assert data['missing_forwarded_attachments'][0]['original_filename'] == 'forwarded.txt'

    def test_orphan_forwarded_attachment_only_message_gets_generic_tombstone(self):
        """Already-cleaned forwarded attachment-only messages should still show a tombstone."""
        forwarded_message = Message.objects.create(chat=self.chat, sender=self.user, content='', forwarded_from_sender_display='source-user', has_attachments=False)
        request = self.factory.get('/')
        request.user = self.user
        data = MessageWithAttachmentsSerializer(forwarded_message, context={'request': request}).data
        assert data['attachments'] == []
        assert len(data['missing_forwarded_attachments']) == 1
        assert data['missing_forwarded_attachments'][0]['kind'] == 'unknown'

class TestMessageCreateWithAttachmentsSerializer:
    """Test cases for MessageCreateWithAttachmentsSerializer"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Set up test data"""
        self.organization = Organization.objects.create(name='Test Org')
        self.project = Project.objects.create(name='Test Project', organization=self.organization)
        self.user = User.objects.create_user(email='test@example.com', username='testuser', password='testpass123')
        self.mentioned_user = User.objects.create_user(email='mentioned@example.com', username='mentioned', password='testpass123')
        self.unrelated_user = User.objects.create_user(email='unrelated@example.com', username='unrelated', password='testpass123')
        self.chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=self.chat, user=self.user, is_active=True)
        ChatParticipant.objects.create(chat=self.chat, user=self.mentioned_user, is_active=True)
        self.unlinked_attachment = MessageAttachment.objects.create(message=None, uploader=self.user, file=SimpleUploadedFile('unlinked.txt', b'content'), file_type='document', file_size=7, original_filename='unlinked.txt', mime_type='text/plain')
        self.factory = APIRequestFactory()

    def test_create_message_with_content(self):
        """Test creating a message with text content"""
        request = self.factory.post('/')
        request.user = self.user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': 'Test message content'}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        message = serializer.save()
        assert message.content == 'Test message content'
        assert message.sender == self.user

    def test_create_rich_message_derives_plain_text_and_mentions(self):
        """Rich messages store Tiptap JSON plus searchable plain text and mention rows."""
        request = self.factory.post('/')
        request.user = self.user
        rich_body = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Hello '}, {'type': 'mention', 'attrs': {'id': self.mentioned_user.id, 'label': self.mentioned_user.username}}]}]}
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': '', 'rich_body': rich_body, 'mention_ids': [self.mentioned_user.id]}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        message = serializer.save()
        assert message.rich_body == rich_body
        assert message.content == 'Hello @mentioned'
        assert MessageMention.objects.filter(message=message, mentioned_user=self.mentioned_user).exists()

    def test_mentions_must_be_active_chat_participants(self):
        """Mention ids are limited to users who can see the chat."""
        request = self.factory.post('/')
        request.user = self.user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': 'Hello @unrelated', 'mention_ids': [self.unrelated_user.id]}, context={'request': request})
        assert not serializer.is_valid()
        assert 'mention_ids' in serializer.errors

    def test_reply_target_must_belong_to_same_chat(self):
        """Quote replies cannot point at a message from another chat."""
        other_chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=other_chat, user=self.unrelated_user, is_active=True)
        other_message = Message.objects.create(chat=other_chat, sender=self.unrelated_user, content='Outside message')
        request = self.factory.post('/')
        request.user = self.user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': 'Cross-chat quote', 'reply_to_id': other_message.id}, context={'request': request})
        assert not serializer.is_valid()
        assert 'reply_to_id' in serializer.errors

    def test_thread_parent_must_belong_to_same_chat(self):
        """Thread replies cannot point at a parent message from another chat."""
        other_chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=other_chat, user=self.unrelated_user, is_active=True)
        other_message = Message.objects.create(chat=other_chat, sender=self.unrelated_user, content='Outside parent')
        request = self.factory.post('/')
        request.user = self.user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': 'Cross-chat thread', 'parent_message_id': other_message.id}, context={'request': request})
        assert not serializer.is_valid()
        assert 'parent_message_id' in serializer.errors

    def test_create_message_with_attachments(self):
        """Test creating a message with attachments"""
        request = self.factory.post('/')
        request.user = self.user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': 'Message with attachment', 'attachment_ids': [self.unlinked_attachment.id]}, context={'request': request})
        assert serializer.is_valid(), serializer.errors
        message = serializer.save()
        self.unlinked_attachment.refresh_from_db()
        assert message.attachments.count() == 1
        assert self.unlinked_attachment.message == message
        assert message.has_attachments

    def test_scheduled_attachments_must_be_owned_unlinked(self):
        """Schedule-send cannot reserve another user's uploaded attachment."""
        other_attachment = MessageAttachment.objects.create(message=None, uploader=self.unrelated_user, file=SimpleUploadedFile('other.txt', b'content'), file_type='document', file_size=7, original_filename='other.txt', mime_type='text/plain')
        request = self.factory.post('/')
        request.user = self.user
        serializer = ScheduledMessageCreateSerializer(data={'chat_id': self.chat.id, 'attachment_ids': [other_attachment.id], 'scheduled_at': (timezone.now() + timedelta(hours=1)).isoformat()}, context={'request': request})
        assert not serializer.is_valid()
        assert 'attachment_ids' in serializer.errors

    def test_scheduled_reply_target_must_belong_to_same_chat(self):
        """Schedule-send quote replies cannot point at another chat."""
        other_chat = Chat.objects.create(project=self.project, type=ChatType.PRIVATE)
        ChatParticipant.objects.create(chat=other_chat, user=self.unrelated_user, is_active=True)
        other_message = Message.objects.create(chat=other_chat, sender=self.unrelated_user, content='Outside message')
        request = self.factory.post('/')
        request.user = self.user
        serializer = ScheduledMessageCreateSerializer(data={'chat_id': self.chat.id, 'content': 'Scheduled cross-chat quote', 'reply_to_id': other_message.id, 'scheduled_at': (timezone.now() + timedelta(hours=1)).isoformat()}, context={'request': request})
        assert not serializer.is_valid()
        assert 'reply_to_id' in serializer.errors

    def test_scheduled_send_revalidates_attachment_before_linking(self):
        """A queued send fails if its attachment was linked before the task runs."""
        existing_message = Message.objects.create(chat=self.chat, sender=self.user, content='Already sent')
        self.unlinked_attachment.message = existing_message
        self.unlinked_attachment.save(update_fields=['message'])
        scheduled = ScheduledMessage.objects.create(chat=self.chat, sender=self.user, content='Scheduled message', attachment_ids=[self.unlinked_attachment.id], scheduled_at=timezone.now() + timedelta(minutes=1))
        send_scheduled_message.run(scheduled.id)
        scheduled.refresh_from_db()
        self.unlinked_attachment.refresh_from_db()
        assert scheduled.status == ScheduledMessage.STATUS_FAILED
        assert self.unlinked_attachment.message_id == existing_message.id
        assert not Message.objects.filter(chat=self.chat, sender=self.user, content='Scheduled message').exists()

    def test_require_content_or_attachments(self):
        """Test that either content or attachments is required"""
        request = self.factory.post('/')
        request.user = self.user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': '', 'attachment_ids': []}, context={'request': request})
        assert not serializer.is_valid()

    def test_non_participant_cannot_send(self):
        """Test that non-participant cannot send message"""
        other_user = User.objects.create_user(email='other@example.com', username='other', password='testpass123')
        request = self.factory.post('/')
        request.user = other_user
        serializer = MessageCreateWithAttachmentsSerializer(data={'chat': self.chat.id, 'content': 'Test'}, context={'request': request})
        assert not serializer.is_valid()
        assert 'chat' in serializer.errors
