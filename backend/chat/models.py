from django.db import models, transaction
from django.conf import settings
from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex
from django.utils import timezone
from core.models import TimeStampedModel, Project, Team
from core.slug_mixins import SluggedResourceModelMixin


class ChatType:
    """Chat type constants"""
    PRIVATE = 'private'
    GROUP = 'group'
    
    CHOICES = [
        (PRIVATE, 'Private Chat'),
        (GROUP, 'Group Chat'),
    ]


class ChannelVisibility:
    """Access policy for project-scoped group channels."""
    PUBLIC = 'public'
    MEMBER_INVITE = 'member_invite'
    MANAGER_INVITE = 'manager_invite'

    CHOICES = [
        (PUBLIC, 'Public: project members can find and join'),
        (MEMBER_INVITE, 'Invite-only: any channel member can add people'),
        (MANAGER_INVITE, 'Restricted: only managers can add people'),
    ]


class Chat(SluggedResourceModelMixin, TimeStampedModel):
    slug_source_field = 'name'
    """
    Chat model representing a conversation between users.
    All chats must be associated with a project.
    
    Access Rules:
    - Private Chat: Users can chat if they are in the same Team OR same Project
    - Group Chat: All participants must be members of the associated Project
    """
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='chats',
        help_text="Project this chat belongs to (required)"
    )
    type = models.CharField(
        max_length=20,
        choices=ChatType.CHOICES,
        default=ChatType.PRIVATE,
        help_text="Type of chat: private (1-on-1) or group"
    )
    name = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Name for group chats (optional for private chats)"
    )
    topic = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text="Short topic line shown in the channel header"
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text="Longer description shown in the channel details drawer"
    )
    visibility = models.CharField(
        max_length=32,
        choices=ChannelVisibility.CHOICES,
        default=ChannelVisibility.PUBLIC,
        help_text="Who can discover or add members to this channel"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_chats',
        help_text="User who created this channel (group chats only)"
    )

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['project', '-updated_at']),
            models.Index(fields=['type', '-updated_at']),
        ]
    
    def get_slug_source_value(self):
        if self.type == ChatType.GROUP:
            return self.name or ''
        return ''

    def __str__(self):
        if self.type == ChatType.GROUP:
            return f"Group: {self.name or 'Unnamed'} (Project: {self.project.name})"
        return f"Private Chat (Project: {self.project.name})"
    
    def get_participant_users(self):
        """Get all active participant users in this chat"""
        return [p.user for p in self.participants.filter(is_active=True).select_related('user')]
    
    def is_user_participant(self, user):
        """Check if a user is an active participant in this chat"""
        return self.participants.filter(user=user, is_active=True).exists()
    
    @staticmethod
    def can_users_chat(user1, user2):
        """
        Check if two users can create a private chat.
        
        Rules:
        - Same Team: Users are in the same Team (via TeamMember)
        - OR Same Project: Users are in the same Project (via ProjectMember)
        
        Returns: (can_chat: bool, reason: str)
        """
        from core.models import TeamMember, ProjectMember
        
        # Check if users are in the same team
        user1_teams = set(TeamMember.objects.filter(user=user1).values_list('team_id', flat=True))
        user2_teams = set(TeamMember.objects.filter(user=user2).values_list('team_id', flat=True))
        
        if user1_teams & user2_teams:  # Intersection - same team
            return True, "same_team"
        
        # Check if users are in the same project
        user1_projects = set(ProjectMember.objects.filter(user=user1, is_active=True).values_list('project_id', flat=True))
        user2_projects = set(ProjectMember.objects.filter(user=user2, is_active=True).values_list('project_id', flat=True))
        
        if user1_projects & user2_projects:  # Intersection - same project
            return True, "same_project"
        
        return False, "no_common_team_or_project"
    
    def can_user_join(self, user):
        """
        Check if a user can join this chat.
        
        For group chats: User must be a member of the associated project
        For private chats: Check via can_users_chat with existing participants
        """
        from core.models import ProjectMember
        
        if self.type == ChatType.GROUP:
            # Group chat: Must be project member
            return ProjectMember.objects.filter(
                user=user,
                project=self.project,
                is_active=True
            ).exists()
        else:
            # Private chat: Check with existing participant
            participants = self.get_participant_users()
            if participants:
                can_chat, _ = self.can_users_chat(user, participants[0])
                return can_chat
            return False


