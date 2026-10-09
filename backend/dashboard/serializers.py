import math

from rest_framework import serializers
from django.utils import timezone
from task.models import Task
from core.models import CustomUser
from .services import (
    LAYOUT_CONFIGURATION, POSITION_EPSILON, WIDGET_IDS, positions_overlap,
)


class DashboardUserSerializer(serializers.ModelSerializer):
    """Lightweight user serializer for dashboard activity feed"""
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email']


class DashboardTaskSerializer(serializers.ModelSerializer):
    """Lightweight task serializer for dashboard activity feed"""
    key = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ['id', 'key', 'summary', 'status', 'type', 'priority']

    def get_key(self, obj):
        """Generate a task key using the project's ID as prefix."""
        project = getattr(obj, "project", None)
        if project and getattr(project, "id", None):
            prefix = project.id
        else:
            prefix = "TASK"
        return f"{prefix}-{obj.id}"


class ActivityEventSerializer(serializers.Serializer):
    """Serializer for unified activity feed events"""
    id = serializers.CharField()
    event_type = serializers.CharField()
    user = DashboardUserSerializer()
    task = DashboardTaskSerializer()
    timestamp = serializers.DateTimeField()
    human_readable = serializers.CharField()
    field_changed = serializers.CharField(required=False, allow_null=True)
    old_value = serializers.CharField(required=False, allow_null=True)
    new_value = serializers.CharField(required=False, allow_null=True)
    is_approved = serializers.BooleanField(required=False, allow_null=True)
    comment_body = serializers.CharField(required=False, allow_null=True)


class StatusBreakdownSerializer(serializers.Serializer):
    """Serializer for status breakdown data"""
    status = serializers.CharField()
    display_name = serializers.CharField()
    count = serializers.IntegerField()
    color = serializers.CharField(required=False)


class PriorityBreakdownSerializer(serializers.Serializer):
    """Serializer for priority breakdown data"""
    priority = serializers.CharField()
    count = serializers.IntegerField()


class TypeBreakdownSerializer(serializers.Serializer):
    """Serializer for work type breakdown data"""
    type = serializers.CharField()
    display_name = serializers.CharField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class TimeMetricsSerializer(serializers.Serializer):
    """Serializer for time-based metrics"""
    completed_last_7_days = serializers.IntegerField()
    updated_last_7_days = serializers.IntegerField()
    created_last_7_days = serializers.IntegerField()
    due_soon = serializers.IntegerField()


class StatusOverviewSerializer(serializers.Serializer):
    """Serializer for status overview section"""
    total_work_items = serializers.IntegerField()
    breakdown = StatusBreakdownSerializer(many=True)


class DailyActivitySerializer(serializers.Serializer):
    """
    Serializer for a single day's task activity.

    One entry per calendar day within the requested window.
    Days with zero activity are included so the frontend
    always receives a complete, gap-free date range.

    Fields:
        date      — ISO date string "YYYY-MM-DD"
        created   — number of tasks created on this day
        completed — number of tasks moved to APPROVED/LOCKED on this day
    """
    date      = serializers.CharField()   # "YYYY-MM-DD"
    created   = serializers.IntegerField()
    completed = serializers.IntegerField()


class DashboardSummarySerializer(serializers.Serializer):
    """Main serializer for dashboard summary endpoint"""
    time_metrics        = TimeMetricsSerializer()
    status_overview     = StatusOverviewSerializer()
    priority_breakdown  = PriorityBreakdownSerializer(many=True)
    types_of_work       = TypeBreakdownSerializer(many=True)
    recent_activity     = ActivityEventSerializer(many=True)
    # SMP-472: per-day created/completed counts for the trend chart
    # Length = `days` query param (7 or 30), sorted chronologically.
    daily_task_activity = DailyActivitySerializer(many=True)


# ── SMP-472: Project Workspace Dashboard serializers ──────────────────────

from decision.models import Decision
from spreadsheet.models import Spreadsheet, WorkflowPattern


class WorkspaceDecisionSerializer(serializers.ModelSerializer):
    """Decision summary for Project Workspace Dashboard."""
    has_unresolved_tasks = serializers.SerializerMethodField()

    class Meta:
        model = Decision
        fields = ['id', 'title', 'status', 'risk_level', 'updated_at', 'has_unresolved_tasks']

    def get_has_unresolved_tasks(self, obj):
        return obj.has_unresolved_tasks_flag


