from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import Draft, ContentBlock, BlockAction, DraftRevision, MediaFile, DraftProjectLink

User = get_user_model()


class BlockActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlockAction
        fields = [
            'id', 'action_type', 'label', 'icon', 
            'is_enabled', 'order', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class ContentBlockSerializer(serializers.ModelSerializer):
    actions = BlockActionSerializer(many=True, read_only=True)
    text_content = serializers.SerializerMethodField()
    
    class Meta:
        model = ContentBlock
        fields = [
            'id', 'draft', 'block_type', 'content', 'order', 
            'actions', 'text_content', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_text_content(self, obj):
        """Get plain text content from the block"""
        if hasattr(obj, 'get_text_content'):
            return obj.get_text_content()
        # For serialization, extract text from content dict
        if isinstance(obj, dict):
            content = obj.get('content', {})
            if isinstance(content, dict) and 'text' in content:
                return content['text']
        return str(obj.get('content', ''))


class DraftSerializer(serializers.ModelSerializer):
    blocks = ContentBlockSerializer(many=True, read_only=True)
    content_blocks_count = serializers.SerializerMethodField()
    user_email = serializers.CharField(source='user.email', read_only=True)
    project_id = serializers.SerializerMethodField()
    project_slug = serializers.SerializerMethodField()
    
    class Meta:
        model = Draft
        fields = ['slug', 
            'id', 'title', 'user', 'user_email', 'status', 
            'content_blocks', 'blocks', 'content_blocks_count',
            'created_at', 'updated_at', 'is_deleted',
            'project_id', 'project_slug',
        ]
        read_only_fields = ['slug', 'id', 'created_at', 'updated_at']

    def _project_link(self, obj):
        try:
            return obj.project_link
        except DraftProjectLink.DoesNotExist:
            return None

    def get_project_id(self, obj):
        link = self._project_link(obj)
        return link.project_id if link else None

    def get_project_slug(self, obj):
        link = self._project_link(obj)
        return link.project.slug if link else None
    
    def get_content_blocks_count(self, obj):
        """Get the number of content blocks"""
        if isinstance(obj.content_blocks, list):
            return len(obj.content_blocks)
        return 0
    
    def validate_content_blocks(self, value):
        """Validate content blocks JSON structure"""
        if not isinstance(value, list):
            raise serializers.ValidationError("Content blocks must be a list")
        
        for i, block in enumerate(value):
            if not isinstance(block, dict):
                raise serializers.ValidationError(f"Block {i} must be a dictionary")
            
            if 'type' not in block:
                raise serializers.ValidationError(f"Block {i} must have a 'type' field")
            
            if 'content' not in block:
                raise serializers.ValidationError(f"Block {i} must have a 'content' field")
        
        return value


class DraftListSerializer(serializers.ModelSerializer):
    """Simplified serializer for list views"""
    content_blocks_count = serializers.SerializerMethodField()
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = Draft
        fields = [
            'id', 'slug', 'title', 'user_email', 'status',
            'content_blocks_count', 'created_at', 'updated_at'
        ]
    
    def get_content_blocks_count(self, obj):
        if isinstance(obj.content_blocks, list):
            return len(obj.content_blocks)
        return 0


class CreateDraftSerializer(serializers.ModelSerializer):
    """Serializer for creating new drafts"""
    class Meta:
        model = Draft
        fields = ['id', 'slug', 'title', 'status', 'content_blocks']
        read_only_fields = ['id', 'slug']
    
    def create(self, validated_data):
        """Create a new draft with the current user"""
        validated_data['user'] = self.context['request'].user
        return super().create(validated_data)


class UpdateDraftSerializer(serializers.ModelSerializer):
    """Serializer for updating drafts"""
    class Meta:
        model = Draft
        fields = ['id', 'title', 'status', 'content_blocks']
        read_only_fields = ['id']


class BlockActionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating block actions"""
    class Meta:
        model = BlockAction
        fields = ['block', 'action_type', 'label', 'icon', 'is_enabled', 'order']
    
    def validate(self, data):
        """Validate that the block belongs to the current user's draft"""
        block = data.get('block')
        if block and hasattr(self.context.get('request'), 'user'):
            user = self.context['request'].user
            if block.draft.user != user:
                raise serializers.ValidationError("You can only add actions to your own drafts")
        return data


class DraftRevisionSerializer(serializers.ModelSerializer):
    """Serializer for draft revisions"""
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    content_preview = serializers.SerializerMethodField()

    class Meta:
        model = DraftRevision
        fields = [
            'id', 'draft', 'title', 'content_blocks', 'status',
            'revision_number', 'change_summary', 'created_at',
            'created_by', 'created_by_email', 'content_preview'
        ]
        read_only_fields = ['id', 'created_at', 'revision_number']

    def get_content_preview(self, obj):
        """Get a preview of the content"""
        return obj.get_content_preview()


class DraftRevisionListSerializer(serializers.ModelSerializer):
    """Simplified serializer for listing revisions"""
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)

    class Meta:
        model = DraftRevision
        fields = [
            'id', 'revision_number', 'title', 'status',
            'change_summary', 'created_at', 'created_by_email'
        ]


class MediaFileSerializer(serializers.ModelSerializer):
    """Serializer for MediaFile model"""
    file_url = serializers.SerializerMethodField()
    uploaded_by_email = serializers.CharField(source='uploaded_by.email', read_only=True)
    
    class Meta:
        model = MediaFile
        fields = [
            'id', 'file', 'file_url', 'media_type', 'original_filename',
            'file_size', 'content_type', 'scan_status', 'uploaded_by', 'uploaded_by_email',
            'draft', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'uploaded_by', 'scan_status', 'created_at', 'updated_at']
    
    def get_file_url(self, obj):
        """Get the file URL"""
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class MediaFileUploadSerializer(serializers.ModelSerializer):
    """Serializer for uploading media files"""
    draft = serializers.IntegerField(required=False, allow_null=True)
    
    class Meta:
        model = MediaFile
        fields = ['file', 'media_type', 'draft']
    
    def validate_media_type(self, value):
        """Validate media type"""
        valid_types = ['image', 'video', 'audio', 'file']
        if value not in valid_types:
            raise serializers.ValidationError(f"Media type must be one of: {', '.join(valid_types)}")
        return value
    
    def create(self, validated_data):
        """Create a new media file"""
        file_obj = validated_data.get('file')
        media_type = validated_data.get('media_type')
        draft_id = validated_data.get('draft')
        
        # Get scan_status from validated_data if provided, otherwise default to READY
        scan_status = validated_data.pop('scan_status', MediaFile.READY)
        
        # Get the current user from context
        user = self.context['request'].user
        
        # Get draft object if draft_id is provided
        draft = None
        if draft_id:
            from .models import Draft
            try:
                draft = Draft.objects.get(id=draft_id, user=user, is_deleted=False)
            except Draft.DoesNotExist:
                raise serializers.ValidationError("Draft not found or access denied")
        
        # Determine media type from file if not provided
        if not media_type and file_obj:
            content_type = file_obj.content_type or ''
            if content_type.startswith('image/'):
                media_type = 'image'
            elif content_type.startswith('video/'):
                media_type = 'video'
            elif content_type.startswith('audio/'):
                media_type = 'audio'
            else:
                media_type = 'file'
        
        # Create the media file
        media_file = MediaFile.objects.create(
            file=file_obj,
            media_type=media_type or 'file',
            original_filename=file_obj.name if file_obj else '',
            file_size=file_obj.size if file_obj else 0,
            content_type=file_obj.content_type if file_obj else '',
            scan_status=scan_status,
            uploaded_by=user,
            draft=draft
        )
        
        return media_file


class NotionStatusSerializer(serializers.Serializer):
    connected = serializers.BooleanField()
    workspace_id = serializers.CharField(allow_null=True, required=False)
    workspace_name = serializers.CharField(allow_null=True, required=False)
    workspace_icon = serializers.CharField(allow_null=True, required=False)
    bot_id = serializers.CharField(allow_null=True, required=False)
    bot_name = serializers.CharField(allow_null=True, required=False)
    connected_at = serializers.DateTimeField(allow_null=True, required=False)


class NotionConnectSerializer(serializers.Serializer):
    auth_url = serializers.URLField()
    state = serializers.CharField()


class NotionDisconnectSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class NotionImportSerializer(serializers.Serializer):
    page = serializers.CharField(max_length=500, allow_blank=False)
    draft_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class LinkDraftProjectSerializer(serializers.Serializer):
    project_id = serializers.IntegerField(min_value=1)


class NotionExportSerializer(serializers.Serializer):
    draft_id = serializers.IntegerField(min_value=1)
    parent_page_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=255)

