from django.utils import timezone
from rest_framework import serializers
from .models import (
    AgentSession, AgentMessage, AgentWorkflowRun, ImportedCSVFile,
    AgentWorkflowDefinition, AgentWorkflowStep, AgentStepExecution,
    AgentPendingExternalApproval, AgentWorkflowTemplate,
    WorkflowTriggerLog, WorkflowTriggerState,
)


class AgentMessageSerializer(serializers.ModelSerializer):
    data = serializers.JSONField(source='metadata', read_only=True)

    class Meta:
        model = AgentMessage
        fields = ['id', 'role', 'content', 'message_type', 'data', 'created_at']
        read_only_fields = ['id', 'created_at']


class AgentSessionListSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    has_unread = serializers.SerializerMethodField()

    class Meta:
        model = AgentSession
        fields = [
            'id', 'title', 'status', 'approval_required', 'is_pinned', 'last_read_at',
            'created_at', 'message_count', 'has_unread',
        ]
        read_only_fields = ['id', 'created_at', 'has_unread', 'is_pinned', 'last_read_at']

    def get_message_count(self, obj):
        return obj.messages.filter(is_deleted=False).count()

    def get_has_unread(self, obj):
        """True when an assistant message exists after last_read_at (or last_read_at unset)."""
        last_assistant = getattr(obj, '_last_assistant_at', None)
        if last_assistant is None:
            return False
        lr = obj.last_read_at
        if lr is None:
            return True
        return last_assistant > lr