class ChatStar(TimeStampedModel):
    """
    User-starred chat for sidebar ordering (Slack-style).
    Scoped per user; position orders stars within a project (via chat.project).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_stars',
    )
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='stars',
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text='Order within starred list for this user in the chat project',
    )

    class Meta:
        unique_together = [('user', 'chat')]
        ordering = ['position', 'id']
        indexes = [
            models.Index(fields=['user', 'position']),
            models.Index(fields=['user', 'chat']),
        ]

    def __str__(self):
        return f'{self.user_id} * {self.chat_id} @ {self.position}'


class ChatParticipant(TimeStampedModel):
    """
    Model representing a user's participation in a chat.
    Tracks join time, last read time, and activity status.
    """
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='participants'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_participations'
    )
    joined_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the user joined this chat"
    )
    last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last time the user read messages in this chat (for quick unread count)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this user is still active in the chat"
    )
    is_manager = models.BooleanField(
        default=False,
        help_text="Whether this participant can manage channel members and settings"
    )
    is_muted = models.BooleanField(
        default=False,
        help_text="When True, suppress notifications for this chat for this user"
    )
    muted_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Optional expiry time for a temporary mute"
    )

    NOTIFICATION_LEVEL_CHOICES = [
        ('all', 'All messages'),
        ('mentions', 'Mentions only'),
        ('none', 'Nothing'),
    ]
    notification_level = models.CharField(
        max_length=20,
        choices=NOTIFICATION_LEVEL_CHOICES,
        default='all',
        help_text="Notification level for this chat"
    )

    class Meta:
        unique_together = ['chat', 'user']
        ordering = ['joined_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['chat', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.user.email} in {self.chat}"

    def is_currently_muted(self):
        """Return True when the participant's mute is active right now."""
        if not self.is_muted:
            return False
        if self.muted_until is None:
            return True
        return self.muted_until > timezone.now()
    
    def get_unread_count(self):
        """
        Get count of unread messages for this participant.
        Uses last_read_at for quick calculation.

        NOTE: This is the SINGLE SOURCE OF TRUTH for unread count calculation.
        All serializers and services should delegate to this method.
        """
        if not self.last_read_at:
            # Never read, count all messages except own
            return self.chat.messages.filter(
                is_deleted=False,
                is_revoked=False
            ).exclude(sender=self.user).count()

        return self.chat.messages.filter(
            created_at__gt=self.last_read_at,
            is_deleted=False,
            is_revoked=False
        ).exclude(sender=self.user).count()

    def get_unread_mention_count(self):
        """Get count of unread messages where this participant was @-mentioned."""
        query = self.chat.messages.filter(
            mentions__mentioned_user=self.user,
            is_deleted=False,
            is_revoked=False,
        ).exclude(sender=self.user)

        if self.last_read_at:
            query = query.filter(created_at__gt=self.last_read_at)

        return query.distinct().count()


class AttachmentType:
    """Attachment type constants"""
    IMAGE = 'image'
    VIDEO = 'video'
    DOCUMENT = 'document'
    
    CHOICES = [
        (IMAGE, 'Image'),
        (VIDEO, 'Video'),
        (DOCUMENT, 'Document'),
    ]


def attachment_upload_path(instance, filename):
    """Generate upload path for attachments: chat/attachments/{chat_id}/{year}/{month}/{filename}"""
    from django.utils import timezone
    now = timezone.now()
    return f"chat/attachments/{instance.message.chat_id}/{now.year}/{now.month:02d}/{filename}"


