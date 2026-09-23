"""
Campaign Management Module - Serializers
============================================================================
"""

from rest_framework import serializers
from django.contrib.auth import get_user_model
from core.models import Project
from core.utils.project import has_project_access
from .models import (
    Campaign,
    CampaignPlatformIntegration,
    CampaignStatusHistory,
    PerformanceCheckIn,
    PerformanceSnapshot,
    CampaignAttachment,
    CampaignTemplate,
    CampaignTaskLink,
    CampaignDecisionLink,
    CampaignCalendarLink,
)
from task.models import Task

User = get_user_model()


# ============================================================================
# User and Project Summary Serializers
# ============================================================================

class UserSummarySerializer(serializers.ModelSerializer):
    """Serializer for user summary information"""
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']


class ProjectSummarySerializer(serializers.ModelSerializer):
    """Serializer for project summary information"""
    class Meta:
        model = Project
        fields = ['id', 'slug', 'name']


# ============================================================================
# Campaign Serializers
# ============================================================================

class CampaignPlatformIntegrationSerializer(serializers.ModelSerializer):
    platform = serializers.SerializerMethodField()
    account_name = serializers.CharField(source='ad_account.name', read_only=True)
    connector_name = serializers.CharField(source='ad_account.connection.user.username', read_only=True)
    can_reconnect = serializers.SerializerMethodField()

    class Meta:
        model = CampaignPlatformIntegration
        fields = ['id', 'platform', 'account_name', 'connector_name', 'can_reconnect',
                  'last_sync_error', 'last_sync_attempted_at', 'last_synced_at']
        read_only_fields = fields

    def get_platform(self, obj):
        return Campaign.Platform.META

    def get_can_reconnect(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.id == obj.ad_account.connection.user_id)


