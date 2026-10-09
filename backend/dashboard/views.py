import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import NotFound, ValidationError
from core.models import Project
from .models import DashboardLayout
from .services import DEFAULT_WIDGETS, LAYOUT_CONFIGURATION, save_layout, widgets_for_response
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta, date
from task.models import Task, ApprovalRecord, TaskComment
from decision.models import Decision
from spreadsheet.models import Spreadsheet
from core.slug_mixins import resolve_project_pk
from .serializers import DashboardSummarySerializer, ProjectWorkspaceDashboardSerializer, DashboardLayoutSerializer


logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.trace import set_span_in_context, INVALID_SPAN
    from opentelemetry.context import attach, detach
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False


def disable_tracing(func):
    """
    SAFETY LOCK 2: Disable OpenTelemetry tracing for this view
    Reason: Dashboard has large response bodies that cause Jaeger UDP packet overflow
    Error prevented: "Data exceeds the max UDP packet size; size 87621, max 65000"
    """
    if not TRACING_AVAILABLE:
        return func

    def wrapper(*args, **kwargs):
        ctx = set_span_in_context(INVALID_SPAN)
        token = attach(ctx)
        try:
            return func(*args, **kwargs)
        finally:
            detach(token)
    return wrapper


class DashboardSummaryView(APIView):
    """
    Dashboard summary endpoint - Returns all dashboard statistics.
    Supports optional project_id filtering.

    Query params:
        project_id (int, optional): Scope all metrics to a specific project.
        days (int, optional): Number of days for daily_task_activity. Allowed: 7 or 30. Default: 7.

    PERFORMANCE NOTES:
    - SAFETY LOCK 2: Tracing disabled for this endpoint (high response body size)
    - SAFETY LOCK 3: Never use async/await or asyncio.gather - sequential queries only
    - Dashboard queries are intentionally synchronous to avoid event loop saturation
    """
    permission_classes = [IsAuthenticated]

    # Allowed values for the `days` query param.
    # 7  → last 7 days  (default)
    # 30 → last 30 days
    ALLOWED_DAYS = {7, 30}

    @disable_tracing
    def get(self, request):
        try:
            # ── Validate project_id ──────────────────────────────────────────
            project_id_param = request.query_params.get('project_id')
            project_id = None
            if project_id_param is not None:
                # Accept slug (current frontend) or numeric pk (legacy); resolve to pk.
                project_id = resolve_project_pk(project_id_param)
                if not project_id:
                    return Response(
                        {"detail": "Invalid project_id. No matching project."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            # ── Validate days ────────────────────────────────────────────────
            days_param = request.query_params.get('days', '7')
            try:
                days = int(days_param)
            except (TypeError, ValueError):
                days = 7
            if days not in self.ALLOWED_DAYS:
                days = 7

            queryset = Task.objects.all()
            if project_id is not None:
                queryset = queryset.filter(project_id=project_id)

            now = timezone.now()
            seven_days_ago = now - timedelta(days=7)
            seven_days_ahead = now + timedelta(days=7)

            # ── Existing time_metrics (always 7-day totals) ──────────────────
            time_metrics = {
                'completed_last_7_days': queryset.filter(
                    status__in=[Task.Status.APPROVED, Task.Status.LOCKED],
                    updated_at__gte=seven_days_ago
                ).count(),
                'updated_last_7_days': queryset.filter(
                    updated_at__gte=seven_days_ago
                ).count(),
                'created_last_7_days': queryset.filter(
                    created_at__gte=seven_days_ago
                ).count(),
                'due_soon': queryset.filter(
                    due_date__gte=now.date(),
                    due_date__lte=seven_days_ahead.date()
                ).exclude(
                    status__in=[Task.Status.APPROVED, Task.Status.LOCKED, Task.Status.CANCELLED]
                ).count()
            }

            # ── daily_task_activity ──────────────────────────────────────────
            # Returns one entry per day for the last `days` days.
            # Each entry: { date: "YYYY-MM-DD", created: int, completed: int }
            #
            # Strategy:
            #   - Build a full date range first so days with zero activity
            #     still appear in the response (the chart needs all N points).
            #   - Query created and completed counts separately, then merge.
            #   - "completed" = tasks whose status changed to APPROVED/LOCKED
            #     within the window.  We use updated_at as the proxy for
            #     completion date because Task has no explicit completed_at field.
            #
            # SAFETY: days is already validated to be in {7, 30}.

            period_start = now - timedelta(days=days)

            # Build zero-filled date index
            daily_map: dict[date, dict] = {}
            for offset in range(days):
                day = (period_start + timedelta(days=offset + 1)).date()
                daily_map[day] = {'date': str(day), 'created': 0, 'completed': 0}

            # Created tasks per day — group by date of created_at
            created_qs = (
                queryset
                .filter(created_at__date__gt=period_start.date(),
                        created_at__date__lte=now.date())
                .values('created_at__date')
                .annotate(count=Count('id'))
            )
            for row in created_qs:
                day = row['created_at__date']
                if day in daily_map:
                    daily_map[day]['created'] = row['count']

            # Completed tasks per day — tasks that moved to a terminal status
            # within the window.  We use updated_at as the completion date proxy.
            completed_qs = (
                queryset
                .filter(
                    status__in=[Task.Status.APPROVED, Task.Status.LOCKED],
                    updated_at__date__gt=period_start.date(),
                    updated_at__date__lte=now.date(),
                )
                .values('updated_at__date')
                .annotate(count=Count('id'))
            )
            for row in completed_qs:
                day = row['updated_at__date']
                if day in daily_map:
                    daily_map[day]['completed'] = row['count']

            # Return sorted chronologically
            daily_task_activity = sorted(daily_map.values(), key=lambda x: x['date'])

            # ── Status breakdown ─────────────────────────────────────────────
            total_work_items = queryset.count()

            status_mapping = {
                Task.Status.DRAFT:        ('TO_DO',      'To Do',       '#94A3B8'),
                Task.Status.SUBMITTED:    ('TO_DO',      'To Do',       '#94A3B8'),
                Task.Status.UNDER_REVIEW: ('IN_PROGRESS','In Progress', '#3B82F6'),
                Task.Status.APPROVED:     ('DONE',       'Done',        '#10B981'),
                Task.Status.LOCKED:       ('DONE',       'Done',        '#10B981'),
                Task.Status.REJECTED:     ('RESEARCH',   'Research',    '#8B5CF6'),
                Task.Status.CANCELLED:    ('CANCELLED',  'Cancelled',   '#6B7280'),
            }

            status_counts: dict = {}
            for task_status in queryset.values('status').annotate(count=Count('status')):
                db_status   = task_status['status']
                count        = task_status['count']
                display_status, display_name, color = status_mapping.get(
                    db_status, (db_status, db_status, '#94A3B8')
                )
                if display_status not in status_counts:
                    status_counts[display_status] = {
                        'status':       display_status,
                        'display_name': display_name,
                        'count':        0,
                        'color':        color,
                    }
                status_counts[display_status]['count'] += count

            status_breakdown = list(status_counts.values())
            status_overview  = {
                'total_work_items': total_work_items,
                'breakdown':        status_breakdown,
            }

            # ── Priority breakdown ───────────────────────────────────────────
            priority_counts = (
                queryset
                .values('priority')
                .annotate(count=Count('priority'))
                .order_by('priority')
            )
            priority_order = [
                Task.Priority.HIGHEST, Task.Priority.HIGH, Task.Priority.MEDIUM,
                Task.Priority.LOW,     Task.Priority.LOWEST,
            ]
            priority_count_dict = {item['priority']: item['count'] for item in priority_counts}
            priority_breakdown = [
                {'priority': p, 'count': priority_count_dict.get(p, 0)}
                for p in priority_order
            ]

            # ── Types of work ────────────────────────────────────────────────
            type_counts = (
                queryset
                .values('type')
                .annotate(count=Count('type'))
                .order_by('-count')
            )
            type_display_names = {
                'budget':        'Budget',
                'asset':         'Asset',
                'retrospective': 'Retrospective',
                'report':        'Report',
                'execution':     'Execution',
            }
            types_of_work = [
                {
                    'type':         item['type'],
                    'display_name': type_display_names.get(item['type'], item['type'].title()),
                    'count':        item['count'],
                    'percentage':   round(item['count'] / total_work_items * 100, 1)
                                    if total_work_items > 0 else 0,
                }
                for item in type_counts
            ]

            # ── Recent activity ──────────────────────────────────────────────
            recent_activity = self._get_recent_activity(project_id, limit=20)

            # ── Assemble response ────────────────────────────────────────────
            dashboard_data = {
                'time_metrics':        time_metrics,
                'status_overview':     status_overview,
                'priority_breakdown':  priority_breakdown,
                'types_of_work':       types_of_work,
                'recent_activity':     recent_activity,
                'daily_task_activity': daily_task_activity,  # NEW
            }

            serializer = DashboardSummarySerializer(dashboard_data)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception:
            logger.exception("Unexpected error in DashboardSummaryView")
            return Response(
                {'detail': 'Internal server error. Please try again later.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_recent_activity(self, project_id, limit=20):
        """
        Gather recent activity from multiple sources and unify them.

        CRITICAL OPTIMIZATION: Direct project_id filtering instead of subqueries.
        - Much faster than task__in=queryset which creates expensive subqueries
        - Uses indexed project_id field for fast lookups
        """
        activities = []
        limit = min(limit, 20)  # SAFETY LOCK 1: hard cap at 20

        task_filter = Q(project_id=project_id) if project_id else Q()

        recent_tasks = (
            Task.objects.filter(task_filter)
            .select_related('owner')
            .order_by('-created_at')[:limit]
        )
        for task in recent_tasks:
            if task.owner:
                activities.append({
                    'id':             f'task_created_{task.id}',
                    'event_type':     'task_created',
                    'user':           task.owner,
                    'task':           task,
                    'timestamp':      task.created_at,
                    'human_readable': self._get_human_readable_time(task.created_at),
                })

        approval_filter = Q(task__project_id=project_id) if project_id else Q()
        approval_records = (
            ApprovalRecord.objects
            .filter(approval_filter)
            .select_related('approved_by', 'task')
            .order_by('-decided_time')[:limit]
        )
        for record in approval_records:
            event_type = 'approved' if record.is_approved else 'rejected'
            activities.append({
                'id':             f'{event_type}_{record.id}',
                'event_type':     event_type,
                'user':           record.approved_by,
                'task':           record.task,
                'timestamp':      record.decided_time,
                'human_readable': self._get_human_readable_time(record.decided_time),
                'is_approved':    record.is_approved,
            })

        comment_filter = Q(task__project_id=project_id) if project_id else Q()
        comments = (
            TaskComment.objects
            .filter(comment_filter)
            .select_related('user', 'task')
            .order_by('-created_at')[:limit]
        )
        for comment in comments:
            activities.append({
                'id':             f'commented_{comment.id}',
                'event_type':     'commented',
                'user':           comment.user,
                'task':           comment.task,
                'timestamp':      comment.created_at,
                'human_readable': self._get_human_readable_time(comment.created_at),
                'comment_body':   comment.body[:100] if len(comment.body) > 100 else comment.body,
            })

        activities.sort(key=lambda x: x['timestamp'], reverse=True)
        return activities[:limit]

    def _get_human_readable_time(self, timestamp):
        """Convert timestamp to human-readable relative time string."""
        now     = timezone.now()
        seconds = (now - timestamp).total_seconds()

        if seconds < 60:
            return 'less than a minute ago'
        elif seconds < 3600:
            m = int(seconds / 60)
            return f'{m} minute{"s" if m > 1 else ""} ago'
        elif seconds < 86400:
            h = int(seconds / 3600)
            return f'{h} hour{"s" if h > 1 else ""} ago'
        elif seconds < 604800:
            d = int(seconds / 86400)
            return f'{d} day{"s" if d > 1 else ""} ago'
        elif seconds < 2592000:
            w = int(seconds / 604800)
            return f'{w} week{"s" if w > 1 else ""} ago'
        else:
            mo = int(seconds / 2592000)
            return f'{mo} month{"s" if mo > 1 else ""} ago'


# ── SMP-472: Project Workspace Dashboard ──────────────────────────────────

class ProjectWorkspaceDashboardView(APIView):
    """
    Project Workspace Dashboard endpoint for SMP-472.

    Returns a lightweight summary of Decisions, Tasks, Spreadsheets, and
    WorkflowPatterns scoped strictly to the requested project.
    This is an orientation surface, not an analytics dashboard —
    no cross-project data is ever returned.

    Query params:
        project_id (int, required): The project to scope the dashboard to.

    Returns:
        200: { decisions: [...], tasks: [...], spreadsheets: [...], patterns: [...] }
        400: If project_id is missing or invalid.
        401: If the user is not authenticated.
    """
    permission_classes = [IsAuthenticated]

    ZONE_LIMIT = 20

    def get(self, request):
        # --- Validate project_id ---
        project_id_param = request.query_params.get('project_id')
        if project_id_param is None:
            return Response(
                {'detail': 'project_id is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Accept slug (current frontend) or numeric pk (legacy); resolve to pk.
        project_id = resolve_project_pk(project_id_param)
        if not project_id:
            return Response(
                {'detail': 'Invalid project_id. No matching project.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.db import models as django_models
        from django.db.models import Exists, OuterRef
        from django.contrib.contenttypes.models import ContentType
        from django.db.models.functions import Cast
        from task.models import TaskRelation
        from spreadsheet.models import PatternJob, WorkflowPattern

        decision_content_type = ContentType.objects.get_for_model(Decision)

        RESOLVED_TASK_STATUSES = [
            Task.Status.APPROVED,
            Task.Status.LOCKED,
            Task.Status.CANCELLED,
        ]

        # --- Decision Zone ---
        unresolved_tasks_subquery = Task.objects.filter(
            project_id=project_id,
            content_type=decision_content_type,
            object_id=Cast(OuterRef('pk'), django_models.CharField()),
        ).exclude(status__in=RESOLVED_TASK_STATUSES)

        decisions = Decision.objects.filter(
            project_id=project_id,
            is_deleted=False,
            status__in=[
                Decision.Status.COMMITTED,
                Decision.Status.AWAITING_APPROVAL,
                Decision.Status.REVIEWED,
            ]
        ).annotate(
            has_unresolved_tasks_flag=Exists(unresolved_tasks_subquery),
            is_awaiting_review_flag=django_models.Case(
                django_models.When(
                    status=Decision.Status.AWAITING_APPROVAL,
                    then=django_models.Value(True),
                ),
                default=django_models.Value(False),
                output_field=django_models.BooleanField(),
            ),
            is_high_risk_flag=django_models.Case(
                django_models.When(risk_level='HIGH', then=django_models.Value(True)),
                default=django_models.Value(False),
                output_field=django_models.BooleanField(),
            ),
        ).order_by(
            '-is_awaiting_review_flag',
            '-has_unresolved_tasks_flag',
            '-is_high_risk_flag',
            '-updated_at',
        )[:self.ZONE_LIMIT]

        # --- Task Zone ---
        blocked_subquery = TaskRelation.objects.filter(
            target_task=OuterRef('pk'),
            relationship_type=TaskRelation.BLOCKS,
        )

        decision_linked_subquery = Task.objects.filter(
            pk=OuterRef('pk'),
            content_type=decision_content_type,
        )

        tasks = Task.objects.filter(
            project_id=project_id,
            status__in=[
                Task.Status.SUBMITTED,
                Task.Status.UNDER_REVIEW,
                Task.Status.REJECTED,
            ]
        ).select_related('owner').annotate(
            is_overdue_flag=django_models.Case(
                django_models.When(
                    due_date__lt=timezone.localdate(),
                    then=django_models.Value(True),
                ),
                default=django_models.Value(False),
                output_field=django_models.BooleanField(),
            ),
            is_blocked_flag=Exists(blocked_subquery),
            is_decision_linked_flag=Exists(decision_linked_subquery),
        ).order_by(
            '-is_overdue_flag',
            '-is_blocked_flag',
            '-is_decision_linked_flag',
            '-updated_at',
        )[:self.ZONE_LIMIT]

        # --- Spreadsheet Zone ---
        running_job_subquery = PatternJob.objects.filter(
            spreadsheet=OuterRef('pk'),
            status__in=['queued', 'running'],
        )

        spreadsheets = Spreadsheet.objects.filter(
            project_id=project_id,
            is_deleted=False,
        ).annotate(
            has_running_job_flag=Exists(running_job_subquery)
        ).order_by('-has_running_job_flag', '-updated_at')[:self.ZONE_LIMIT]

        # --- Pattern Zone ---
        project_spreadsheet_ids = Spreadsheet.objects.filter(
            project_id=project_id,
            is_deleted=False,
        ).values_list('id', flat=True)

        pattern_origin_running_subquery = PatternJob.objects.filter(
            spreadsheet_id=OuterRef('origin_spreadsheet_id'),
            status__in=['queued', 'running'],
        )

        patterns = WorkflowPattern.objects.filter(
            origin_spreadsheet_id__in=project_spreadsheet_ids,
            is_archived=False,
            is_deleted=False,
        ).annotate(
            is_origin_running_flag=Exists(pattern_origin_running_subquery),
        ).order_by('-is_origin_running_flag', '-updated_at')[:self.ZONE_LIMIT]

        data = {
            'decisions':    list(decisions),
            'tasks':        list(tasks),
            'spreadsheets': list(spreadsheets),
            'patterns':     list(patterns),
        }
        serializer = ProjectWorkspaceDashboardSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


class DashboardLayoutView(APIView):
    permission_classes = [IsAuthenticated]

    def project(self, request):
        project_id = resolve_project_pk(request.query_params.get('project_id'))
        if not project_id:
            raise ValidationError({'project_id': 'A valid project is required.'})
        projects = Project.objects.filter(pk=project_id).filter(
            Q(owner=request.user) | Q(members__user=request.user, members__is_active=True)
        ).distinct()
        org_id = getattr(request.user, 'current_organization_id', None) or getattr(request.user, 'organization_id', None)
        if org_id:
            projects = projects.filter(organization_id=org_id)
        project = projects.first()
        if project is None:
            raise NotFound('Project not found.')
        return project

    def get(self, request):
        project = self.project(request)
        layout = DashboardLayout.objects.filter(project=project, user=request.user).first()
        return Response(
            {'project_id': project.pk, 'project_slug': project.slug,
             'widgets': widgets_for_response(layout.widgets) if layout else DEFAULT_WIDGETS,
             'configuration': LAYOUT_CONFIGURATION},
            headers={'Cache-Control': 'private, no-store'},
        )

    def put(self, request):
        project = self.project(request)
        serializer = DashboardLayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        layout = save_layout(project, request.user, serializer.validated_data['widgets'])
        return Response(
            {'project_id': project.pk, 'project_slug': project.slug,
             'widgets': layout.widgets, 'updated_at': layout.updated_at,
             'configuration': LAYOUT_CONFIGURATION},
            headers={'Cache-Control': 'private, no-store'},
        )
