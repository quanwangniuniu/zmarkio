from rest_framework import serializers
from django.utils import timezone
from task.models import Task
from core.models import CustomUser
from .widget_catalog import GRID_COLUMNS, MAX_ROWS, MAX_WIDGETS, WIDGET_BY_ID


class DashboardWidgetPositionSerializer(serializers.Serializer):
    id = serializers.CharField()
    x = serializers.IntegerField(min_value=0)
    y = serializers.IntegerField(min_value=0)
    w = serializers.IntegerField(min_value=1)
    h = serializers.IntegerField(min_value=1)


class DashboardLayoutSerializer(serializers.Serializer):
    widgets = DashboardWidgetPositionSerializer(many=True)

    def validate_widgets(self, widgets):
        if not widgets:
            raise serializers.ValidationError('Keep at least one dashboard widget.')
        if len(widgets) > MAX_WIDGETS:
            raise serializers.ValidationError('Too many dashboard widgets.')

        seen = set()
        for widget in widgets:
            widget_id = widget['id']
            definition = WIDGET_BY_ID.get(widget_id)
            if definition is None:
                raise serializers.ValidationError(f'Unknown widget: {widget_id}.')
            if widget_id in seen:
                raise serializers.ValidationError(f'Duplicate widget: {widget_id}.')
            seen.add(widget_id)
            if widget['x'] + widget['w'] > GRID_COLUMNS or widget['y'] + widget['h'] > MAX_ROWS:
                raise serializers.ValidationError('A widget is outside the dashboard grid.')
            if widget['h'] < definition['min_h']:
                raise serializers.ValidationError(f'{widget_id} is below its minimum height.')

        for index, left in enumerate(widgets):
            for right in widgets[index + 1:]:
                if (left['x'] < right['x'] + right['w'] and right['x'] < left['x'] + left['w']
                        and left['y'] < right['y'] + right['h'] and right['y'] < left['y'] + left['h']):
                    raise serializers.ValidationError('Dashboard widgets cannot overlap.')
        return widgets


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
