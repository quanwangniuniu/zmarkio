from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import transaction
from django.utils import timezone
from django.conf import settings
import logging
import os
import subprocess
import tempfile
from .models import Chat, ChatParticipant, ChatStar, Message, MessageMention, MessageStatus, ChatType, ChannelVisibility, MessageAttachment, MessageReaction, PinnedMessage, SavedMessage, ScheduledMessage
from .services import ChatService
from core.models import ProjectMember, Project
from core.slug_mixins import resolve_project_pk

User = get_user_model()
logger = logging.getLogger(__name__)
MAX_CHANNEL_NAME_LENGTH = 40


# ==================== Mixins for DRY ====================

class ChatUnreadCountMixin:
    """Mixin providing get_unread_count for Chat serializers"""

    def _get_current_participant(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None

        try:
            return ChatParticipant.objects.get(
                chat=obj,
                user=request.user,
                is_active=True
            )
        except ChatParticipant.DoesNotExist:
            return None

    def get_unread_count(self, obj):
        """Get unread message count for current user using model method"""
        participant = self._get_current_participant(obj)
        if not participant:
            return 0
        # Delegate to model method (single source of truth)
        return participant.get_unread_count()

    def get_mention_unread_count(self, obj):
        """Get unread @-mention count for current user in this chat."""
        participant = self._get_current_participant(obj)
        if not participant:
            return 0
        return participant.get_unread_mention_count()


class MessageContentValidationMixin:
    """Mixin providing content validation for Message serializers"""
    
    def validate_content(self, value):
        """Validate message content"""
        if not value or not value.strip():
            raise serializers.ValidationError("Message content cannot be empty")
        
        if len(value) > 5000:
            raise serializers.ValidationError("Message content too long (max 5000 characters)")
        
        return value.strip()


class ChatParticipantValidationMixin:
    """Mixin providing chat participant validation for Message serializers"""
    
    def validate_chat(self, value):
        """Validate user has access to the chat"""
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context required")
        
        # Check if user is a participant
        if not ChatParticipant.objects.filter(
            chat=value,
            user=request.user,
            is_active=True
        ).exists():
            raise serializers.ValidationError("You are not a participant of this chat")
        
        return value


class UserSimpleSerializer(serializers.ModelSerializer):
    """Simplified user serializer for chat context"""
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'avatar', 'is_online']
        read_only_fields = ['id', 'username', 'email', 'first_name', 'last_name', 'avatar']

    def get_is_online(self, obj):
        # Callers that serialize many users at once can pass a precomputed set
        # under `online_user_ids` to avoid one Redis round trip per user. The
        # per-user lookup stays the default so existing callers are unaffected.
        online_user_ids = self.context.get('online_user_ids')
        if online_user_ids is not None:
            return obj.id in online_user_ids
        from .services import OnlineStatusService
        return OnlineStatusService.is_online(obj.id)


class ChatParticipantSerializer(serializers.ModelSerializer):
    """Serializer for chat participants"""
    user = UserSimpleSerializer(read_only=True)
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = ChatParticipant
        fields = [
            'id', 'user', 'chat', 'joined_at',
            'last_read_at', 'is_active', 'is_manager', 'is_muted', 'muted_until', 'notification_level', 'unread_count'
        ]
        read_only_fields = ['id', 'joined_at']

    def get_unread_count(self, obj):
        """Calculate unread message count for this participant using model method"""
        return obj.get_unread_count()