class Message(TimeStampedModel):
    """
    Message model representing a text message in a chat.
    Once created, messages are persisted and accessible to all participants.
    """
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages',
        help_text="User who sent this message"
    )
    content = models.TextField(
        blank=True,
        default='',
        help_text="Text content of the message"
    )
    has_attachments = models.BooleanField(
        default=False,
        help_text="Whether this message has attachments (for optimization)"
    )
    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='replies',
        null=True,
        blank=True,
        db_index=True,
        help_text="Message being replied to (quote reply)"
    )
    parent_message = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='thread_replies',
        null=True,
        blank=True,
        db_index=True,
        help_text="Root message this is a thread reply to. Null = root/main-timeline message."
    )
    forwarded_from_message = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        related_name='forwarded_messages',
        null=True,
        blank=True,
        db_index=True,
        help_text="Original message that this message was forwarded from"
    )
    forwarded_from_sender_display = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Snapshot of original sender display name at forward time"
    )
    forwarded_from_created_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Snapshot of original message creation time at forward time"
    )
    rich_body = models.JSONField(
        null=True,
        blank=True,
        help_text="Tiptap JSON document for rich rendering. content holds the searchable plain-text copy."
    )
    client_message_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        db_index=True,
        help_text="Client idempotency key for retried sends; NULL for legacy messages.",
    )
    seq = models.PositiveBigIntegerField(
        editable=False,
        help_text="Monotonic sequence number within the chat, used for deterministic ordering.",
    )
    is_edited = models.BooleanField(default=False, help_text="True after content has been edited")
    is_deleted = models.BooleanField(default=False, help_text="Soft delete flag")
    deleted_at = models.DateTimeField(null=True, blank=True, help_text="When the message was soft deleted")
    is_revoked = models.BooleanField(default=False, help_text="Whether the message has been revoked by sender")
    revoked_at = models.DateTimeField(null=True, blank=True, help_text="When the message was revoked")
    hidden_by_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='hidden_messages',
        blank=True,
        help_text="Users who have hidden this message (personal hide, not affecting others)"
    )
    link_preview_hidden_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='hidden_link_previews',
        blank=True,
        help_text=(
            "Users who dismissed this message's link preview card. Personal view "
            "preference: the message and the shared LinkPreview cache are untouched."
        )
    )
    # Full-text search vector — kept up-to-date via post_save signal in chat/signals.py
    search_vector = SearchVectorField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['sender', 'client_message_id'],
                name='chat_message_sender_client_msg_uniq',
            ),
            models.UniqueConstraint(
                fields=['chat', 'seq'],
                name='chat_message_chat_seq_uniq',
            ),
        ]
        indexes = [
            models.Index(fields=['chat', 'created_at']),
            models.Index(fields=['sender', 'created_at']),
            models.Index(fields=['chat', '-created_at']),  # For latest messages
            models.Index(fields=['chat', 'is_deleted']),
            models.Index(fields=['chat', 'is_revoked']),
            models.Index(fields=['parent_message', 'created_at']),  # Thread reply listing
            GinIndex(fields=['search_vector'], name='chat_msg_search_vec_idx'),
        ]
    
    def __str__(self):
        preview = self.content[:50] + '...' if len(self.content) > 50 else self.content
        return f"{self.sender.email}: {preview}"

    def save(self, *args, **kwargs):
        """Allocate a gap-tolerant, per-chat sequence before the first insert.

        Locking the parent chat serializes concurrent message creation for the
        same room.  All creation paths (REST, websocket, scheduled delivery,
        and management code) therefore share one ordering rule.
        """
        if self._state.adding and self.seq is None:
            with transaction.atomic():
                Chat.objects.select_for_update().only('pk').get(pk=self.chat_id)
                current_max = (
                    type(self).objects
                    .filter(chat_id=self.chat_id)
                    .order_by('-seq')
                    .values_list('seq', flat=True)
                    .first()
                )
                self.seq = (current_max or 0) + 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)
    
    def get_status_for_user(self, user):
        """Get the status of this message for a specific user"""
        try:
            status = self.statuses.get(user=user)
            return status.status
        except MessageStatus.DoesNotExist:
            return None