class CampaignSerializer(serializers.ModelSerializer):
    """Full Campaign serializer with all fields"""
    owner = UserSummarySerializer(read_only=True)
    owner_id = serializers.IntegerField(write_only=True, required=True)
    creator = UserSummarySerializer(read_only=True)
    assignee = UserSummarySerializer(read_only=True)
    assignee_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    project = ProjectSummarySerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True, required=True)
    platform_integrations = serializers.SerializerMethodField()

    def get_platform_integrations(self, obj):
        if Campaign.Platform.META not in (obj.platforms or []):
            return []
        integrations = [integration for integration in obj.platform_integrations.all()
                        if integration.ad_account.project_id == obj.project_id]
        return CampaignPlatformIntegrationSerializer(integrations, many=True, context=self.context).data
    
    class Meta:
        model = Campaign
        fields = ['slug', 
            'id', 'name', 'objective', 'platforms', 'hypothesis', 'tags',
            'start_date', 'end_date', 'actual_completion_date',
            'owner', 'owner_id', 'creator', 'assignee', 'assignee_id',
            'project', 'project_id', 'budget_estimate',
            'status', 'status_note', 'latest_performance_summary',
            'created_at', 'updated_at', 'is_deleted', 'platform_integrations'
        ]
        read_only_fields = ['slug', 
            'id', 'creator', 'status', 'actual_completion_date',
            'latest_performance_summary', 'created_at', 'updated_at', 'is_deleted'
        ]

    def validate_platforms(self, value):
        """Validate platforms list"""
        if not value or not isinstance(value, list):
            raise serializers.ValidationError("Platforms must be a non-empty list")
        
        valid_platforms = [choice[0] for choice in Campaign.Platform.choices]
        invalid_platforms = [p for p in value if p not in valid_platforms]
        if invalid_platforms:
            raise serializers.ValidationError(
                f"Invalid platforms: {invalid_platforms}. Must be one of: {valid_platforms}"
            )
        return value

    def validate(self, data):
        """Cross-field validation"""
        # Validate end_date is after start_date
        start_date = data.get('start_date', self.instance.start_date if self.instance else None)
        end_date = data.get('end_date', self.instance.end_date if self.instance else None)
        
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({
                'end_date': 'End date must be after start date'
            })
        
        return data

    def create(self, validated_data):
        """Create campaign with automatic creator assignment"""
        user = self.context['request'].user
        
        # Set creator
        validated_data['creator'] = user
        
        # Validate project access
        project_id = validated_data.pop('project_id')
        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            raise serializers.ValidationError({'project_id': 'Project not found'})
        
        if not has_project_access(user, project):
            raise serializers.ValidationError({
                'project_id': 'You must be a member of this project'
            })
        validated_data['project'] = project
        
        # Validate owner
        owner_id = validated_data.pop('owner_id')
        try:
            owner = User.objects.get(id=owner_id)
        except User.DoesNotExist:
            raise serializers.ValidationError({'owner_id': 'User not found'})
        
        if not has_project_access(owner, project):
            raise serializers.ValidationError({
                'owner_id': 'Owner must be a member of this project'
            })
        validated_data['owner'] = owner
        
        # Handle assignee
        assignee_id = validated_data.pop('assignee_id', None)
        if assignee_id:
            try:
                assignee = User.objects.get(id=assignee_id)
                if not has_project_access(assignee, project):
                    raise serializers.ValidationError({
                        'assignee_id': 'Assignee must be a member of this project'
                    })
                validated_data['assignee'] = assignee
            except User.DoesNotExist:
                raise serializers.ValidationError({'assignee_id': 'User not found'})
        
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Update campaign"""
        # Handle project_id if provided
        if 'project_id' in validated_data:
            project_id = validated_data.pop('project_id')
            try:
                project = Project.objects.get(id=project_id)
            except Project.DoesNotExist:
                raise serializers.ValidationError({'project_id': 'Project not found'})
            
            user = self.context['request'].user
            if not has_project_access(user, project):
                raise serializers.ValidationError({
                    'project_id': 'You must be a member of this project'
                })
            validated_data['project'] = project
        
        # Handle owner_id if provided
        if 'owner_id' in validated_data:
            owner_id = validated_data.pop('owner_id')
            try:
                owner = User.objects.get(id=owner_id)
                project = validated_data.get('project', instance.project)
                if not has_project_access(owner, project):
                    raise serializers.ValidationError({
                        'owner_id': 'Owner must be a member of this project'
                    })
                validated_data['owner'] = owner
            except User.DoesNotExist:
                raise serializers.ValidationError({'owner_id': 'User not found'})
        
        # Handle assignee_id if provided
        if 'assignee_id' in validated_data:
            assignee_id = validated_data.pop('assignee_id')
            if assignee_id:
                try:
                    assignee = User.objects.get(id=assignee_id)
                    project = validated_data.get('project', instance.project)
                    if not has_project_access(assignee, project):
                        raise serializers.ValidationError({
                            'assignee_id': 'Assignee must be a member of this project'
                        })
                    validated_data['assignee'] = assignee
                except User.DoesNotExist:
                    raise serializers.ValidationError({'assignee_id': 'User not found'})
            else:
                validated_data['assignee'] = None
        
        return super().update(instance, validated_data)


class CampaignCreateSerializer(CampaignSerializer):
    """Serializer for campaign creation with required fields"""
    class Meta(CampaignSerializer.Meta):
        extra_kwargs = {
            'name': {'required': True},
            'objective': {'required': True},
            'platforms': {'required': True},
            'start_date': {'required': True},
        }


class CampaignUpdateSerializer(serializers.ModelSerializer):
    """Serializer for campaign updates (partial fields only)"""
    class Meta:
        model = Campaign
        fields = [
            'name', 'objective', 'platforms', 'hypothesis', 'tags',
            'end_date', 'budget_estimate', 'status_note',
            'assignee_id'
        ]
        extra_kwargs = {
            'name': {'required': False},
            'objective': {'required': False},
            'platforms': {'required': False},
        }

    assignee_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_platforms(self, value):
        """Validate platforms list"""
        if value is not None:
            valid_platforms = [choice[0] for choice in Campaign.Platform.choices]
            invalid_platforms = [p for p in value if p not in valid_platforms]
            if invalid_platforms:
                raise serializers.ValidationError(
                    f"Invalid platforms: {invalid_platforms}. Must be one of: {valid_platforms}"
                )
        return value


class CampaignStatusHistorySerializer(serializers.ModelSerializer):
    """Serializer for campaign status history (read-only)"""
    changed_by = UserSummarySerializer(read_only=True)
    from_status_display = serializers.CharField(source='get_from_status_display', read_only=True)
    to_status_display = serializers.CharField(source='get_to_status_display', read_only=True)
    
    class Meta:
        model = CampaignStatusHistory
        fields = [
            'id', 'campaign', 'from_status', 'from_status_display',
            'to_status', 'to_status_display', 'changed_by', 'note',
            'created_at'
        ]
        read_only_fields = ['id', 'campaign', 'from_status', 'to_status', 'changed_by', 'note', 'created_at']


# ============================================================================
# Performance Tracking Serializers
# ============================================================================

class PerformanceCheckInSerializer(serializers.ModelSerializer):
    """Serializer for performance check-ins"""
    checked_by = UserSummarySerializer(read_only=True)
    sentiment_display = serializers.CharField(source='get_sentiment_display', read_only=True)
    
    class Meta:
        model = PerformanceCheckIn
        fields = [
            'id', 'campaign', 'sentiment', 'sentiment_display',
            'note', 'checked_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'checked_by', 'created_at', 'updated_at']


class PerformanceCheckInCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating performance check-ins"""
    class Meta:
        model = PerformanceCheckIn
        fields = ['sentiment', 'note']  # campaign is set by view's perform_create

    def create(self, validated_data):
        """Create check-in with automatic user assignment"""
        validated_data['checked_by'] = self.context['request'].user
        return super().create(validated_data)