class ParticipantNotificationSerializer(serializers.ModelSerializer):
    """Serializer for updating is_muted / notification_level for the current user."""

    class Meta:
        model = ChatParticipant
        fields = ['is_muted', 'muted_until', 'notification_level']

    def validate_muted_until(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Temporary mute expiry must be in the future")
        return value

    def update(self, instance, validated_data):
        has_is_muted = 'is_muted' in validated_data
        has_muted_until = 'muted_until' in validated_data

        if validated_data.get('is_muted') is False:
            validated_data['muted_until'] = None
        elif has_muted_until and validated_data.get('muted_until') is not None:
            validated_data['is_muted'] = True
        elif has_is_muted and validated_data.get('is_muted') is True and not has_muted_until:
            validated_data['muted_until'] = None

        return super().update(instance, validated_data)


class MessageStatusSerializer(serializers.ModelSerializer):
    """Serializer for message status"""
    user = UserSimpleSerializer(read_only=True)

    class Meta:
        model = MessageStatus
        fields = [
            'id', 'message', 'user', 'status',
            'delivered_at', 'read_at', 'created_at'
        ]
        read_only_fields = fields


class ReactionSerializer(serializers.Serializer):
    """
    Serializer for aggregated emoji reactions on a message.
    Groups reactions by emoji and includes count and user list.
    """
    emoji = serializers.CharField()
    count = serializers.IntegerField()
    users = serializers.ListField(child=serializers.DictField())
    reacted_by_me = serializers.BooleanField()


class AddReactionSerializer(serializers.Serializer):
    """Serializer for adding a reaction to a message"""
    emoji = serializers.CharField(max_length=10, required=True)

    def validate_emoji(self, value):
        """Validate emoji is not empty"""
        if not value or not value.strip():
            raise serializers.ValidationError("Emoji cannot be empty")
        return value.strip()


class MessageListSerializer(serializers.ListSerializer):
    """Resolves link previews for a whole page in one query (MED-279).

    Without this, every message looks its own preview up, so a page of 50 costs 50
    queries for what is really one `url IN (...)`.
    """

    def to_representation(self, data):
        from django.db import models as django_models
        from .services import build_link_preview_map

        messages = list(data.all() if isinstance(data, django_models.Manager) else data)
        self.context['link_preview_map'] = build_link_preview_map(messages)
        return super().to_representation(messages)


class MessageSerializer(MessageContentValidationMixin, serializers.ModelSerializer):
    """Serializer for chat messages"""
    sender = UserSimpleSerializer(read_only=True)
    status = serializers.SerializerMethodField()
    statuses = MessageStatusSerializer(many=True, read_only=True)
    has_attachments = serializers.SerializerMethodField()
    attachment_count = serializers.SerializerMethodField()
    is_forwarded = serializers.SerializerMethodField()
    forwarded_from = serializers.SerializerMethodField()
    reply_to = serializers.SerializerMethodField()
    reactions = serializers.SerializerMethodField()
    can_revoke = serializers.SerializerMethodField()
    is_hidden_by_me = serializers.SerializerMethodField()
    mentioned_user_ids = serializers.SerializerMethodField()
    missing_forwarded_attachments = serializers.SerializerMethodField()
    thread_reply_count = serializers.SerializerMethodField()
    thread_last_reply_at = serializers.SerializerMethodField()
    thread_participants = serializers.SerializerMethodField()
    has_unread_thread_replies = serializers.SerializerMethodField()
    link_preview = serializers.SerializerMethodField()
    parent_message_id = serializers.IntegerField(read_only=True, allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.context.get('send_response'):
            # A create response is immediately rendered only by the sender.
            # Recipient and thread aggregates are available from normal reads
            # and realtime status events; calculating them here makes send
            # latency grow with channel size and adds several ORM queries.
            for field_name in (
                'statuses',
                'reactions',
                'is_hidden_by_me',
                'thread_reply_count',
                'thread_last_reply_at',
                'thread_participants',
                'has_unread_thread_replies',
            ):
                self.fields.pop(field_name, None)
        elif self.context.get('omit_recipient_statuses'):
            self.fields.pop('statuses', None)

    class Meta:
        model = Message
        fields = [
            'id', 'seq', 'chat', 'sender', 'content', 'rich_body', 'status', 'statuses',
            'created_at', 'updated_at', 'is_edited', 'is_deleted', 'deleted_at', 'is_revoked', 'revoked_at',
            'has_attachments', 'attachment_count',
            'is_forwarded', 'forwarded_from', 'reply_to', 'reactions', 'can_revoke',
            'is_hidden_by_me', 'mentioned_user_ids',
            'missing_forwarded_attachments',
            'parent_message_id',
            'thread_reply_count', 'thread_last_reply_at', 'thread_participants',
            'has_unread_thread_replies',
            'link_preview',
        ]
        read_only_fields = ['id', 'seq', 'sender', 'created_at', 'updated_at']
        list_serializer_class = MessageListSerializer

    DELETED_MESSAGE_PRESERVED_FIELDS = frozenset({
        'id',
        'seq',
        'chat',
        'sender',
        'status',
        'statuses',
        'created_at',
        'updated_at',
        'is_edited',
        'is_deleted',
        'deleted_at',
        'is_revoked',
        'revoked_at',
        'is_hidden_by_me',
        'parent_message_id',
        'thread_reply_count',
        'thread_last_reply_at',
        'thread_participants',
        'has_unread_thread_replies',
    })
    DELETED_MESSAGE_REDACTIONS = {
        'content': '',
        'rich_body': None,
        'has_attachments': False,
        'attachment_count': 0,
        'is_forwarded': False,
        'forwarded_from': None,
        'reply_to': None,
        'reactions': [],
        'can_revoke': False,
        'mentioned_user_ids': [],
        'missing_forwarded_attachments': [],
        'attachments': [],
    }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self._is_redacted_message(instance):
            return data
        return self._redact_deleted_message_representation(data)

    def _redact_deleted_message_representation(self, data):
        for field in list(data.keys()):
            if field in self.DELETED_MESSAGE_PRESERVED_FIELDS:
                continue
            redacted_value = self.DELETED_MESSAGE_REDACTIONS.get(field)
            if isinstance(redacted_value, list):
                redacted_value = list(redacted_value)
            elif isinstance(redacted_value, dict):
                redacted_value = dict(redacted_value)
            data[field] = redacted_value
        return data

    def _get_prefetched_related(self, obj, related_name):
        cache = getattr(obj, '_prefetched_objects_cache', {})
        if related_name in cache:
            return list(cache[related_name])
        return None

    def _get_thread_replies_for_summary(self, obj):
        replies = getattr(obj, '_thread_replies_for_summary', None)
        if replies is not None:
            return list(replies)
        cached_replies = self._get_prefetched_related(obj, 'thread_replies')
        if cached_replies is not None:
            return [reply for reply in cached_replies if reply.chat_id == obj.chat_id]
        return None

    def _get_thread_read_status_for_user(self, obj):
        statuses = getattr(obj, '_thread_read_status_for_user', None)
        if statuses is not None:
            return statuses[0] if statuses else None
        return None

    def _get_prefetched_statuses(self, obj):
        return self._get_prefetched_related(obj, 'statuses')

    def _is_redacted_message(self, obj):
        """Deleted messages keep timeline metadata but redact user-controlled content."""
        return bool(obj.is_deleted)

    def _forward_source_is_unavailable(self, obj):
        """True when a forwarded message has lost the live source it copied from."""
        if not self.get_is_forwarded(obj):
            return False
        if not obj.forwarded_from_message_id:
            return True
        source = getattr(obj, 'forwarded_from_message', None)
        if source is None:
            return True
        return bool(source.is_deleted or source.is_revoked)

    def _attachment_kind(self, attachment):
        mime_type = (getattr(attachment, 'mime_type', '') or '').lower()
        if mime_type.startswith('audio/'):
            return 'audio'
        if mime_type.startswith('video/'):
            return 'video'
        if getattr(attachment, 'file_type', '') == 'image':
            return 'image'
        return 'document'

    def get_mentioned_user_ids(self, obj):
        mentions = self._get_prefetched_related(obj, 'mentions')
        if mentions is not None:
            return [mention.mentioned_user_id for mention in mentions]
        return list(obj.mentions.values_list('mentioned_user_id', flat=True))

    def get_link_preview(self, obj):
        """Cached OpenGraph card for this message's first URL, or None (MED-279).

        Scoped to the viewer: a user who dismissed this card gets None.
        """
        from .services import build_message_link_preview
        request = self.context.get('request')
        return build_message_link_preview(
            obj,
            getattr(request, 'user', None),
            preview_map=self.context.get('link_preview_map'),
        )

    def get_missing_forwarded_attachments(self, obj):
        """Tombstones for forwarded attachments whose original message is gone."""
        if not self._forward_source_is_unavailable(obj):
            return []

        attachments = self._get_prefetched_related(obj, 'attachments')
        if attachments is None:
            attachments = list(obj.attachments.all())

        tombstones = [
            {
                'id': attachment.id,
                'kind': self._attachment_kind(attachment),
                'original_filename': attachment.original_filename or 'Forwarded file',
                'file_size_display': MessageAttachmentSerializer().get_file_size_display(attachment),
                'reason': 'original_deleted',
            }
            for attachment in attachments
        ]

        # Older forwarded attachment rows may already have had their copied attachment
        # record deleted. If the forwarded message is attachment-only, still render a
        # visible tombstone instead of leaving only "Forwarded from ...".
        if not tombstones and not (obj.content or '').strip():
            tombstones.append({
                'id': -obj.id,
                'kind': 'unknown',
                'original_filename': 'Forwarded file',
                'file_size_display': '',
                'reason': 'original_deleted',
            })

        return tombstones
    
    def get_status(self, obj):
        """Get message status for the current user"""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 'sent'
        
        # If sender, always show as 'sent'
        if obj.sender == request.user:
            return 'sent'

        statuses = self._get_prefetched_statuses(obj)
        if statuses is not None:
            for msg_status in statuses:
                if msg_status.user_id == request.user.id:
                    return msg_status.status
            return 'sent'
        
        # Get status for current user
        try:
            msg_status = MessageStatus.objects.get(
                message=obj,
                user=request.user
            )
            return msg_status.status
        except MessageStatus.DoesNotExist:
            return 'sent'

    def get_is_forwarded(self, obj):
        """Whether message has forwarded metadata."""
        return bool(
            obj.forwarded_from_message_id
            or obj.forwarded_from_sender_display
            or obj.forwarded_from_created_at
        )

    def get_has_attachments(self, obj):
        """Whether message contains attachments."""
        if self._forward_source_is_unavailable(obj):
            return False
        attachments = self._get_prefetched_related(obj, 'attachments')
        if attachments is not None:
            return bool(obj.has_attachments or attachments)
        return bool(obj.has_attachments or obj.attachments.exists())

    def get_attachment_count(self, obj):
        """Number of attachments linked to this message."""
        if self._forward_source_is_unavailable(obj):
            return 0
        attachments = self._get_prefetched_related(obj, 'attachments')
        if attachments is not None:
            return len(attachments)
        return obj.attachments.count()

    def get_forwarded_from(self, obj):
        """Forwarded source metadata."""
        if not self.get_is_forwarded(obj):
            return None

        return {
            'message_id': obj.forwarded_from_message_id,
            'sender_display': obj.forwarded_from_sender_display or '',
            'created_at': obj.forwarded_from_created_at,
        }

    def get_reply_to(self, obj):
        """Quote reply source metadata."""
        if not obj.reply_to_id:
            return None

        reply_msg = obj.reply_to
        if not reply_msg:
            return None

        request = self.context.get('request')

        # Include the first attachment so the reply preview can show a file/audio indicator.
        reply_attachments = []
        for att in reply_msg.attachments.all()[:1]:
            file_url = None
            if att.file:
                raw_url = att.file.url
                file_url = request.build_absolute_uri(raw_url) if request else raw_url
            reply_attachments.append({
                'id': att.id,
                'file_type': att.file_type,
                'original_filename': att.original_filename,
                'file_url': file_url,
                'mime_type': att.mime_type,
            })

        return {
            'id': reply_msg.id,
            'sender': {
                'id': reply_msg.sender.id,
                'username': reply_msg.sender.username,
                'email': reply_msg.sender.email,
            },
            'content': reply_msg.content,
            'created_at': reply_msg.created_at.isoformat() if reply_msg.created_at else None,
            'attachments': reply_attachments,
        }

    def get_reactions(self, obj):
        """
        Get aggregated reactions for this message.
        Groups by emoji and includes count, users list, and whether current user reacted.
        """
        request = self.context.get('request')
        current_user_id = request.user.id if request and request.user.is_authenticated else None

        # Get all reactions for this message. The message list endpoint prefetches
        # reactions in bulk, so avoid issuing one query per message when available.
        reactions = self._get_prefetched_related(obj, 'reactions')
        if reactions is None:
            reactions = MessageReaction.objects.filter(message=obj).select_related('user')

        # Group by emoji
        emoji_groups = {}
        for reaction in reactions:
            emoji = reaction.emoji
            if emoji not in emoji_groups:
                emoji_groups[emoji] = {
                    'emoji': emoji,
                    'count': 0,
                    'users': [],
                    'reacted_by_me': False,
                }
            emoji_groups[emoji]['count'] += 1
            emoji_groups[emoji]['users'].append({
                'id': reaction.user.id,
                'username': reaction.user.username,
            })
            if current_user_id and reaction.user.id == current_user_id:
                emoji_groups[emoji]['reacted_by_me'] = True

        return list(emoji_groups.values())

    def get_can_revoke(self, obj):
        """
        Check if the current user can revoke this message.
        Rules: Must be sender, message must be inside the revoke window, and not already revoked.
        """
        from django.utils import timezone
        from datetime import timedelta

        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False

        # Check if user is sender
        if obj.sender != request.user:
            return False

        # Check if message is already revoked
        if obj.is_revoked:
            return False

        time_limit = timezone.now() - timedelta(minutes=settings.CHAT_REVOKE_WINDOW_MINUTES)
        return obj.created_at > time_limit

    def get_is_hidden_by_me(self, obj):
        """
        Check if the current user has hidden this message.
        """
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False

        hidden_by_current_user = getattr(obj, '_hidden_by_current_user', None)
        if hidden_by_current_user is not None:
            return bool(hidden_by_current_user)

        return obj.hidden_by_users.filter(id=request.user.id).exists()

    # ------------------------------------------------------------------ #
    # Thread fields                                                        #
    # ------------------------------------------------------------------ #

    def get_thread_reply_count(self, obj):
        """Number of direct thread replies on this root message."""
        if obj.parent_message_id:
            return None  # Thread replies don't have sub-threads
        if hasattr(obj, '_thread_reply_count'):
            return obj._thread_reply_count
        replies = self._get_thread_replies_for_summary(obj)
        if replies is not None:
            return len(replies)
        return obj.thread_replies.filter(chat=obj.chat).count()

    def get_thread_last_reply_at(self, obj):
        """ISO timestamp of the most recent thread reply, or None."""
        if obj.parent_message_id:
            return None
        if hasattr(obj, '_thread_last_reply_at'):
            last_reply_at = obj._thread_last_reply_at
            return last_reply_at.isoformat() if last_reply_at else None
        replies = self._get_thread_replies_for_summary(obj)
        if replies is not None:
            last = replies[-1] if replies else None
            return last.created_at.isoformat() if last else None
        last = obj.thread_replies.filter(chat=obj.chat).order_by('-created_at').first()
        return last.created_at.isoformat() if last else None

    def get_thread_participants(self, obj):
        """
        Up to 4 distinct senders in the thread (for avatar stack).
        Returns a list of {id, username, avatar} dicts.
        """
        if obj.parent_message_id:
            return []
        replies = self._get_thread_replies_for_summary(obj)
        if replies is None:
            replies = obj.thread_replies.filter(chat=obj.chat).select_related('sender').order_by('created_at')
        seen_ids = set()
        participants = []
        for reply in replies:
            if reply.sender_id not in seen_ids:
                seen_ids.add(reply.sender_id)
                try:
                    avatar_url = reply.sender.avatar.url if reply.sender.avatar else None
                except (ValueError, AttributeError):
                    avatar_url = None
                participants.append({
                    'id': reply.sender.id,
                    'username': reply.sender.username,
                    'email': reply.sender.email,
                    'avatar': avatar_url,
                })
            if len(participants) >= 4:
                break
        return participants

    def get_has_unread_thread_replies(self, obj):
        """True if this root message has thread replies newer than the user's last read."""
        if obj.parent_message_id:
            return False
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        from .models import ThreadReadStatus
        replies = self._get_thread_replies_for_summary(obj)
        if replies is not None:
            last_reply = replies[-1] if replies else None
        else:
            last_reply = obj.thread_replies.filter(chat=obj.chat).order_by('-created_at').first()
        if not last_reply:
            return False
        prefetched_status = self._get_thread_read_status_for_user(obj)
        if prefetched_status is not None:
            return last_reply.created_at > prefetched_status.last_read_at
        if hasattr(obj, '_thread_read_status_for_user'):
            return True
        try:
            trs = ThreadReadStatus.objects.get(user=request.user, root_message=obj)
            return last_reply.created_at > trs.last_read_at
        except ThreadReadStatus.DoesNotExist:
            # Never read the thread → unread if any replies exist
            return True


class MessageCreateSerializer(MessageContentValidationMixin, ChatParticipantValidationMixin, serializers.ModelSerializer):
    """Serializer for creating messages"""
    
    class Meta:
        model = Message
        fields = ['chat', 'content']
    
    def create(self, validated_data):
        """Create message and set sender"""
        request = self.context.get('request')
        validated_data['sender'] = request.user
        return super().create(validated_data)


class ChatSerializer(ChatUnreadCountMixin, serializers.ModelSerializer):
    """Serializer for chat conversations"""
    participants = serializers.SerializerMethodField()
    created_by = UserSimpleSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    mention_unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            'id', 'slug', 'project', 'type', 'name', 'topic', 'description', 'visibility',
            'created_by_id', 'created_by',
            'participants', 'last_message', 'unread_count', 'mention_unread_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_by_id', 'created_by', 'created_at', 'updated_at']
    
    def get_last_message(self, obj):
        """Get the last message in the chat"""
        last_msg = Message.objects.filter(
            chat=obj,
            is_deleted=False,
            is_revoked=False
        ).order_by('-seq').first()

        if last_msg:
            return MessageSerializer(last_msg, context=self.context).data
        return None

    def get_participants(self, obj):
        """Return active participants only."""
        participants = ChatParticipant.objects.filter(
            chat=obj,
            is_active=True,
        ).select_related('user')
        return ChatParticipantSerializer(participants, many=True, context=self.context).data


class ChatListSerializer(ChatUnreadCountMixin, serializers.ModelSerializer):
    """Simplified serializer for chat list (performance optimized)"""
    participants = serializers.SerializerMethodField()
    created_by = UserSimpleSerializer(read_only=True)
    participant_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()
    last_message_time = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    mention_unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Chat
        fields = [
            'id', 'slug', 'project', 'type', 'name', 'topic', 'description', 'visibility',
            'created_by_id', 'created_by',
            'participants', 'participant_count', 'last_message',
            'last_message_time', 'unread_count', 'mention_unread_count',
            'created_at', 'updated_at'
        ]
    
    def get_participants(self, obj):
        """Get simplified participant info with online status"""
        from .services import OnlineStatusService
        
        participants = ChatParticipant.objects.filter(
            chat=obj,
            is_active=True
        ).select_related('user')
        
        return [{
            'id': p.id,
            'user': {
                'id': p.user.id,
                'username': p.user.username,
                'email': p.user.email,
                'is_online': OnlineStatusService.is_online(p.user.id)
            },
            'joined_at': p.joined_at.isoformat() if p.joined_at else None,
            'is_manager': p.is_manager,
            'is_muted': p.is_muted,
            'muted_until': p.muted_until.isoformat() if p.muted_until else None,
            'notification_level': p.notification_level,
        } for p in participants]
    
    def get_participant_count(self, obj):
        """Get number of active participants"""
        return ChatParticipant.objects.filter(
            chat=obj,
            is_active=True
        ).count()
    
    def get_last_message(self, obj):
        """Get the last message in the chat (full message object for consistency with WebSocket)"""
        last_msg = Message.objects.filter(
            chat=obj,
            is_deleted=False,
            is_revoked=False
        ).select_related('sender', 'reply_to', 'reply_to__sender').order_by('-seq').first()

        if last_msg:
            attachment_count = last_msg.attachments.count()
            has_attachments = bool(last_msg.has_attachments or attachment_count > 0)
            is_forwarded = bool(
                last_msg.forwarded_from_message_id
                or last_msg.forwarded_from_sender_display
                or last_msg.forwarded_from_created_at
            )
            # Build reply_to payload
            reply_to_data = None
            if last_msg.reply_to_id and last_msg.reply_to:
                reply_to_data = {
                    'id': last_msg.reply_to.id,
                    'sender': {
                        'id': last_msg.reply_to.sender.id,
                        'username': last_msg.reply_to.sender.username,
                        'email': last_msg.reply_to.sender.email,
                    },
                    'content': last_msg.reply_to.content,
                    'created_at': last_msg.reply_to.created_at.isoformat() if last_msg.reply_to.created_at else None,
                }
            return {
                'id': last_msg.id,
                'seq': last_msg.seq,
                'chat_id': last_msg.chat_id,
                'sender': {
                    'id': last_msg.sender.id,
                    'username': last_msg.sender.username,
                    'email': last_msg.sender.email,
                },
                'content': last_msg.content,
                'is_forwarded': is_forwarded,
                'forwarded_from': (
                    {
                        'message_id': last_msg.forwarded_from_message_id,
                        'sender_display': last_msg.forwarded_from_sender_display or '',
                        'created_at': last_msg.forwarded_from_created_at.isoformat() if last_msg.forwarded_from_created_at else None,
                    }
                    if is_forwarded
                    else None
                ),
                'reply_to': reply_to_data,
                'has_attachments': has_attachments,
                'attachment_count': attachment_count,
                'created_at': last_msg.created_at.isoformat(),
                'updated_at': last_msg.updated_at.isoformat(),
            }
        return None
    
    def get_last_message_time(self, obj):
        """Get timestamp of last message"""
        last_msg = Message.objects.filter(
            chat=obj,
            is_deleted=False,
            is_revoked=False
        ).order_by('-seq').first()

        return last_msg.created_at if last_msg else obj.updated_at


class ChatStarSerializer(serializers.ModelSerializer):
    """Starred chat row with nested chat list payload."""

    chat = ChatListSerializer(read_only=True)

    class Meta:
        model = ChatStar
        fields = ['id', 'chat', 'position', 'created_at', 'updated_at']
        read_only_fields = fields


class ChatStarCreateSerializer(serializers.Serializer):
    chat_id = serializers.IntegerField(min_value=1)


class ChatStarReorderSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(min_value=1)
    chat_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )


class ProjectPkOrSlugField(serializers.Field):
    """Accept project pk (int/str digits) or project slug on write."""

    default_error_messages = {
        'invalid': 'Invalid project.',
    }

    def to_internal_value(self, data):
        pk = resolve_project_pk(data)
        if pk is None:
            self.fail('invalid')
        try:
            return Project.objects.get(pk=pk)
        except Project.DoesNotExist:
            self.fail('invalid')

    def to_representation(self, value):
        # to_internal_value stores a Project instance, but this can also run on a
        # raw pk (e.g. serializing a model field), so handle both forms.
        if isinstance(value, Project):
            return value.pk
        return value


class ChatCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating chats"""
    project = ProjectPkOrSlugField()
    participant_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        default=list,
        help_text="List of user IDs to add as participants (creator is added automatically)"
    )
    
    class Meta:
        model = Chat
        fields = ['project', 'type', 'name', 'participant_ids']
    
    def validate_participant_ids(self, value):
        """Validate participant IDs."""
        if not value:
            return []

        # Remove duplicates
        value = list(set(value))
        
        # Check users exist
        existing_users = User.objects.filter(id__in=value).count()
        if existing_users != len(value):
            raise serializers.ValidationError("Some user IDs are invalid")
        
        return value
    
    def validate(self, data):
        """Validate chat creation"""
        request = self.context.get('request')
        if not request:
            raise serializers.ValidationError("Request context required")
        
        chat_type = data.get('type', ChatType.PRIVATE)
        participant_ids = data.get('participant_ids', [])
        project = data.get('project')
        
        # Validate chat type and participants
        if chat_type == ChatType.PRIVATE:
            if len(participant_ids) != 1:
                raise serializers.ValidationError(
                    "Private chat must have exactly 1 participant (excluding yourself)"
                )
            
            other_user_id = participant_ids[0]
            
            # Check if users can chat
            other_user = User.objects.get(id=other_user_id)
            can_chat, reason = Chat.can_users_chat(request.user, other_user)
            if not can_chat:
                raise serializers.ValidationError(
                    f"Cannot create private chat: {reason}"
                )
            
            # Check for existing private chat between these two users in this project
            existing_chat = Chat.objects.filter(
                project=project,
                type=ChatType.PRIVATE,
                participants__user=request.user,
                participants__is_active=True
            ).filter(
                participants__user_id=other_user_id,
                participants__is_active=True
            ).first()
            
            if existing_chat:
                raise serializers.ValidationError(
                    f"A private chat with this user already exists in this project (Chat ID: {existing_chat.id})"
                )
        
        elif chat_type == ChatType.GROUP:
            if not data.get('name'):
                raise serializers.ValidationError(
                    "Group chat must have a name"
                )

            if len(data.get('name', '').strip()) > MAX_CHANNEL_NAME_LENGTH:
                raise serializers.ValidationError(
                    f"Channel name cannot be longer than {MAX_CHANNEL_NAME_LENGTH} characters"
                )

            if participant_ids:
                # Validate all participants are project members
                all_user_ids = participant_ids + [request.user.id]
                project_member_count = ProjectMember.objects.filter(
                    project=project,
                    user_id__in=all_user_ids,
                    is_active=True
                ).count()

                if project_member_count != len(all_user_ids):
                    raise serializers.ValidationError(
                        "All participants must be members of the project"
                    )
        
        return data
    
    @transaction.atomic
    def create(self, validated_data):
        """Create chat with participants"""
        request = self.context.get('request')
        participant_ids = validated_data.pop('participant_ids')
        
        # Create chat — set created_by to the requesting user
        chat = Chat.objects.create(created_by=request.user, **validated_data)
        
        # Add current user as participant
        ChatParticipant.objects.create(
            chat=chat,
            user=request.user,
            is_active=True,
            is_manager=validated_data.get('type') == ChatType.GROUP,
        )
        
        # Add other participants
        for user_id in participant_ids:
            ChatParticipant.objects.create(
                chat=chat,
                user_id=user_id,
                is_active=True
            )

        ChatService.invalidate_presence_recipients_for_chat(chat)
        
        return chat


class PinnedMessageContentSerializer(serializers.ModelSerializer):
    """Lightweight message summary required by the pinned drawer."""

    sender = UserSimpleSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ['id', 'chat', 'sender', 'content', 'created_at', 'parent_message_id']
        read_only_fields = fields


class PinnedMessageSerializer(serializers.ModelSerializer):
    """Serializer for pinned messages in a channel."""
    message = PinnedMessageContentSerializer(read_only=True)
    pinned_by = UserSimpleSerializer(read_only=True)

    class Meta:
        model = PinnedMessage
        fields = ['id', 'chat', 'message', 'pinned_by', 'created_at']
        read_only_fields = fields

class PinMessageRequestSerializer(serializers.Serializer):
    """Validate the message identifier used by pin and unpin actions."""

    # Bounded by what the column can hold, not by an arbitrary limit: Message
    # uses BigAutoField, and DRF's IntegerField accepts any Python int, so
    # without the ceiling an oversized id passes validation and only fails once
    # PostgreSQL rejects it as out of range for bigint — a 500 for what is
    # plainly a malformed request.
    message_id = serializers.IntegerField(min_value=1, max_value=2 ** 63 - 1)


class SavedMessageSerializer(serializers.ModelSerializer):
    message = serializers.SerializerMethodField()
    chat_id = serializers.IntegerField(source='message.chat_id', read_only=True)
    project_id = serializers.IntegerField(source='message.chat.project_id', read_only=True)
    chat_name = serializers.SerializerMethodField()
    chat_type = serializers.SerializerMethodField()

    class Meta:
        model = SavedMessage
        fields = ['id', 'message', 'chat_id', 'project_id', 'chat_name', 'chat_type', 'created_at']
        read_only_fields = fields

    def get_message(self, obj):
        return MessageWithAttachmentsSerializer(obj.message, context=self.context).data

    def get_chat_name(self, obj):
        chat = obj.message.chat
        if not chat:
            return None
        if chat.type == ChatType.PRIVATE:
            # For DMs show the other participant's name instead of a generic label
            request = self.context.get('request')
            if request and request.user:
                other = chat.participants.exclude(user=request.user).select_related('user').first()
                if other and other.user:
                    return other.user.username or other.user.email
            return 'Direct message'
        return chat.name or 'Unnamed channel'

    def get_chat_type(self, obj):
        chat = obj.message.chat
        if not chat:
            return None
        # Normalise to 'direct'/'group' for the frontend
        return 'direct' if chat.type == ChatType.PRIVATE else 'group'


class ChatUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating channel name/topic/description (group chats only)."""

    class Meta:
        model = Chat
        fields = ['name', 'topic', 'description', 'visibility']

    def validate_name(self, value):
        if value is not None and not value.strip():
            raise serializers.ValidationError("Channel name cannot be blank")
        cleaned = value.strip() if value else value
        if cleaned and len(cleaned) > MAX_CHANNEL_NAME_LENGTH:
            raise serializers.ValidationError(f"Channel name cannot be longer than {MAX_CHANNEL_NAME_LENGTH} characters")
        return cleaned

    def validate_topic(self, value):
        return (value or '').strip()

    def validate_description(self, value):
        return (value or '').strip()

    def validate_visibility(self, value):
        valid_values = {choice[0] for choice in ChannelVisibility.CHOICES}
        if value not in valid_values:
            raise serializers.ValidationError("Invalid channel visibility")
        return value