class MessageStatus(TimeStampedModel):
    """
    Tracks the delivery and read status of messages for each recipient.
    
    Status Flow:
    - sent: Message created (default)
    - delivered: Message delivered via WebSocket or user came online
    - read: User has read the message
    
    This enables:
    - "✓" (sent)
    - "✓✓" (delivered) 
    - "✓✓" blue (read)
    - Group chat: "Read by 3 of 5"
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='statuses',
        help_text="The message this status refers to"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_statuses',
        help_text="The recipient user"
    )
    
    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='sent',
        help_text="Current status of the message for this user"
    )
    
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the message was delivered to the user"
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user read the message"
    )
    
    class Meta:
        unique_together = ['message', 'user']
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['message', 'status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['message', 'user']),
        ]
        verbose_name_plural = 'Message statuses'
    
    def __str__(self):
        return f"{self.message.id} - {self.user.email}: {self.status}"
    
    def mark_as_delivered(self):
        """Mark message as delivered"""
        from django.utils import timezone
        if self.status == 'sent':
            self.status = 'delivered'
            self.delivered_at = timezone.now()
            self.save(update_fields=['status', 'delivered_at', 'updated_at'])
    
    def mark_as_read(self):
        """Mark message as read"""
        from django.utils import timezone
        if self.status in ['sent', 'delivered']:
            self.status = 'read'
            self.read_at = timezone.now()
            if not self.delivered_at:
                self.delivered_at = self.read_at
            self.save(update_fields=['status', 'delivered_at', 'read_at', 'updated_at'])


class ChatOutboxEvent(models.Model):
    """Durable hand-off between a committed tenant message and Celery.

    The table deliberately lives in ``public`` (this model is not listed in
    ``core.tenant_config``), allowing one dispatcher to serve every tenant.
    """

    EVENT_MESSAGE_REALTIME = 'message.realtime'
    EVENT_MESSAGE_NOTIFICATIONS = 'message.notifications'
    EVENT_CHOICES = [
        (EVENT_MESSAGE_REALTIME, 'Message realtime delivery'),
        (EVENT_MESSAGE_NOTIFICATIONS, 'Message persisted notifications'),
    ]

    tenant_schema = models.CharField(max_length=128)
    event_type = models.CharField(max_length=64, choices=EVENT_CHOICES)
    aggregate_id = models.PositiveBigIntegerField(help_text='Tenant Message primary key')
    available_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant_schema', 'event_type', 'aggregate_id'],
                name='chat_outbox_tenant_event_message_uniq',
            ),
        ]
        indexes = [
            models.Index(
                fields=['published_at', 'available_at', 'claimed_at'],
                name='chat_outbox_pending_idx',
            ),
        ]

    def __str__(self):
        return f'{self.tenant_schema}:{self.event_type}:{self.aggregate_id}'


class MessageMention(TimeStampedModel):
    """
    Structured mention relation for chat messages.
    Enables mention notifications, search, and unread badge logic.
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='mentions',
    )
    mentioned_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_mentions',
    )

    class Meta:
        unique_together = ['message', 'mentioned_user']
        indexes = [
            models.Index(fields=['mentioned_user', 'created_at'], name='chat_mention_user_idx'),
        ]

    def __str__(self):
        return f"Mention {self.mentioned_user_id} in message {self.message_id}"


class MessageReaction(TimeStampedModel):
    """
    Emoji reactions on messages.
    Each user can add one reaction per emoji per message.
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='reactions',
        help_text="The message this reaction is on"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_reactions',
        help_text="User who reacted"
    )
    emoji = models.CharField(
        max_length=10,
        help_text="Emoji character (e.g., 😊, 👍)"
    )

    class Meta:
        unique_together = ['message', 'user', 'emoji']
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['message', 'emoji']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return f"{self.user.email} reacted {self.emoji} on message {self.message_id}"


class ThreadReadStatus(TimeStampedModel):
    """
    Tracks when a user last read the thread replies of a root message.
    Used to compute has_unread_thread_replies per user.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='thread_read_statuses',
    )
    root_message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='thread_read_statuses',
        help_text="The parent (root) message whose thread was read.",
    )
    last_read_at = models.DateTimeField(
        help_text="Timestamp of the latest thread reply seen by this user.",
    )

    class Meta:
        unique_together = ['user', 'root_message']
        indexes = [
            models.Index(fields=['user', 'root_message']),
        ]

    def __str__(self):
        return f"{self.user.email} read thread {self.root_message_id} at {self.last_read_at}"


def temp_attachment_upload_path(instance, filename):
    """Generate upload path for temporary attachments before message is created"""
    from django.utils import timezone
    import uuid
    now = timezone.now()
    unique_id = uuid.uuid4().hex[:8]
    return f"chat/temp_attachments/{now.year}/{now.month:02d}/{unique_id}_{filename}"


