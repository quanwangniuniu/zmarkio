from django.db import models
from core.slug_mixins import SluggedResourceModelMixin
from django.contrib.auth import get_user_model
from django.core.validators import MinLengthValidator
from django.utils import timezone
import json

User = get_user_model()


class Draft(SluggedResourceModelMixin, models.Model):
    """
    Notion-style draft document model
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('archived', 'Archived'),
    ]
    
    title = models.CharField(
        max_length=200,
        validators=[MinLengthValidator(1)],
        help_text="Draft title"
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notion_drafts',
        help_text="Owner of this draft"
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        help_text="Current status of the draft"
    )
    content_blocks = models.JSONField(
        default=list,
        help_text="JSON array of content blocks"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['updated_at']),
            models.Index(fields=['is_deleted']),
        ]
    
    def __str__(self):
        return self.title
    
    def get_content_blocks_count(self):
        """Get the number of content blocks"""
        if isinstance(self.content_blocks, list):
            return len(self.content_blocks)
        return 0
    
    def add_content_block(self, block_data):
        """Add a new content block"""
        if not isinstance(self.content_blocks, list):
            self.content_blocks = []
        
        # Generate unique ID for the block
        block_id = f"block_{len(self.content_blocks) + 1}_{self.id}"
        block_data['id'] = block_id
        self.content_blocks.append(block_data)
        self.save()
        return block_id
    
    def update_content_block(self, block_id, block_data):
        """Update an existing content block"""
        if not isinstance(self.content_blocks, list):
            return False
        
        for i, block in enumerate(self.content_blocks):
            if block.get('id') == block_id:
                block_data['id'] = block_id
                self.content_blocks[i] = block_data
                self.save()
                return True
        return False
    
    def delete_content_block(self, block_id):
        """Delete a content block"""
        if not isinstance(self.content_blocks, list):
            return False
        
        for i, block in enumerate(self.content_blocks):
            if block.get('id') == block_id:
                del self.content_blocks[i]
                self.save()
                return True
        return False


class ContentBlock(models.Model):
    """
    Individual content block model for more structured storage
    """
    BLOCK_TYPES = [
        ('text', 'Plain Text'),
        ('rich_text', 'Rich Text'),
        ('heading', 'Heading'),
        ('list', 'List'),
        ('quote', 'Quote'),
        ('code', 'Code Block'),
        ('divider', 'Divider'),
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('file', 'File'),
        ('web_bookmark', 'Web Bookmark'),
    ]
    
    draft = models.ForeignKey(
        Draft,
        on_delete=models.CASCADE,
        related_name='blocks',
        help_text="Parent draft"
    )
    block_type = models.CharField(
        max_length=20,
        choices=BLOCK_TYPES,
        default='text',
        help_text="Type of content block"
    )
    content = models.JSONField(
        default=dict,
        help_text="Block content and formatting"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Order of the block in the draft"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['order', 'created_at']
        indexes = [
            models.Index(fields=['draft', 'order']),
            models.Index(fields=['block_type']),
        ]
    
    def __str__(self):
        return f"{self.get_block_type_display()} block in {self.draft.title}"
    
    def get_text_content(self):
        """Extract plain text content from the block"""
        if self.block_type == 'text':
            return self.content.get('text', '')
        elif self.block_type == 'rich_text':
            # Extract text from Notion-style rich text formatting
            text_parts = []
            for item in self.content.get('content', []):
                if isinstance(item, dict):
                    # Support both old format and Notion API format
                    if 'text' in item:
                        # Old format: {'text': 'content'}
                        if isinstance(item['text'], str):
                            text_parts.append(item['text'])
                        # Notion format: {'text': {'content': 'Some words', 'link': null}}
                        elif isinstance(item['text'], dict):
                            text_parts.append(item['text'].get('content', ''))
                    # Support direct plain_text field
                    elif 'plain_text' in item:
                        text_parts.append(item['plain_text'])
            return ''.join(text_parts)
        return str(self.content)

    def validate_notion_content(self):
        """
        Validate content structure for Notion-style rich text blocks
        Returns tuple (is_valid, error_message)
        """
        if self.block_type != 'rich_text':
            return True, None

        if not isinstance(self.content, dict):
            return False, "Content must be a dictionary"

        content_list = self.content.get('content', [])
        if not isinstance(content_list, list):
            return False, "Content must contain a 'content' list"

        for i, item in enumerate(content_list):
            if not isinstance(item, dict):
                return False, f"Content item {i} must be a dictionary"

            # Check for required fields in Notion format
            if 'type' in item:
                # Notion API format validation
                if item['type'] == 'text':
                    if 'text' not in item:
                        return False, f"Text type item {i} must have 'text' field"
                    text_obj = item['text']
                    if not isinstance(text_obj, dict):
                        return False, f"Text field in item {i} must be a dictionary"
                    if 'content' not in text_obj:
                        return False, f"Text object in item {i} must have 'content' field"

                # Validate annotations if present
                if 'annotations' in item:
                    annotations = item['annotations']
                    if not isinstance(annotations, dict):
                        return False, f"Annotations in item {i} must be a dictionary"

                    # Check valid annotation fields
                    valid_annotations = ['bold', 'italic', 'strikethrough', 'underline', 'code', 'color']
                    for key in annotations:
                        if key not in valid_annotations:
                            return False, f"Invalid annotation '{key}' in item {i}"

        return True, None

    def get_notion_formatted_html(self):
        """
        Convert Notion-style rich text content to HTML
        Returns HTML string with proper formatting
        """
        if self.block_type != 'rich_text':
            return self.get_text_content()

        html_parts = []
        content_list = self.content.get('content', [])

        for item in content_list:
            if not isinstance(item, dict):
                continue

            text = ''
            annotations = {}

            # Extract text content
            if 'text' in item:
                if isinstance(item['text'], str):
                    text = item['text']
                elif isinstance(item['text'], dict):
                    text = item['text'].get('content', '')
            elif 'plain_text' in item:
                text = item['plain_text']

            # Extract annotations
            if 'annotations' in item:
                annotations = item['annotations']

            # Build HTML with annotations
            formatted_text = text

            # Apply formatting based on annotations
            if annotations.get('code'):
                formatted_text = f'<code>{formatted_text}</code>'
            if annotations.get('bold'):
                formatted_text = f'<strong>{formatted_text}</strong>'
            if annotations.get('italic'):
                formatted_text = f'<em>{formatted_text}</em>'
            if annotations.get('strikethrough'):
                formatted_text = f'<s>{formatted_text}</s>'
            if annotations.get('underline'):
                formatted_text = f'<u>{formatted_text}</u>'

            # Apply color if not default
            color = annotations.get('color', 'default')
            if color != 'default':
                formatted_text = f'<span style="color: {color};">{formatted_text}</span>'

            # Handle links
            href = item.get('href') or (item.get('text', {}).get('link') if isinstance(item.get('text'), dict) else None)
            if href:
                formatted_text = f'<a href="{href}">{formatted_text}</a>'

            html_parts.append(formatted_text)

        return ''.join(html_parts)


class DraftRevision(models.Model):
    """
    Version history for drafts - stores snapshots of draft content
    """
    draft = models.ForeignKey(
        Draft,
        on_delete=models.CASCADE,
        related_name='revisions',
        help_text="Parent draft"
    )
    title = models.CharField(
        max_length=200,
        help_text="Draft title at this revision"
    )
    content_blocks = models.JSONField(
        default=list,
        help_text="Snapshot of content blocks at this revision"
    )
    status = models.CharField(
        max_length=20,
        help_text="Draft status at this revision"
    )
    revision_number = models.PositiveIntegerField(
        default=1,
        help_text="Sequential revision number"
    )
    change_summary = models.TextField(
        blank=True,
        help_text="Summary of changes in this revision"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='draft_revisions',
        help_text="User who created this revision"
    )

    class Meta:
        ordering = ['-created_at', '-revision_number']
        indexes = [
            models.Index(fields=['draft', '-created_at']),
            models.Index(fields=['draft', 'revision_number']),
        ]
        unique_together = ['draft', 'revision_number']

    def __str__(self):
        return f"{self.draft.title} - Revision {self.revision_number}"

    def get_content_preview(self, max_length=100):
        """Get a preview of the content"""
        if not self.content_blocks:
            return "Empty draft"

        first_block = self.content_blocks[0] if isinstance(self.content_blocks, list) else {}
        content = str(first_block.get('content', ''))[:max_length]
        return content + '...' if len(content) == max_length else content


class BlockAction(models.Model):
    """
    Inline button actions for content blocks
    """
    ACTION_TYPES = [
        ('preview', 'Preview'),
        ('save', 'Save'),
        ('delete', 'Delete'),
        ('edit', 'Edit'),
        ('duplicate', 'Duplicate'),
        ('move_up', 'Move Up'),
        ('move_down', 'Move Down'),
    ]

    block = models.ForeignKey(
        ContentBlock,
        on_delete=models.CASCADE,
        related_name='actions',
        help_text="Content block this action belongs to"
    )
    action_type = models.CharField(
        max_length=20,
        choices=ACTION_TYPES,
        help_text="Type of action"
    )
    label = models.CharField(
        max_length=50,
        help_text="Display label for the action button"
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon class or name for the button"
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text="Whether this action is currently enabled"
    )
    order = models.PositiveIntegerField(
        default=0,
        help_text="Order of the action button"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['order', 'created_at']
        unique_together = ['block', 'action_type']
        indexes = [
            models.Index(fields=['block', 'order']),
            models.Index(fields=['action_type']),
        ]
    
    def __str__(self):
        return f"{self.get_action_type_display()} action for {self.block}"


class MediaFile(models.Model):
    """
    Model for storing media files uploaded to Notion editor blocks
    """
    MEDIA_TYPES = [
        ('image', 'Image'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('file', 'File'),
    ]
    
    # File status for virus scanning
    READY = 'ready'
    INFECTED = 'infected'
    ERROR_SCANNING = 'error_scanning'
    
    SCAN_STATUS_CHOICES = [
        (READY, 'Ready - File is safe and available'),
        (INFECTED, 'Infected - File contains virus/malware'),
        (ERROR_SCANNING, 'ErrorScanning - Scanner error occurred'),
    ]
    
    file = models.FileField(
        upload_to='notion_editor/media/%Y/%m/%d/',
        max_length=500,
        help_text="Uploaded media file"
    )
    media_type = models.CharField(
        max_length=20,
        choices=MEDIA_TYPES,
        help_text="Type of media file"
    )
    original_filename = models.CharField(
        max_length=255,
        help_text="Original filename before upload"
    )
    file_size = models.PositiveIntegerField(
        help_text="File size in bytes"
    )
    content_type = models.CharField(
        max_length=100,
        blank=True,
        help_text="MIME type of the file"
    )
    scan_status = models.CharField(
        max_length=20,
        choices=SCAN_STATUS_CHOICES,
        default=READY,
        help_text="Virus scan status of the file"
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notion_media_files',
        help_text="User who uploaded the file"
    )
    draft = models.ForeignKey(
        Draft,
        on_delete=models.CASCADE,
        related_name='media_files',
        null=True,
        blank=True,
        help_text="Draft this file belongs to (optional)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['media_type']),
            models.Index(fields=['uploaded_by', 'created_at']),
            models.Index(fields=['draft']),
            models.Index(fields=['scan_status']),
        ]
    
    def __str__(self):
        return f"{self.get_media_type_display()}: {self.original_filename}"
    
    def get_file_url(self):
        """Get the file URL"""
        return self.file.url if self.file else None
    
    def get_file_name(self):
        """Get the file name"""
        return self.file.name if self.file else None


class NotionConnection(models.Model):
    """
    Stores OAuth connection info for a user's Notion workspace.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='notion_connection',
    )
    workspace_id = models.CharField(max_length=128, blank=True, null=True)
    workspace_name = models.CharField(max_length=255, blank=True, null=True)
    workspace_icon = models.URLField(blank=True, null=True)
    bot_id = models.CharField(max_length=128, blank=True, null=True)
    bot_name = models.CharField(max_length=255, blank=True, null=True)
    encrypted_access_token = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=False)
    connected_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notion_connections'
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['workspace_id']),
        ]

    def __str__(self):
        return f"NotionConnection(user={self.user_id}, active={self.is_active})"

    def set_access_token(self, token: str | None):
        from google_docs_integration.crypto import encrypt_token

        self.encrypted_access_token = encrypt_token(token)

    def get_access_token(self) -> str | None:
        from google_docs_integration.crypto import decrypt_token

        return decrypt_token(self.encrypted_access_token)

    def disconnect(self):
        self.is_active = False
        self.workspace_id = None
        self.workspace_name = None
        self.workspace_icon = None
        self.bot_id = None
        self.bot_name = None
        self.set_access_token(None)
        self.connected_at = None
        self.save(
            update_fields=[
                'is_active',
                'workspace_id',
                'workspace_name',
                'workspace_icon',
                'bot_id',
                'bot_name',
                'encrypted_access_token',
                'connected_at',
                'updated_at',
            ]
        )


class DraftProjectLink(models.Model):
    """
    Assigns a Draft to at most one Project at a time.

    Draft intentionally has no `project` FK on the model itself (Drafts predate
    project scoping and most existing rows have no project). This is a
    persisted ownership record, not derived/rebuildable RAG index state, which
    is why it lives here next to Draft rather than in the `rag` app.

    `draft` is a OneToOneField (not a FK) because a Draft can belong to at
    most one Project at a time; `project` is a plain FK because a Project can
    have many linked Drafts. A Draft with no row here is unassigned, and RAG
    indexing must skip it rather than guess a project.
    """

    draft = models.OneToOneField(
        Draft,
        on_delete=models.CASCADE,
        related_name='project_link',
        help_text="The draft assigned to a project.",
    )
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='draft_links',
        help_text="The project this draft is assigned to.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Draft {self.draft_id} -> Project {self.project_id}"