class WorkspaceTaskSerializer(serializers.ModelSerializer):
    """Task summary for Project Workspace Dashboard."""
    is_blocked         = serializers.SerializerMethodField()
    is_decision_linked = serializers.SerializerMethodField()
    is_overdue         = serializers.SerializerMethodField()
    owner_initials     = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'summary', 'status', 'priority', 'type',
            'due_date', 'updated_at', 'is_overdue', 'is_blocked',
            'is_decision_linked', 'owner_initials',
        ]

    def get_is_blocked(self, obj):
        return obj.is_blocked_flag

    def get_is_decision_linked(self, obj):
        return obj.is_decision_linked_flag

    def get_is_overdue(self, obj):
        if hasattr(obj, 'is_overdue_flag'):
            return bool(obj.is_overdue_flag)
        if not obj.due_date:
            return False
        terminal = {Task.Status.APPROVED, Task.Status.LOCKED, Task.Status.CANCELLED}
        return obj.status not in terminal and obj.due_date < timezone.localdate()

    def get_owner_initials(self, obj):
        owner = getattr(obj, 'owner', None)
        if not owner:
            return None
        first = (getattr(owner, 'first_name', '') or '').strip()
        last  = (getattr(owner, 'last_name',  '') or '').strip()
        if first or last:
            letters = []
            if first: letters.append(first[0])
            if last:  letters.append(last[0])
            return ''.join(letters).upper()[:2] or None
        username = (getattr(owner, 'username', '') or '').strip()
        if username:
            parts = [p for p in username.replace('_', ' ').replace('.', ' ').split(' ') if p]
            letters = [p[0] for p in parts[:2] if p]
            return ''.join(letters).upper()[:2] or username[:2].upper()
        email = (getattr(owner, 'email', '') or '').strip()
        if email:
            local = email.split('@')[0]
            parts = [p for p in local.replace('_', ' ').replace('.', ' ').split(' ') if p]
            letters = [p[0] for p in parts[:2] if p]
            return ''.join(letters).upper()[:2] or local[:2].upper()
        return None


class WorkspaceSpreadsheetSerializer(serializers.ModelSerializer):
    """Spreadsheet summary for Project Workspace Dashboard."""
    has_running_job = serializers.SerializerMethodField()

    class Meta:
        model = Spreadsheet
        fields = ['id', 'name', 'updated_at', 'has_running_job']

    def get_has_running_job(self, obj):
        return obj.has_running_job_flag


class WorkspacePatternSerializer(serializers.ModelSerializer):
    """WorkflowPattern summary for Project Workspace Dashboard."""

    class Meta:
        model = WorkflowPattern
        fields = [
            'id', 'name', 'description', 'version', 'updated_at',
            'origin_spreadsheet_id',
        ]


class ProjectWorkspaceDashboardSerializer(serializers.Serializer):
    """Main serializer for SMP-472 Project Workspace Dashboard endpoint."""
    decisions    = WorkspaceDecisionSerializer(many=True)
    tasks        = WorkspaceTaskSerializer(many=True)
    spreadsheets = WorkspaceSpreadsheetSerializer(many=True)
    patterns     = WorkspacePatternSerializer(many=True)


class WidgetPositionSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=64)
    x = serializers.FloatField(
        min_value=0,
        max_value=LAYOUT_CONFIGURATION['columns'] - LAYOUT_CONFIGURATION['min_width'],
    )
    y = serializers.FloatField(min_value=0, max_value=LAYOUT_CONFIGURATION['max_y'])
    w = serializers.FloatField(
        min_value=LAYOUT_CONFIGURATION['min_width'],
        max_value=LAYOUT_CONFIGURATION['columns'],
    )
    h = serializers.FloatField(min_value=LAYOUT_CONFIGURATION['min_height'], max_value=LAYOUT_CONFIGURATION['max_height'])

    def to_internal_value(self, data):
        if isinstance(data, dict) and 'title' in data:
            raise serializers.ValidationError({'title': 'Widget titles cannot be customized.'})
        return super().to_internal_value(data)

    def validate_id(self, value):
        if value not in WIDGET_IDS:
            raise serializers.ValidationError('Unknown widget type.')
        return value

    def validate(self, attrs):
        if not all(math.isfinite(attrs[key]) for key in ('x', 'y', 'w', 'h')):
            raise serializers.ValidationError('Widget dimensions must be finite.')
        if attrs['x'] + attrs['w'] > LAYOUT_CONFIGURATION['columns'] + POSITION_EPSILON:
            raise serializers.ValidationError('Widget extends beyond the grid.')
        return attrs


class DashboardLayoutSerializer(serializers.Serializer):
    widgets = WidgetPositionSerializer(many=True, allow_empty=True)

    def validate_widgets(self, widgets):
        if len(widgets) > LAYOUT_CONFIGURATION['max_widgets']:
            raise serializers.ValidationError('Too many widgets.')
        seen = set()
        for widget in widgets:
            if widget['id'] in seen:
                raise serializers.ValidationError('Widget IDs must be unique.')
            seen.add(widget['id'])
        for index, a in enumerate(widgets):
            for b in widgets[index + 1:]:
                if positions_overlap(a, b):
                    raise serializers.ValidationError('Widgets may not overlap.')
        return widgets