class PerformanceSnapshotSerializer(serializers.ModelSerializer):
    """Serializer for performance snapshots"""
    snapshot_by = UserSummarySerializer(read_only=True)
    metric_type_display = serializers.CharField(source='get_metric_type_display', read_only=True)
    milestone_type_display = serializers.CharField(source='get_milestone_type_display', read_only=True)
    screenshot_url = serializers.SerializerMethodField()
    
    class Meta:
        model = PerformanceSnapshot
        fields = [
            'id', 'campaign', 'milestone_type', 'milestone_type_display',
            'spend', 'metric_type', 'metric_type_display', 'metric_value',
            'percentage_change', 'notes', 'screenshot', 'screenshot_url',
            'additional_metrics', 'snapshot_by',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'snapshot_by', 'screenshot_url', 'created_at', 'updated_at'
        ]

    def get_screenshot_url(self, obj):
        """Generate screenshot URL from file field"""
        if obj.screenshot and hasattr(obj.screenshot, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.screenshot.url)
            return obj.screenshot.url
        return None


class PerformanceSnapshotCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating performance snapshots"""
    class Meta:
        model = PerformanceSnapshot
        fields = [
            'milestone_type', 'spend', 'metric_type',
            'metric_value', 'percentage_change', 'notes', 'screenshot',
            'additional_metrics'
        ]  # campaign is set by view's perform_create

    def create(self, validated_data):
        """Create snapshot with automatic user assignment"""
        validated_data['snapshot_by'] = self.context['request'].user
        return super().create(validated_data)


# ============================================================================
# Campaign Attachment Serializers
# ============================================================================

class CampaignAttachmentSerializer(serializers.ModelSerializer):
    """Serializer for campaign attachments"""
    uploaded_by = UserSummarySerializer(read_only=True)
    asset_type_display = serializers.CharField(source='get_asset_type_display', read_only=True)
    file_url = serializers.SerializerMethodField()
    
    class Meta:
        model = CampaignAttachment
        fields = [
            'id', 'campaign', 'file', 'file_url', 'url', 'asset_type',
            'asset_type_display', 'uploaded_by', 'metadata',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'uploaded_by', 'file_url', 'created_at', 'updated_at'
        ]

    def get_file_url(self, obj):
        """Generate file URL from file field"""
        if obj.file and hasattr(obj.file, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class CampaignAttachmentCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating campaign attachments (supports file or URL)"""
    class Meta:
        model = CampaignAttachment
        fields = ['file', 'url', 'asset_type', 'metadata']  # campaign is set by view's perform_create

    def validate(self, data):
        """Ensure either file or url is provided"""
        file = data.get('file')
        url = data.get('url')
        
        if not file and not url:
            raise serializers.ValidationError({
                'file': 'Either file or URL must be provided',
                'url': 'Either file or URL must be provided'
            })
        
        return data

    def create(self, validated_data):
        """Create attachment with automatic user assignment"""
        validated_data['uploaded_by'] = self.context['request'].user
        return super().create(validated_data)


# ============================================================================
# Campaign Template Serializers
# ============================================================================