class AgentSessionDetailSerializer(serializers.ModelSerializer):
    messages = AgentMessageSerializer(many=True, read_only=True)
    follow_up_available = serializers.SerializerMethodField()
    follow_up_started = serializers.SerializerMethodField()

    class Meta:
        model = AgentSession
        fields = [
            'id', 'title', 'status', 'approval_required', 'is_pinned', 'last_read_at',
            'created_at', 'updated_at', 'messages', 'follow_up_available', 'follow_up_started',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_status(self, value):
        allowed = {c[0] for c in AgentSession.STATUS_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError('Invalid status.')
        return value

    def validate_last_read_at(self, value):
        if value is not None and value > timezone.now():
            raise serializers.ValidationError('last_read_at cannot be in the future.')
        return value

    def get_follow_up_available(self, obj):
        return obj.workflow_runs.filter(
            status='awaiting_confirmation',
            chat_follow_up_started=False,
            chat_followed_up=False,
            is_deleted=False,
        ).exists()

    def get_follow_up_started(self, obj):
        return obj.workflow_runs.filter(
            status='awaiting_confirmation',
            chat_follow_up_started=True,
            chat_followed_up=False,
            is_deleted=False,
        ).exists()


class AgentPendingExternalApprovalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentPendingExternalApproval
        fields = [
            'id', 'kind', 'status', 'draft', 'destination_options',
            'default_destination', 'created_at',
        ]
        read_only_fields = fields


class ResolveExternalApprovalSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['approve', 'reject'])
    draft = serializers.JSONField(required=False, default=dict)
    destination = serializers.JSONField(required=False, allow_null=True)


class AgentWorkflowRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentWorkflowRun
        fields = [
            'id', 'session', 'spreadsheet', 'decision',
            'workflow_definition', 'current_step_order', 'status',
            'analysis_result', 'created_tasks', 'error_message',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ImportedCSVFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImportedCSVFile
        fields = [
            'id', 'filename', 'original_filename',
            'row_count', 'column_count', 'file_size',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ChatInputSerializer(serializers.Serializer):
    message = serializers.CharField(required=True)
    spreadsheet_id = serializers.IntegerField(required=False, allow_null=True)
    sheet_id = serializers.IntegerField(required=False, allow_null=True)
    csv_filename = serializers.RegexField(
        r'^[\w\-. ()]+\.(csv|xlsx|xls)$', required=False, allow_null=True,
        error_messages={'invalid': 'Invalid filename format.'}
    )
    file_id = serializers.UUIDField(required=False, allow_null=True)
    action = serializers.ChoiceField(
        choices=[
            'analyze', 'analyze_spreadsheet_insights', 'create_decisions', 'create_tasks',
            'generate_miro', 'distribute_message', 'start_follow_up', 'cancel_follow_up',
            'confirm_columns', 'confirm_anomalies', 'resume_workflow',
            'resolve_external_approval',
        ],
        required=False,
        allow_null=True,
    )
    approval_id = serializers.UUIDField(required=False, allow_null=True)
    approval_decision = serializers.ChoiceField(
        choices=['approve', 'reject'],
        required=False,
        allow_null=True,
    )
    approval_draft = serializers.JSONField(required=False, allow_null=True)
    calendar_context = serializers.JSONField(required=False, allow_null=True)
    # Draft → Agent context (read-only). Format: {"draftId": <pk|slug>, ...}.
    # The Agent reads the draft via notion_editor's existing API/permissions.
    draft_context = serializers.JSONField(required=False, allow_null=True)
    workflow_id = serializers.UUIDField(required=False, allow_null=True)
    # User-approved column mapping for confirm_columns action.
    # Format: {original_header: canonical_name or "unknown"}
    column_mapping = serializers.JSONField(required=False, allow_null=True)
    # Reviewed anomaly list for confirm_anomalies action.
    # Format: [{id, included, severity, description}, ...] covering every anomaly.
    reviewed_anomalies = serializers.JSONField(required=False, allow_null=True)
    user_context = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=500,
    )


class UploadAnalyzeInputSerializer(serializers.Serializer):
    """Multipart fields for POST /api/agent/upload-analyze/."""
    session_id = serializers.UUIDField(required=False, allow_null=True)
    generation_outputs = serializers.JSONField(required=False, allow_null=True)


class AgentWorkflowStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentWorkflowStep
        fields = ['id', 'name', 'step_type', 'order', 'config', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            'order': {'required': False},
            'config': {'required': False},
            'description': {'required': False},
        }


class AgentWorkflowDefinitionListSerializer(serializers.ModelSerializer):
    step_count = serializers.SerializerMethodField()
    step_types = serializers.SerializerMethodField()
    trigger_state = serializers.SerializerMethodField()

    class Meta:
        model = AgentWorkflowDefinition
        fields = [
            'id', 'slug', 'name', 'description', 'is_default', 'is_system',
            'status', 'step_count', 'step_types', 'created_at',
            'trigger_config', 'trigger_state',
        ]
        read_only_fields = ['id', 'slug', 'is_system', 'created_at']

    def get_step_count(self, obj):
        return obj.steps.filter(is_deleted=False).count()

    def get_step_types(self, obj):
        return list(
            obj.steps.filter(is_deleted=False)
            .order_by('order')
            .values_list('step_type', flat=True)
        )

    def get_trigger_state(self, obj):
        """Return trigger state if exists."""
        try:
            state = obj.trigger_state
            if state:
                return WorkflowTriggerStateSerializer(state).data
        except WorkflowTriggerState.DoesNotExist:
            pass
        return None


class AgentWorkflowDefinitionDetailSerializer(serializers.ModelSerializer):
    steps = serializers.SerializerMethodField()

    class Meta:
        model = AgentWorkflowDefinition
        fields = [
            'id', 'slug', 'name', 'description', 'is_default', 'is_system',
            'status', 'steps', 'trigger_config',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'is_system', 'created_at', 'updated_at']
        extra_kwargs = {
            'trigger_config': {'required': False},
        }

    def get_steps(self, obj):
        active_steps = obj.steps.filter(is_deleted=False).order_by('order')
        return AgentWorkflowStepSerializer(active_steps, many=True).data


class AgentStepExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentStepExecution
        fields = [
            'id', 'step_order', 'step_name', 'status',
            'input_data', 'output_data', 'error_message',
            'started_at', 'completed_at',
        ]
        read_only_fields = ['id', 'started_at', 'completed_at']


class StepReorderSerializer(serializers.Serializer):
    step_ids = serializers.ListField(child=serializers.UUIDField())


class AgentWorkflowTemplateSerializer(serializers.ModelSerializer):
    """
    Serializer for AgentWorkflowTemplate.

    Templates store their own steps configuration and are fully independent.

    Visibility:
      - organization: visible to all members of that org
      - projects (M2M): visible to members of any listed project
      - both empty: private (creator only)
    """
    workflow_step_count = serializers.SerializerMethodField()
    workflow_step_types = serializers.SerializerMethodField()
    applied_project_count = serializers.SerializerMethodField()
    organization_name = serializers.CharField(source='organization.name', read_only=True)
    # Read-only project info (list of {id, name})
    project_list = serializers.SerializerMethodField()
    # Annotated field: whether template is shared to current active project
    is_shared_to_current_project = serializers.BooleanField(read_only=True, required=False)
    source_workflow_id = serializers.UUIDField(write_only=True, required=False)
    organization_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    # Write: list of project IDs (integers or UUIDs); null/[] clears all
    project_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = AgentWorkflowTemplate
        fields = [
            'id', 'slug', 'name', 'description', 'category',
            'steps_config', 'workflow_step_count',
            'workflow_step_types', 'created_by',
            'organization', 'organization_name',
            'project_list',
            'applied_project_count',
            'is_shared_to_current_project',
            'use_cases',
            'source_workflow_id', 'organization_id', 'project_ids',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'slug', 'created_by',
            'organization',
            'is_shared_to_current_project',
            'created_at', 'updated_at',
        ]

    def get_workflow_step_count(self, obj):
        return len(obj.steps_config or [])

    def get_workflow_step_types(self, obj):
        if not obj.steps_config:
            return []
        return [step.get('step_type') for step in obj.steps_config]

    def get_applied_project_count(self, obj):
        return obj.projects.count()

    def get_project_list(self, obj):
        """Return [{id, name}] for all shared projects."""
        return list(obj.projects.values('id', 'name'))

    def validate(self, attrs):
        """Validate organization_id and project_ids, resolve instances."""
        from core.models import Organization, Project
        from core.models import ProjectMember

        request = self.context.get('request')
        user = request.user if request else None

        # --- organization_id ---
        org_id = attrs.pop('organization_id', ...)
        if org_id is not ...:
            if org_id is None:
                attrs['organization'] = None
            else:
                if not user:
                    raise serializers.ValidationError({'organization_id': 'User context required.'})
                try:
                    org = Organization.objects.get(id=org_id)
                except Organization.DoesNotExist:
                    raise serializers.ValidationError({'organization_id': 'Organization not found.'})
                if not hasattr(user, 'organization') or user.organization_id != org.id:
                    raise serializers.ValidationError({
                        'organization_id': 'You can only share templates within your own organization.'
                    })
                attrs['organization'] = org

        # --- project_ids (M2M list) ---
        raw_ids = attrs.pop('project_ids', ...)
        if raw_ids is not ...:
            if not raw_ids:
                attrs['_project_objs'] = []
            else:
                if not user:
                    raise serializers.ValidationError({'project_ids': 'User context required.'})
                projects = list(Project.objects.filter(id__in=raw_ids, is_deleted=False))
                found_ids = {p.id for p in projects}
                missing = [str(i) for i in raw_ids if i not in found_ids]
                if missing:
                    raise serializers.ValidationError({
                        'project_ids': f'Projects not found: {", ".join(missing)}'
                    })
                # Verify membership for each project
                member_project_ids = set(
                    ProjectMember.objects.filter(
                        user=user, project__in=projects, is_active=True
                    ).values_list('project_id', flat=True)
                )
                forbidden = [p.name for p in projects if p.id not in member_project_ids]
                if forbidden:
                    raise serializers.ValidationError({
                        'project_ids': f'Not a member of: {", ".join(forbidden)}'
                    })
                attrs['_project_objs'] = projects

        return attrs

    def validate_source_workflow_id(self, value):
        from .models import AgentWorkflowDefinition
        if not AgentWorkflowDefinition.objects.filter(id=value, is_deleted=False).exists():
            raise serializers.ValidationError('Source workflow not found.')
        return value

    def create(self, validated_data):
        project_objs = validated_data.pop('_project_objs', None)
        instance = super().create(validated_data)
        if project_objs is not None:
            instance.projects.set(project_objs)
        return instance

    def update(self, instance, validated_data):
        project_objs = validated_data.pop('_project_objs', None)
        instance = super().update(instance, validated_data)
        if project_objs is not None:
            instance.projects.set(project_objs)
        return instance


class WorkflowTriggerStateSerializer(serializers.ModelSerializer):
    """Serializer for workflow trigger state."""

    class Meta:
        model = WorkflowTriggerState
        fields = [
            'workflow', 'last_successful_trigger', 'last_polling_check',
            'next_scheduled_run', 'last_trigger_type', 'trigger_count_last_hour',
            'trigger_count_reset_at', 'last_checked_data_hash', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class WorkflowTriggerLogSerializer(serializers.ModelSerializer):
    """Serializer for workflow trigger logs."""

    class Meta:
        model = WorkflowTriggerLog
        fields = [
            'id', 'workflow', 'trigger_type', 'status',
            'trigger_context', 'workflow_run', 'error_message',
            'execution_time_ms', 'created_at',
        ]
        read_only_fields = fields


class TriggerConfigUpdateSerializer(serializers.Serializer):
    """Serializer for updating trigger configuration."""
    trigger_config = serializers.JSONField(required=False)