class MessageAttachment(TimeStampedModel):
    """
    Attachment model for files attached to messages.
    Supports images, videos, and documents.
    
    Upload Flow:
    1. User selects file in frontend
    2. Frontend uploads file to /api/chat/attachments/ (creates temp attachment)
    3. User sends message with attachment IDs
    4. Backend links attachments to message
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='attachments',
        null=True,
        blank=True,
        help_text="The message this attachment belongs to (null for temp uploads)"
    )
    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='uploaded_attachments',
        help_text="User who uploaded this attachment"
    )
    file = models.FileField(
        upload_to=temp_attachment_upload_path,
        help_text="The uploaded file"
    )
    file_type = models.CharField(
        max_length=20,
        choices=AttachmentType.CHOICES,
        default=AttachmentType.DOCUMENT,
        help_text="Type of attachment: image, video, or document"
    )
    file_size = models.PositiveIntegerField(
        default=0,
        help_text="File size in bytes"
    )
    original_filename = models.CharField(
        max_length=255,
        help_text="Original filename as uploaded"
    )
    mime_type = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text="MIME type of the file"
    )
    # AI-generated transcript for audio clips
    transcript = models.TextField(
        null=True,
        blank=True,
        default=None,
        help_text="AI-generated transcript for audio attachments"
    )
    # Optional thumbnail for images/videos
    thumbnail = models.ImageField(
        upload_to='chat/thumbnails/',
        null=True,
        blank=True,
        help_text="Thumbnail for images and videos"
    )
    
    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['message', 'created_at']),
            models.Index(fields=['uploader', 'created_at']),
            models.Index(fields=['file_type']),
        ]
    
    def __str__(self):
        return f"{self.original_filename} ({self.file_type})"
    
    @property
    def file_url(self):
        """Get the URL for the file"""
        if self.file:
            return self.file.url
        return None
    
    @property
    def thumbnail_url(self):
        """Get the URL for the thumbnail"""
        if self.thumbnail:
            return self.thumbnail.url
        return None
    
    @property
    def file_size_display(self):
        """Human-readable file size"""
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    @classmethod
    def get_file_type_from_mime(cls, mime_type):
        """Determine file type from MIME type"""
        if not mime_type:
            return AttachmentType.DOCUMENT
        
        mime_lower = mime_type.lower()
        if mime_lower.startswith('image/'):
            return AttachmentType.IMAGE
        elif mime_lower.startswith('video/'):
            return AttachmentType.VIDEO
        else:
            return AttachmentType.DOCUMENT
    
    @classmethod
    def validate_file(cls, file, file_type=None):
        """
        Validate file size and type.
        Returns (is_valid, error_message)
        """
        # File size limits in bytes
        SIZE_LIMITS = {
            AttachmentType.IMAGE: 10 * 1024 * 1024,     # 10 MB
            AttachmentType.VIDEO: 25 * 1024 * 1024,    # 25 MB
            AttachmentType.DOCUMENT: 20 * 1024 * 1024, # 20 MB
        }
        
        # Allowed MIME types
        ALLOWED_MIMES = {
            AttachmentType.IMAGE: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
            AttachmentType.VIDEO: ['video/mp4', 'video/webm', 'video/quicktime', 'video/x-msvideo'],
            AttachmentType.DOCUMENT: [
                'application/pdf',
                'application/msword',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/vnd.ms-excel',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'application/vnd.ms-powerpoint',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                'audio/mp4',
                'audio/mpeg',
                'audio/ogg',
                'audio/wav',
                'audio/webm',
                'text/plain',
                'text/csv',
            ],
        }
        
        # Determine file type from content type if not provided
        content_type = getattr(file, 'content_type', '')
        if not file_type:
            file_type = cls.get_file_type_from_mime(content_type)
        
        # Check file size
        max_size = SIZE_LIMITS.get(file_type, SIZE_LIMITS[AttachmentType.DOCUMENT])
        if file.size > max_size:
            max_mb = max_size / (1024 * 1024)
            return False, f"File too large. Maximum size for {file_type} is {max_mb:.0f} MB"
        
        # Check MIME type
        allowed = ALLOWED_MIMES.get(file_type, ALLOWED_MIMES[AttachmentType.DOCUMENT])
        if content_type and content_type.lower() not in allowed:
            return False, f"File type '{content_type}' is not allowed for {file_type}"

        return True, None


class PinnedMessage(TimeStampedModel):
    """
    A message pinned in a chat channel.
    Only one pin record per message per chat (unique_together enforced).
    """
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='pinned_messages',
        help_text="Chat channel this pin belongs to",
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='pins',
        help_text="Message that was pinned",
    )
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='pinned_messages',
        help_text="User who pinned the message",
    )

    class Meta:
        unique_together = ['chat', 'message']
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['chat', '-created_at']),
        ]

    def __str__(self):
        return f"Pin: message {self.message_id} in chat {self.chat_id}"


class SavedMessage(TimeStampedModel):
    """
    User-bookmarked message ('save for later').
    One record per (user, message) pair.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_messages',
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='saves',
    )

    class Meta:
        unique_together = ['user', 'message']
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    def __str__(self):
        return f"{self.user_id} saved message {self.message_id}"