class CampaignTemplateSerializer(serializers.ModelSerializer):
    """Serializer for campaign templates"""
    creator = UserSummarySerializer(read_only=True)
    project = ProjectSummarySerializer(read_only=True)
    sharing_scope_display = serializers.CharField(source='get_sharing_scope_display', read_only=True)
    
    class Meta:
        model = CampaignTemplate
        fields = [
            'id', 'slug', 'name', 'description', 'creator', 'version_number',
            'sharing_scope', 'sharing_scope_display', 'project', 'project_id',
            'objective', 'platforms', 'hypothesis_framework', 'tag_suggestions',
            'task_checklist', 'review_schedule_pattern', 'decision_point_triggers',
            'recommended_variation_count', 'variation_templates',
            'usage_count', 'is_archived',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'slug', 'creator', 'version_number', 'usage_count', 'created_at', 'updated_at'
        ]

    project_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    def create(self, validated_data):
        """Create template with automatic creator assignment and auto-versioning.
        
        Note: version_number is always auto-generated based on existing templates
        and cannot be manually set. Any user-provided version_number will be ignored.
        """
        from django.db import transaction
        
        user = self.context['request'].user
        validated_data['creator'] = user
        
        # Handle project_id if provided
        project_id = validated_data.pop('project_id', None)
        project = None
        if project_id:
            try:
                project = Project.objects.get(id=project_id)
                validated_data['project'] = project
            except Project.DoesNotExist:
                raise serializers.ValidationError({'project_id': 'Project not found'})
        
        name = validated_data.get('name')
        sharing_scope = validated_data.get('sharing_scope', CampaignTemplate.SharingScope.PERSONAL)
        
        # Auto-versioning: archive existing template with same name
        with transaction.atomic():
            existing_qs = CampaignTemplate.objects.filter(
                name=name,
                sharing_scope=sharing_scope,
                is_archived=False,
            )
            if sharing_scope == CampaignTemplate.SharingScope.PERSONAL:
                existing_qs = existing_qs.filter(creator=user)
            else:
                if not project:
                    raise serializers.ValidationError({
                        'project_id': 'project_id is required for TEAM/ORGANIZATION templates'
                    })
                existing_qs = existing_qs.filter(project=project)
            
            existing = existing_qs.order_by('-version_number', '-created_at').first()
            next_version = (existing.version_number + 1) if existing else 1
            
            if existing:
                existing.is_archived = True
                existing.save(update_fields=['is_archived', 'updated_at'])
            
            validated_data['version_number'] = next_version
            return super().create(validated_data)


# ============================================================================
# Integration Serializers (Campaign ↔ Task Links)
# ============================================================================

class CampaignTaskLinkSerializer(serializers.ModelSerializer):
    """Serializer for Campaign-Task link"""
    task_slug = serializers.SlugField(source='task.slug', read_only=True)

    class Meta:
        model = CampaignTaskLink
        fields = ['id', 'campaign', 'task', 'task_slug', 'link_type', 'created_at', 'updated_at']
        read_only_fields = ['id', 'task_slug', 'created_at', 'updated_at']


class CampaignTaskLinkCreateSerializer(serializers.Serializer):
    """
    Create serializer for linking an existing Task to a Campaign.
    """
    campaign = serializers.UUIDField()
    task = serializers.IntegerField()
    link_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate(self, data):
        request = self.context['request']
        user = request.user

        try:
            campaign = Campaign.objects.select_related('project').get(id=data['campaign'], is_deleted=False)
        except Campaign.DoesNotExist:
            raise serializers.ValidationError({'campaign': 'Campaign not found'})

        if not has_project_access(user, campaign.project):
            raise serializers.ValidationError({'campaign': 'You do not have access to this campaign'})

        try:
            task = Task.objects.select_related('project').get(id=data['task'])
        except Task.DoesNotExist:
            raise serializers.ValidationError({'task': 'Task not found'})

        if task.project_id != campaign.project_id:
            raise serializers.ValidationError({'task': 'Task must belong to the same project as the campaign'})

        data['campaign_obj'] = campaign
        data['task_obj'] = task
        return data

    def create(self, validated_data):
        campaign = validated_data['campaign_obj']
        task = validated_data['task_obj']
        link_type = validated_data.get('link_type')

        link, created = CampaignTaskLink.objects.get_or_create(
            campaign=campaign,
            task=task,
            defaults={'link_type': link_type},
        )
        # If link already exists, update link_type if provided
        if link_type is not None and link.link_type != link_type:
            link.link_type = link_type
            link.save(update_fields=['link_type', 'updated_at'])
            created = False  # Mark as not newly created if we updated it
        return (link, created)