class MarkAsReadSerializer(serializers.Serializer):
    """Serializer for marking messages as read"""
    message_id = serializers.IntegerField(required=False, allow_null=True)
    
    def validate_message_id(self, value):
        """Validate message exists"""
        if value is not None:
            try:
                Message.objects.get(id=value)
            except Message.DoesNotExist:
                raise serializers.ValidationError("Message not found")
        return value


class ForwardBatchSerializer(serializers.Serializer):
    """Serializer for forwarding multiple messages to multiple targets"""
    source_chat_id = serializers.IntegerField(required=True)
    source_message_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=True,
        allow_empty=False,
        max_length=100
    )
    target_chat_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        default=list,
        max_length=100
    )
    target_user_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
        default=list,
        max_length=100
    )
    def validate_source_message_ids(self, value):
        """Normalize and validate source message ids"""
        unique_ids = list(dict.fromkeys(value))
        if not unique_ids:
            raise serializers.ValidationError("source_message_ids cannot be empty")
        return unique_ids

    def validate_target_chat_ids(self, value):
        """Normalize target chat ids"""
        return list(dict.fromkeys(value))

    def validate_target_user_ids(self, value):
        """Normalize target user ids"""
        return list(dict.fromkeys(value))

    def validate(self, attrs):
        target_chat_ids = attrs.get('target_chat_ids', [])
        target_user_ids = attrs.get('target_user_ids', [])

        if not target_chat_ids and not target_user_ids:
            raise serializers.ValidationError(
                "At least one target is required (target_chat_ids or target_user_ids)"
            )

        return attrs


class MessageAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for MessageAttachment model"""
    file_url = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    file_size_display = serializers.SerializerMethodField()
    
    class Meta:
        model = MessageAttachment
        fields = [
            'id', 'message', 'file_type', 'file_url', 'thumbnail_url',
            'file_size', 'file_size_display', 'original_filename',
            'mime_type', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_file_url(self, obj):
        """Return the full URL for the file"""
        message = getattr(obj, 'message', None)
        if message and (
            message.forwarded_from_sender_display
            or message.forwarded_from_created_at
            or message.forwarded_from_message_id
        ):
            source = getattr(message, 'forwarded_from_message', None)
            if not message.forwarded_from_message_id or source is None or source.is_deleted or source.is_revoked:
                return None
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None
    
    def get_thumbnail_url(self, obj):
        """Return thumbnail URL if available"""
        message = getattr(obj, 'message', None)
        if message and (
            message.forwarded_from_sender_display
            or message.forwarded_from_created_at
            or message.forwarded_from_message_id
        ):
            source = getattr(message, 'forwarded_from_message', None)
            if not message.forwarded_from_message_id or source is None or source.is_deleted or source.is_revoked:
                return None
        if hasattr(obj, 'thumbnail') and obj.thumbnail:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail.url)
            return obj.thumbnail.url
        return None
    
    def get_file_size_display(self, obj):
        """Return human-readable file size"""
        size = obj.file_size or 0
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.1f} GB"


class ChatContextSerializer(serializers.ModelSerializer):
    """Minimal chat context for Files view."""

    class Meta:
        model = Chat
        fields = ['id', 'slug', 'type', 'name']
        read_only_fields = fields


class AttachmentFileListRowSerializer(MessageAttachmentSerializer):
    """
    List-row serializer for Messages → Files view.

    Includes uploader + chat context for display. Intended for linked attachments only.
    """

    uploader = UserSimpleSerializer(read_only=True)
    chat = serializers.SerializerMethodField()
    message_id = serializers.IntegerField(read_only=True)
    thread_root_message_id = serializers.IntegerField(source='message.parent_message_id', read_only=True)
    is_orphaned_forward = serializers.SerializerMethodField()

    class Meta(MessageAttachmentSerializer.Meta):
        fields = MessageAttachmentSerializer.Meta.fields + [
            'uploader', 'chat', 'message_id', 'thread_root_message_id', 'is_orphaned_forward',
        ]

    def get_chat(self, obj):
        chat = getattr(getattr(obj, 'message', None), 'chat', None)
        if not chat:
            return None
        return ChatContextSerializer(chat).data

    def get_is_orphaned_forward(self, obj) -> bool:
        """True when this attachment came from a forwarded message whose source was deleted."""
        return bool(getattr(obj, 'is_orphaned_forward', False))


class AttachmentUploadSerializer(serializers.ModelSerializer):
    """Serializer for uploading attachments"""

    def _generate_video_thumbnail(self, attachment: MessageAttachment) -> None:
        """Generate a thumbnail image for video attachments using ffmpeg."""
        try:
            input_path = attachment.file.path
        except Exception:
            logger.warning("Video thumbnail generation skipped: storage has no local path.")
            return

        if not os.path.exists(input_path):
            logger.warning("Video thumbnail generation skipped: file not found.")
            return

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp_file:
            output_path = temp_file.name

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    input_path,
                    "-ss",
                    "00:00:01.000",
                    "-vframes",
                    "1",
                    "-vf",
                    "scale=640:-1",
                    output_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                filename = f"{os.path.splitext(attachment.original_filename)[0]}_thumb.jpg"
                with open(output_path, "rb") as handle:
                    attachment.thumbnail.save(filename, File(handle), save=True)
        except Exception as exc:
            logger.warning("Video thumbnail generation failed: %s", exc)
        finally:
            try:
                os.remove(output_path)
            except OSError:
                pass
    
    class Meta:
        model = MessageAttachment
        fields = ['id', 'file', 'file_type', 'file_size', 'original_filename', 'mime_type']
        read_only_fields = ['id', 'file_type', 'file_size', 'original_filename', 'mime_type']
    
    def create(self, validated_data):
        file = validated_data.get('file')
        
        # Auto-populate fields from the uploaded file
        validated_data['uploader'] = self.context['request'].user
        validated_data['original_filename'] = file.name if file else ''
        validated_data['file_size'] = file.size if file else 0
        validated_data['mime_type'] = getattr(file, 'content_type', 'application/octet-stream')
        
        # Determine file_type from mime_type
        mime_type = validated_data['mime_type']
        if mime_type.startswith('image/'):
            validated_data['file_type'] = 'image'
        elif mime_type.startswith('video/'):
            validated_data['file_type'] = 'video'
        else:
            validated_data['file_type'] = 'document'
        
        attachment = super().create(validated_data)
        if attachment.file_type == "video":
            self._generate_video_thumbnail(attachment)
        return attachment


class MessageWithAttachmentsSerializer(MessageSerializer):
    """Serializer for Message model with nested attachments"""
    attachments = serializers.SerializerMethodField()
    
    class Meta(MessageSerializer.Meta):
        fields = MessageSerializer.Meta.fields + ['attachments']

    def get_attachments(self, obj):
        if self._forward_source_is_unavailable(obj):
            return []
        attachments = self._get_prefetched_related(obj, 'attachments')
        if attachments is None:
            attachments = obj.attachments.all()
        return MessageAttachmentSerializer(attachments, many=True, context=self.context).data


class MessageCreateWithAttachmentsSerializer(ChatParticipantValidationMixin, serializers.ModelSerializer):
    """Serializer for creating messages with attachments"""
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        write_only=True,
        default=list
    )
    reply_to_id = serializers.IntegerField(
        required=False,
        write_only=True,
        allow_null=True,
        help_text="ID of the message being replied to (quote reply)"
    )
    parent_message_id = serializers.IntegerField(
        required=False,
        write_only=True,
        allow_null=True,
        help_text="ID of the root message this is a thread reply to"
    )
    rich_body = serializers.JSONField(
        required=False,
        allow_null=True,
        help_text="Tiptap JSON document. When provided, content is derived automatically."
    )
    mention_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        write_only=True,
        default=list,
        help_text="User IDs mentioned in this message."
    )
    client_message_id = serializers.CharField(
        required=False,
        write_only=True,
        allow_blank=True,
        allow_null=True,
        max_length=64,
        help_text="Client-generated idempotency key for retried sends.",
    )
    attachments = MessageAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = [
            'id', 'chat', 'content', 'rich_body', 'mention_ids', 'attachment_ids',
            'reply_to_id', 'parent_message_id', 'client_message_id', 'attachments', 'created_at',
        ]
        read_only_fields = ['id', 'attachments', 'created_at']

    def validate_content(self, value):
        """Allow empty content if attachments or rich_body are provided"""
        return value.strip() if value else ''

    def validate(self, data):
        """Validate that either content, rich_body, or attachments are provided.
        If rich_body is present without content, derive plain text automatically."""
        from .services import extract_message_plain_text

        rich_body = data.get('rich_body')
        content = data.get('content', '')
        attachment_ids = data.get('attachment_ids', [])

        # Derive plain text from rich_body when content not explicitly supplied
        if rich_body and not content:
            data['content'] = extract_message_plain_text(rich_body)
            content = data['content']

        if not content and not attachment_ids:
            raise serializers.ValidationError(
                "Either content, rich_body, or attachments must be provided"
            )

        mention_ids = data.get('mention_ids') or []
        chat = data.get('chat')
        if mention_ids:
            if len(mention_ids) != len(set(mention_ids)):
                raise serializers.ValidationError({
                    'mention_ids': 'Duplicate mentioned users are not allowed.'
                })

            valid_ids = set(
                ChatParticipant.objects.filter(
                    chat=chat,
                    is_active=True,
                    user_id__in=mention_ids,
                ).values_list('user_id', flat=True)
            )
            invalid_ids = sorted(set(mention_ids) - valid_ids)
            if invalid_ids:
                raise serializers.ValidationError({
                    'mention_ids': 'Mentioned users must be active participants in this chat.'
                })

        reply_to_id = data.get('reply_to_id')
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                raise serializers.ValidationError({
                    'reply_to_id': 'Reply target message not found.'
                })
            if reply_to.chat_id != chat.id:
                raise serializers.ValidationError({
                    'reply_to_id': 'Reply target message must belong to this chat.'
                })
            if reply_to.is_deleted or reply_to.is_revoked:
                raise serializers.ValidationError({
                    'reply_to_id': 'Reply target message is no longer available.'
                })

        parent_message_id = data.get('parent_message_id')
        if parent_message_id:
            try:
                parent_message = Message.objects.get(id=parent_message_id)
            except Message.DoesNotExist:
                raise serializers.ValidationError({
                    'parent_message_id': 'Thread parent message not found.'
                })
            if parent_message.chat_id != chat.id:
                raise serializers.ValidationError({
                    'parent_message_id': 'Thread parent message must belong to this chat.'
                })
            if parent_message.parent_message_id:
                raise serializers.ValidationError({
                    'parent_message_id': 'Thread replies must target a root message.'
                })
            if parent_message.is_deleted or parent_message.is_revoked:
                raise serializers.ValidationError({
                    'parent_message_id': 'Thread parent message is no longer available.'
                })

        return data

    def validate_reply_to_id(self, value):
        """Validate reply_to message exists"""
        if value is None:
            return None
        try:
            Message.objects.get(id=value)
        except Message.DoesNotExist:
            raise serializers.ValidationError("Reply target message not found")
        return value

    def create(self, validated_data):
        from .services import MessageService

        request = self.context.get('request')
        sender = request.user
        chat = validated_data['chat']

        reply_to_id = validated_data.pop('reply_to_id', None)
        parent_message_id = validated_data.pop('parent_message_id', None)
        attachment_ids = validated_data.pop('attachment_ids', [])
        mention_ids = validated_data.pop('mention_ids', [])
        client_message_id = validated_data.pop('client_message_id', None) or None
        if client_message_id == '':
            client_message_id = None

        content = validated_data.get('content', '')
        rich_body = validated_data.get('rich_body')

        message, created = MessageService.create_message_with_attachments(
            sender=sender,
            chat=chat,
            content=content,
            attachment_ids=attachment_ids,
            mention_ids=mention_ids,
            rich_body=rich_body,
            reply_to_id=reply_to_id,
            parent_message_id=parent_message_id,
            client_message_id=client_message_id,
        )
        self._message_created = created
        return message


class SetReminderSerializer(serializers.Serializer):
    """Serializer for setting a message reminder"""
    remind_at = serializers.DateTimeField(
        required=True,
        help_text="When to send the reminder notification (must be in the future)"
    )
    note = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        default='',
        help_text="Optional note for the reminder"
    )

    def validate_remind_at(self, value):
        """Validate that remind_at is in the future"""
        from django.utils import timezone
        if value <= timezone.now():
            raise serializers.ValidationError("Reminder time must be in the future")
        return value


# ── Scheduled message serializers ─────────────────────────────────────────────

class ScheduledMessageSerializer(serializers.ModelSerializer):
    """Read serializer for a scheduled message (returned on create and list)."""

    class Meta:
        model = ScheduledMessage
        fields = [
            'id',
            'chat',
            'content',
            'rich_body',
            'attachment_ids',
            'mention_ids',
            'reply_to',
            'scheduled_at',
            'status',
            'task_id',
            'created_at',
        ]
        read_only_fields = fields


class ScheduledMessageCreateSerializer(serializers.Serializer):
    """Write serializer for creating a scheduled message."""
    chat_id = serializers.IntegerField(required=True)
    content = serializers.CharField(required=False, allow_blank=True, default='')
    rich_body = serializers.JSONField(required=False, allow_null=True, default=None)
    attachment_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    mention_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    reply_to_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    scheduled_at = serializers.DateTimeField(required=True)

    def validate_scheduled_at(self, value):
        from django.utils import timezone
        if value <= timezone.now():
            raise serializers.ValidationError("scheduled_at must be in the future.")
        return value

    def validate(self, data):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            raise serializers.ValidationError("Request context required.")

        chat_id = data.get('chat_id')
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            raise serializers.ValidationError({'chat_id': 'Chat not found.'})

        if not ChatParticipant.objects.filter(chat=chat, user=request.user, is_active=True).exists():
            raise serializers.ValidationError({'chat_id': 'You are not a participant of this chat.'})

        attachment_ids = data.get('attachment_ids') or []
        if len(attachment_ids) != len(set(attachment_ids)):
            raise serializers.ValidationError({
                'attachment_ids': 'Duplicate attachment IDs are not allowed.'
            })

        if not data.get('content') and not data.get('rich_body') and not attachment_ids:
            raise serializers.ValidationError("A message must have content or at least one attachment.")

        if attachment_ids:
            valid_attachment_count = MessageAttachment.objects.filter(
                id__in=attachment_ids,
                uploader=request.user,
                message__isnull=True,
            ).count()
            if valid_attachment_count != len(attachment_ids):
                raise serializers.ValidationError({
                    'attachment_ids': 'Attachments must be unlinked files uploaded by you.'
                })

        mention_ids = data.get('mention_ids') or []
        if len(mention_ids) != len(set(mention_ids)):
            raise serializers.ValidationError({
                'mention_ids': 'Duplicate mentioned users are not allowed.'
            })

        if mention_ids:
            valid_mention_ids = set(
                ChatParticipant.objects.filter(
                    chat=chat,
                    is_active=True,
                    user_id__in=mention_ids,
                ).values_list('user_id', flat=True)
            )
            if set(mention_ids) != valid_mention_ids:
                raise serializers.ValidationError({
                    'mention_ids': 'Mentioned users must be active participants in this chat.'
                })

        reply_to_id = data.get('reply_to_id')
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                raise serializers.ValidationError({'reply_to_id': 'Reply target message not found.'})
            if reply_to.chat_id != chat.id:
                raise serializers.ValidationError({
                    'reply_to_id': 'Reply target message must belong to this chat.'
                })
            if reply_to.is_deleted or reply_to.is_revoked:
                raise serializers.ValidationError({
                    'reply_to_id': 'Reply target message is no longer available.'
                })

        return data


# ── Search result serializer ───────────────────────────────────────────────────

class MessageSearchResultSerializer(serializers.ModelSerializer):
    """Serializer for full-text message search results."""
    sender = serializers.SerializerMethodField()
    chat_name = serializers.SerializerMethodField()
    chat_type = serializers.CharField(source='chat.type', read_only=True)
    project_id = serializers.IntegerField(source='chat.project_id', read_only=True)
    highlight = serializers.SerializerMethodField()
    attachment_count = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'chat_id', 'chat_name', 'chat_type', 'project_id',
            'content', 'highlight', 'created_at',
            'sender', 'has_attachments', 'attachment_count',
        ]

    def get_sender(self, obj):
        u = obj.sender
        avatar = None
        if hasattr(u, 'profile') and u.profile.avatar:
            request = self.context.get('request')
            avatar = request.build_absolute_uri(u.profile.avatar.url) if request else u.profile.avatar.url
        return {
            'id': u.id,
            'username': u.username or '',
            'email': u.email or '',
            'avatar': avatar,
        }

    def get_chat_name(self, obj):
        chat = obj.chat
        if chat.type == 'group':
            return chat.name or 'Group Chat'
        # For private chats, return the other participant's name
        request = self.context.get('request')
        if request:
            prefetched = getattr(chat, '_prefetched_objects_cache', {}).get('participants')
            if prefetched is not None:
                other = next((participant for participant in prefetched if participant.user_id != request.user.id), None)
            else:
                other = chat.participants.exclude(user=request.user).select_related('user').first()
            if other:
                return other.user.username or other.user.email or 'Direct Message'
        return 'Direct Message'

    def get_highlight(self, obj):
        # Annotation added by the search view; fallback to truncated content
        highlight = getattr(obj, 'highlight', None)
        if highlight:
            return highlight
        content = obj.content or ''
        return content[:200] + ('…' if len(content) > 200 else '')

    def get_attachment_count(self, obj):
        if hasattr(obj, '_prefetched_objects_cache') and 'attachments' in obj._prefetched_objects_cache:
            return len(obj._prefetched_objects_cache['attachments'])
        return obj.attachments.count() if obj.has_attachments else 0