class MessageReminder(TimeStampedModel):
    """
    Reminder for a message that should notify the user at a specific time.
    Users can set reminders to be notified about messages later.
    """
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='reminders',
        help_text="The message to be reminded about"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='message_reminders',
        help_text="User who set the reminder"
    )
    remind_at = models.DateTimeField(
        help_text="When to send the reminder notification"
    )
    note = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Optional note for the reminder"
    )

    # Status tracking
    is_sent = models.BooleanField(
        default=False,
        help_text="Whether the reminder notification has been sent"
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the reminder notification was sent"
    )

    class Meta:
        unique_together = ['message', 'user']
        ordering = ['remind_at']
        indexes = [
            models.Index(fields=['is_sent', 'remind_at']),  # For periodic task queries
            models.Index(fields=['user', 'is_sent']),
            models.Index(fields=['message', 'user']),
        ]

    def __str__(self):
        return f"Reminder for {self.user.email} on message {self.message_id} at {self.remind_at}"


class ScheduledMessage(TimeStampedModel):
    """
    A message queued to be sent at a specific future time (schedule-send).
    A Celery task is dispatched with eta=scheduled_at and updates status on completion.
    """
    chat = models.ForeignKey(
        Chat,
        on_delete=models.CASCADE,
        related_name='scheduled_messages',
        help_text="Chat the message will be sent to",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scheduled_messages',
        help_text="User who scheduled the message",
    )
    content = models.TextField(
        blank=True,
        default='',
        help_text="Plain-text content (mirrors rich_body for search/display)",
    )
    rich_body = models.JSONField(
        null=True,
        blank=True,
        help_text="Tiptap JSON document (same format as Message.rich_body)",
    )
    attachment_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of MessageAttachment IDs to link when the message is created",
    )
    mention_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of user IDs to @-mention",
    )
    reply_to = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduled_replies',
        help_text="Message being quote-replied to",
    )
    scheduled_at = models.DateTimeField(
        help_text="When the message should be sent (UTC)",
        db_index=True,
    )

    STATUS_PENDING = 'pending'
    STATUS_SENDING = 'sending'
    STATUS_SENT = 'sent'
    STATUS_CANCELLED = 'cancelled'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENDING, 'Sending'),
        (STATUS_SENT, 'Sent'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_FAILED, 'Failed'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    sent_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='from_scheduled',
        help_text="The Message created when this scheduled send fired",
    )
    task_id = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Celery task ID — used to revoke the task on cancellation",
    )
    error_message = models.TextField(
        blank=True,
        default='',
        help_text="Error detail if status=failed",
    )

    class Meta:
        ordering = ['scheduled_at']
        indexes = [
            models.Index(fields=['sender', 'status', 'scheduled_at']),
            models.Index(fields=['chat', 'status', 'scheduled_at']),
        ]

    def __str__(self):
        return f"ScheduledMessage by {self.sender_id} in chat {self.chat_id} at {self.scheduled_at} [{self.status}]"


class LinkPreview(TimeStampedModel):
    """Cached OpenGraph metadata for a URL posted in chat (MED-279).

    Keyed by the *normalized* URL and shared across every message that mentions
    it, so a link posted a hundred times is fetched once. Messages do not own a
    preview; they look one up by their URL at render time (read-through).

    Outcomes are stored as well as successes: a URL that failed or was refused by
    the SSRF guard is remembered too, so a bad link costs one fetch rather than
    one per mention.
    """

    STATUS_PENDING = 'pending'
    STATUS_READY = 'ready'
    STATUS_FAILED = 'failed'
    STATUS_BLOCKED = 'blocked'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Fetch in progress'),
        (STATUS_READY, 'Fetched'),
        (STATUS_FAILED, 'Upstream fetch failed'),
        (STATUS_BLOCKED, 'Refused by the URL safety guard'),
    ]

    url = models.TextField(
        unique=True,
        help_text="Normalized URL (fragment stripped, host lowercased) — the cache key",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        help_text="Outcome of the last fetch attempt",
    )
    title = models.TextField(null=True, blank=True, help_text="og:title, or the <title> tag")
    description = models.TextField(null=True, blank=True, help_text="og:description")
    image_url = models.TextField(null=True, blank=True, help_text="og:image — hotlinked by the client")
    fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the last attempt finished; basis for the 24h freshness window",
    )

    class Meta:
        indexes = [
            models.Index(fields=['status', 'fetched_at']),
        ]

    def __str__(self):
        return f"LinkPreview({self.url}) [{self.status}]"