# ============================================================================
# Campaign Decision Link Serializers
# ============================================================================

class CampaignDecisionLinkSerializer(serializers.ModelSerializer):
    """Serializer for campaign-decision links"""
    campaign = serializers.UUIDField(source='campaign.id', read_only=True)
    decision = serializers.IntegerField(source='decision.id', read_only=True)
    
    class Meta:
        model = CampaignDecisionLink
        fields = ['id', 'campaign', 'decision', 'trigger_type', 'created_at']
        read_only_fields = ['id', 'created_at']


class CampaignDecisionLinkCreateSerializer(serializers.Serializer):
    """Serializer for creating campaign-decision links"""
    campaign = serializers.UUIDField()
    decision = serializers.IntegerField()
    trigger_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
    def validate(self, data):
        user = self.context['request'].user
        from decision.models import Decision
        
        try:
            campaign = Campaign.objects.select_related('project').get(id=data['campaign'], is_deleted=False)
        except Campaign.DoesNotExist:
            raise serializers.ValidationError({'campaign': 'Campaign not found'})
        
        if not has_project_access(user, campaign.project):
            raise serializers.ValidationError({'campaign': 'You do not have access to this campaign'})
        
        try:
            decision = Decision.objects.select_related('author').get(id=data['decision'])
        except Decision.DoesNotExist:
            raise serializers.ValidationError({'decision': 'Decision not found'})
        
        # Ensure decision author is a member of campaign's project
        if decision.author and not has_project_access(decision.author, campaign.project):
            raise serializers.ValidationError({
                'decision': 'Decision author must be a member of the campaign\'s project'
            })
        
        data['campaign_obj'] = campaign
        data['decision_obj'] = decision
        return data
    
    def create(self, validated_data):
        campaign = validated_data['campaign_obj']
        decision = validated_data['decision_obj']
        trigger_type = validated_data.get('trigger_type')
        
        link, _ = CampaignDecisionLink.objects.get_or_create(
            campaign=campaign,
            decision=decision,
            defaults={'trigger_type': trigger_type},
        )
        if trigger_type is not None and link.trigger_type != trigger_type:
            link.trigger_type = trigger_type
            link.save(update_fields=['trigger_type', 'updated_at'])
        return link


# ============================================================================
# Campaign Calendar Link Serializers
# ============================================================================

class CampaignCalendarLinkSerializer(serializers.ModelSerializer):
    """Serializer for campaign-calendar links"""
    campaign = serializers.UUIDField(source='campaign.id', read_only=True)
    event = serializers.UUIDField(source='event.id', read_only=True)
    
    class Meta:
        model = CampaignCalendarLink
        fields = ['id', 'campaign', 'event', 'event_type', 'created_at']
        read_only_fields = ['id', 'created_at']


class CampaignCalendarLinkCreateSerializer(serializers.Serializer):
    """Serializer for creating campaign-calendar links"""
    campaign = serializers.UUIDField()
    event = serializers.UUIDField()
    event_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
    def validate(self, data):
        user = self.context['request'].user
        from calendars.models import Event
        
        try:
            campaign = Campaign.objects.select_related('project', 'project__organization').get(
                id=data['campaign'], is_deleted=False
            )
        except Campaign.DoesNotExist:
            raise serializers.ValidationError({'campaign': 'Campaign not found'})
        
        if not has_project_access(user, campaign.project):
            raise serializers.ValidationError({'campaign': 'You do not have access to this campaign'})
        
        try:
            event = Event.objects.select_related('organization').get(id=data['event'], is_deleted=False)
        except Event.DoesNotExist:
            raise serializers.ValidationError({'event': 'Event not found'})
        
        # Ensure event organization matches campaign's project organization
        if event.organization_id != campaign.project.organization_id:
            raise serializers.ValidationError({
                'event': 'Event must belong to the same organization as the campaign\'s project'
            })
        
        data['campaign_obj'] = campaign
        data['event_obj'] = event
        return data
    
    def create(self, validated_data):
        campaign = validated_data['campaign_obj']
        event = validated_data['event_obj']
        event_type = validated_data.get('event_type')
        
        link, _ = CampaignCalendarLink.objects.get_or_create(
            campaign=campaign,
            event=event,
            defaults={'event_type': event_type},
        )
        if event_type is not None and link.event_type != event_type:
            link.event_type = event_type
            link.save(update_fields=['event_type', 'updated_at'])
        return link

