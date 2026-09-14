import logging
from rest_framework import viewsets, status, generics, permissions
from core.slug_mixins import SlugLookupViewSetMixin, resolve_project_pk
from task.lookups import resolve_task_lookup_kwargs

logger = logging.getLogger(__name__)
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from django.core.cache import cache
from datetime import datetime
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, Value, When
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from django.shortcuts import get_object_or_404
from django.utils import timezone
from task.models import Task, ApprovalRecord, TaskComment, TaskAttachment, TaskFieldHistory, TaskHierarchy, TaskRelation, ApprovalChain, TaskPin
from task.serializers import TaskSerializer, TaskListSerializer, TaskLinkSerializer, ApprovalRecordSerializer, TaskApprovalSerializer, TaskForwardSerializer, TaskCommentSerializer, TaskAttachmentSerializer, SubtaskAddSerializer, TaskRelationAddSerializer, TaskBulkActionSerializer, TaskFieldHistorySerializer
from task.signals import set_current_user
from task.services import (
    bulk_update_tasks,
    user_can_edit_task,
    add_subtask_to_parent,
    reassign_subtask_parent,
    TaskHierarchyCycleError,
    HIERARCHY_CYCLE_ERROR_CODE,
    hierarchy_cycle_error_message,
)
from task.gantt_service import build_gantt_payload, resolve_sprint_label_from_tasks
from task import intelligence as intel
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from core.models import ProjectMember, Project
from core.utils.project import get_user_active_project
from notifications.models import NotificationCategory, NotificationEventType
from notifications.services import create_notification
from notifications.action_urls import task_action_url
import json
import traceback

# region agent log
def _debug_log(session_id, location, message, data=None, hypothesis_id=None):
    import os
    payload = {"sessionId": session_id, "location": location, "message": message, "timestamp": __import__("time").time() * 1000}
    if data is not None:
        payload["data"] = data
    if hypothesis_id is not None:
        payload["hypothesisId"] = hypothesis_id
    line = json.dumps(payload) + "\n"
    for path in [
        "/Users/huangtaowen/Desktop/Proj/mediaJira/.cursor/debug-70a616.log",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug-70a616.log"),
    ]:
        try:
            with open(path, "a") as f:
                f.write(line)
            break
        except Exception:
            continue
# endregion


def _hierarchy_cycle_response():
    return Response(
        {
            'detail': hierarchy_cycle_error_message(),
            'code': HIERARCHY_CYCLE_ERROR_CODE,
        },
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


class TaskViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """ViewSet for Task model"""
    queryset = Task.objects.select_related(
        'project',
        'owner',
        'created_by',
        'current_approver',
        'meeting_origin__meeting__type_definition',
    )
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user and request.user.is_authenticated:
            set_current_user(request.user)

    def get_serializer_class(self):
        if getattr(self, "action", None) == "list":
            return TaskListSerializer
        return TaskSerializer

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except Exception as e:
            import logging
            logging.getLogger("task.views").error(
                "TaskViewSet.create exception: %s\n%s", e, traceback.format_exc()
            )
            raise

    def force_create(self, request):
        """
        Fallback task creation endpoint.

        - Require the frontend to pass a valid project_id
        - Ensure the current user is a member of this Project (create ProjectMember if it doesn't exist)
        - Use the normal serializer to create Task
        - Don't automatically create Project (avoid too much magic)
        """
        data = request.data.copy()
        project_id = data.get('project_id')

        if not project_id:
            return Response(
                {'error': 'project_id is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get the project (404 if it doesn't exist). Accept slug or numeric pk.
        resolved_pk = resolve_project_pk(project_id)
        if not resolved_pk:
            return Response({'error': 'Project not found'}, status=status.HTTP_404_NOT_FOUND)
        project = get_object_or_404(Project, id=resolved_pk)

        # Ensure the current user is a member of this project
        ProjectMember.objects.get_or_create(
            user=request.user,
            project=project,
            defaults={'is_active': True},
        )

        # Use the normal serializer to create the task
        serializer = self.get_serializer(
            data=data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        task = serializer.save()

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        """
        Echo the client-generated operation ID after a successful update.

        The ID is request metadata only. It is not persisted and therefore
        requires no model or migration change.
        """
        operation_id = request.query_params.get('operation_id')

        if operation_id is not None:
            operation_id = operation_id.strip()
            if not operation_id or len(operation_id) > 128:
                raise DRFValidationError({
                    'operation_id': (
                        'Operation ID must contain between 1 and 128 characters.'
                    ),
                })

        response = super().update(request, *args, **kwargs)

        if operation_id and isinstance(response.data, dict):
            response.data['operation_id'] = operation_id

        return response

    def get_queryset(self):
        """Filter queryset based on user permissions and query parameters"""
        # region agent log
        _debug_log("70a616", "task/views.py:get_queryset", "get_queryset_start", {"user_id": getattr(self.request.user, "id", None)}, "H2")
        # endregion
        user = self.request.user
        if not user.is_authenticated:
            return Task.objects.none()

        queryset = Task.objects.select_related(
            'project',
            'owner',
            'created_by',
            'current_approver',
            'meeting_origin__meeting__type_definition',
        ).annotate(
            subtask_count=Count('subtasks', distinct=True),
            _is_pinned=Exists(
                TaskPin.objects.filter(task_id=OuterRef('pk'), user=user)
            ),
        )
        accessible_project_ids = set(
            ProjectMember.objects.filter(
                user=user,
                is_active=True
            ).values_list('project_id', flat=True)
        )
        
        # Use user.active_project directly to avoid side effects from get_user_active_project
        # which automatically sets active_project if it's None.
        # Guard against stale active_project_id pointing to a deleted project — Django's FK
        # accessor raises DoesNotExist (not returning None) in that case.
        try:
            active_project = user.active_project
        except Project.DoesNotExist:
            active_project = None
            user.active_project = None
            user.save(update_fields=['active_project'])
        # Verify that active_project is still accessible (user still has membership)
        if active_project:
            if active_project.id not in accessible_project_ids:
                # Active project is no longer accessible, clear it
                active_project = None
                user.active_project = None
                user.save(update_fields=['active_project'])

        # Get all_projects parameter to allow fetching tasks from all accessible projects
        all_projects_param = self.request.query_params.get('all_projects', 'false')
        all_projects = all_projects_param.lower() == 'true'

        # Treat blank project_id as absent (?project_id= should not force strict empty scope)
        requested_project_id_raw = self.request.query_params.get('project_id')
        if requested_project_id_raw is not None:
            requested_project_id_raw = str(requested_project_id_raw).strip()
            if requested_project_id_raw == '':
                requested_project_id_raw = None

        has_explicit_project_id = requested_project_id_raw is not None
        requested_project_id = None
        if has_explicit_project_id:
            # Accept slug (current frontend) or numeric pk (legacy); resolve to pk.
            requested_project_id = resolve_project_pk(requested_project_id_raw)

            if requested_project_id not in accessible_project_ids:
                raise PermissionDenied('You do not have access to this project.')

            project_filter = Q(project_id=requested_project_id)
        else:
            # New logic: support all_projects parameter
            if all_projects and accessible_project_ids:
                project_filter = Q(project_id__in=accessible_project_ids)
            elif active_project:
                project_filter = Q(project_id=active_project.id)
            elif accessible_project_ids:
                project_filter = Q(project_id__in=accessible_project_ids)
            else:
                project_filter = Q(pk__in=[])

        include_cross_project_param = self.request.query_params.get(
            'include_cross_project_approvals', 'false'
        )
        include_cross_project_approvals = include_cross_project_param.lower() == 'true'

        # Explicit project_id => default strict scope (only that project). Optional
        # include_cross_project_approvals=true restores union with approver inbox tasks.
        if has_explicit_project_id:
            if include_cross_project_approvals:
                queryset = queryset.filter(project_filter | Q(current_approver=user))
            else:
                queryset = queryset.filter(project_filter)
        else:
            queryset = queryset.filter(project_filter | Q(current_approver=user))

        def _get_multi_values(param_name: str):
            """
            Accept multi-values in either:
            - repeated query params: ?status=A&status=B
            - comma-separated: ?status=A,B
            Returns list[str] (possibly empty).
            """
            values = list(self.request.query_params.getlist(param_name))
            values.extend(self.request.query_params.getlist(param_name + '[]'))
            if not values:
                raw = self.request.query_params.get(param_name)
                if raw:
                    values = [raw]
            expanded = []
            for v in values:
                if v is None:
                    continue
                parts = [p.strip() for p in str(v).split(",")]
                expanded.extend([p for p in parts if p])
            return expanded

        def _parse_int_list(param_name: str):
            raw_values = _get_multi_values(param_name)
            if not raw_values:
                return []
            parsed = []
            for v in raw_values:
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    raise DRFValidationError({param_name: f"{param_name} must be an integer"})
                if iv < 1:
                    raise DRFValidationError({param_name: f"{param_name} must be a positive integer"})
                parsed.append(iv)
            return parsed

        # Apply filters
        task_types = _get_multi_values("type")
        if task_types:
            valid_types = {c[0] for c in Task._meta.get_field("type").choices}
            invalid = [t for t in task_types if t not in valid_types]
            if invalid:
                raise DRFValidationError({"type": "Invalid type value"})
            queryset = queryset.filter(type__in=task_types)

        owner_ids = _parse_int_list("owner_id")
        if owner_ids:
            queryset = queryset.filter(owner_id__in=owner_ids)

        statuses = _get_multi_values("status")
        if statuses:
            valid_statuses = {c[0] for c in Task.Status.choices}
            invalid = [s for s in statuses if s not in valid_statuses]
            if invalid:
                raise DRFValidationError({"status": "Invalid status value"})
            queryset = queryset.filter(status__in=statuses)

        # Priority filter (multi-select)
        priorities = _get_multi_values("priority")
        if priorities:
            valid_priorities = {c[0] for c in Task.Priority.choices}
            invalid = [p for p in priorities if p not in valid_priorities]
            if invalid:
                raise DRFValidationError({"priority": "Invalid priority value"})
            queryset = queryset.filter(priority__in=priorities)

        # Current approver (assignee) filter (multi-select)
        approver_ids = _parse_int_list("current_approver_id")
        if approver_ids:
            queryset = queryset.filter(current_approver_id__in=approver_ids)

        # Due date range filters
        due_date_after = self.request.query_params.get('due_date_after')
        if due_date_after:
            try:
                d = datetime.strptime(due_date_after, '%Y-%m-%d').date()
                queryset = queryset.filter(due_date__gte=d)
            except (ValueError, TypeError):
                raise DRFValidationError({'due_date_after': 'due_date_after must be YYYY-MM-DD'})
        due_date_before = self.request.query_params.get('due_date_before')
        if due_date_before:
            try:
                d = datetime.strptime(due_date_before, '%Y-%m-%d').date()
                queryset = queryset.filter(due_date__lte=d)
            except (ValueError, TypeError):
                raise DRFValidationError({'due_date_before': 'due_date_before must be YYYY-MM-DD'})

        # Created date range filters (compare date part of created_at)
        created_after = self.request.query_params.get('created_after')
        if created_after:
            try:
                if 'T' in created_after or ' ' in created_after:
                    d = datetime.fromisoformat(created_after.replace('Z', '+00:00')).date()
                else:
                    d = datetime.strptime(created_after, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=d)
            except (ValueError, TypeError):
                raise DRFValidationError({'created_after': 'created_after must be YYYY-MM-DD or ISO datetime'})
        created_before = self.request.query_params.get('created_before')
        if created_before:
            try:
                if 'T' in created_before or ' ' in created_before:
                    d = datetime.fromisoformat(created_before.replace('Z', '+00:00')).date()
                else:
                    d = datetime.strptime(created_before, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=d)
            except (ValueError, TypeError):
                raise DRFValidationError({'created_before': 'created_before must be YYYY-MM-DD or ISO datetime'})

        # Filter by content_type
        content_type = self.request.query_params.get('content_type')
        if content_type:
            try:
                ct = ContentType.objects.get(model=content_type)
                queryset = queryset.filter(content_type=ct)
            except ContentType.DoesNotExist:
                # If content_type doesn't exist, return empty queryset
                return Task.objects.none()
        
        # Filter by object_id
        object_id = self.request.query_params.get('object_id')
        if object_id:
            queryset = queryset.filter(object_id=object_id)
        
        # Exclude subtasks - only show parent tasks in the listing
        # A task is a subtask if its is_subtask field is True (persistent even after parent deletion)
        # Allow including subtasks if explicitly requested (e.g., for subtask selection)
        include_subtasks_param = self.request.query_params.get('include_subtasks', 'false')
        include_subtasks = include_subtasks_param.lower() == 'true'

        if not include_subtasks:
            # Exclude all tasks that have is_subtask=True
            queryset = queryset.filter(is_subtask=False)

        # Parent filter: show only subtasks or only top-level tasks
        has_parent_param = self.request.query_params.get('has_parent')
        if has_parent_param is not None:
            if has_parent_param.lower() == 'true':
                queryset = queryset.filter(is_subtask=True)
            elif has_parent_param.lower() == 'false':
                queryset = queryset.filter(is_subtask=False)
            else:
                raise DRFValidationError({'has_parent': 'has_parent must be true or false'})

        search_param = self.request.query_params.get('search')
        search_rank_order = None
        if search_param is not None:
            search_param = str(search_param).strip()
            if search_param:
                # Match visible task title only; slug can contain unrelated letters
                # (e.g. summary "A" with slug "final-campaign-performance-summary").
                search_q = Q(summary__icontains=search_param)
                if search_param.isdigit():
                    search_q |= Q(pk=int(search_param))
                queryset = queryset.filter(search_q)
                search_rank_order = Case(
                    When(summary__iexact=search_param, then=Value(0)),
                    When(summary__istartswith=search_param, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                )

        # Tag names filter — matches tasks that have at least one of the given tag names.
        tag_names = _get_multi_values("tag_names")
        if tag_names:
            from django.db.models import Q as _Q
            tag_q = _Q()
            for name in tag_names:
                name = name.strip()
                if name:
                    tag_q |= _Q(tags__contains=[{'name': name}])
            queryset = queryset.filter(tag_q)

        # Personal pins should float to the top without changing the project order.
        if search_rank_order is not None:
            queryset = queryset.annotate(_search_rank=search_rank_order).order_by(
                '_search_rank',
                '-_is_pinned',
                'order_in_project',
                '-id',
            )
        else:
            queryset = queryset.order_by('-_is_pinned', 'order_in_project', '-id')
        # List response does not include draft_payload; defer it so list works if migration adding the column is not yet applied.
        if getattr(self, 'action', None) in ('list', 'gantt'):
            queryset = queryset.defer('draft_payload')
        # region agent log
        _debug_log("70a616", "task/views.py:get_queryset", "get_queryset_end", None, "H2")
        # endregion
        return queryset

    def list(self, request, *args, **kwargs):
        # region agent log
        _debug_log("70a616", "task/views.py:list", "list_start", {"query": dict(request.query_params)}, "H5")
        # endregion
        try:
            response = super().list(request, *args, **kwargs)
            # region agent log
            _debug_log("70a616", "task/views.py:list", "list_end", None, "H5")
            # endregion
            return response
        except Exception as e:
            # region agent log
            _debug_log("70a616", "task/views.py:list", "list_failed", {
                "exc_type": type(e).__name__,
                "exc_message": str(e),
                "traceback": traceback.format_exc(),
            }, "H2_H3_H5")
            # endregion
            raise

    def _resolve_tag_catalog_project_id(self, request):
        user = request.user
        accessible_ids = set(
            ProjectMember.objects.filter(user=user, is_active=True)
            .values_list('project_id', flat=True)
        )

        project_id_param = request.query_params.get('project_id')
        if project_id_param:
            pid = resolve_project_pk(project_id_param)
            if pid is None:
                raise DRFValidationError({'project_id': 'Unknown project'})
            if pid not in accessible_ids:
                raise PermissionDenied('You do not have access to this project.')
            return pid

        try:
            active = user.active_project
        except Project.DoesNotExist:
            active = None
        if not active or active.id not in accessible_ids:
            return None
        return active.id

    @action(detail=False, methods=['get'], url_path='tag-catalog')
    def tag_catalog(self, request):
        """
        GET /api/tasks/tag-catalog/?project_id=<id>

        Returns the deduplicated set of tags currently applied to any task in the
        project, so all members of a project share the same label library.
        Falls back to the user's active project when project_id is omitted.
        """
        pid = self._resolve_tag_catalog_project_id(request)
        if pid is None:
            return Response({'tags': []})

        seen = {}
        for raw in Task.objects.filter(project_id=pid).values_list('tags', flat=True):
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get('name') or '').strip()
                if not name:
                    continue
                key = name.casefold()
                if key in seen:
                    continue
                color = str(item.get('color') or '').strip().upper() or '#6B7280'
                seen[key] = {'name': name, 'color': color}

        tags = sorted(seen.values(), key=lambda t: t['name'].lower())
        return Response({'tags': tags})

    @action(detail=False, methods=['delete'], url_path='tag-catalog')
    def delete_tag(self, request):
        """
        DELETE /api/tasks/tag-catalog/?project_id=<id>&name=<tag>

        Removes the tag from all tasks in the project. The catalog is derived
        from task tags, so this is the persistent delete operation for a tag.
        """
        pid = self._resolve_tag_catalog_project_id(request)
        if pid is None:
            return Response({'name': '', 'updated_tasks': 0})

        name = str(
            request.query_params.get('name')
            or getattr(request, 'data', {}).get('name', '')
            or ''
        ).strip()
        if not name:
            raise DRFValidationError({'name': 'Tag name is required'})
        key = name.casefold()

        updated_count = 0
        with transaction.atomic():
            for task in Task.objects.select_for_update().filter(project_id=pid):
                raw_tags = task.tags if isinstance(task.tags, list) else []
                next_tags = []
                removed = False
                for item in raw_tags:
                    item_name = ''
                    if isinstance(item, dict):
                        item_name = str(item.get('name') or '').strip()
                    elif isinstance(item, str):
                        item_name = item.strip().lstrip('#').strip()
                    if item_name and item_name.casefold() == key:
                        removed = True
                        continue
                    next_tags.append(item)
                if removed:
                    task.tags = next_tags
                    task.save(update_fields=['tags', 'updated_at'])
                    updated_count += 1

        return Response({'name': name, 'updated_tasks': updated_count})

    @action(detail=False, methods=['get'], url_path='intelligence')
    def intelligence(self, request):
        """
        GET /api/task/tasks/intelligence/?project_id=<id>[&stall_days=7][&due_soon_days=7][&activity_limit=20][&velocity_weeks=8]

        Returns a single payload with all task-intelligence signals for the
        given project (or the user's active project when project_id is omitted).
        """
        from datetime import date as _date

        user = request.user
        accessible_ids = set(
            ProjectMember.objects.filter(user=user, is_active=True)
            .values_list('project_id', flat=True)
        )

        project_id_param = request.query_params.get('project_id')
        if project_id_param:
            pid = resolve_project_pk(project_id_param)
            if pid is None:
                return Response({'detail': 'Invalid project_id.'}, status=status.HTTP_400_BAD_REQUEST)
            if pid not in accessible_ids:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            project_ids = [pid]
        else:
            try:
                active = user.active_project
            except Project.DoesNotExist:
                active = None
            project_ids = [active.id] if active and active.id in accessible_ids else list(accessible_ids)

        if not project_ids:
            return Response({'detail': 'No accessible projects.'}, status=status.HTTP_404_NOT_FOUND)

        stall_days = max(1, int(request.query_params.get('stall_days', 7)))
        due_soon_days = max(1, int(request.query_params.get('due_soon_days', 7)))
        activity_limit = min(100, max(1, int(request.query_params.get('activity_limit', 20))))
        velocity_weeks = max(1, int(request.query_params.get('velocity_weeks', 8)))
        today = _date.today()

        def _task_stub(t):
            return {
                'id': t.id,
                'summary': t.summary,
                'status': t.status,
                'priority': getattr(t, 'priority', None),
                'type': t.type,
                'due_date': t.due_date.isoformat() if t.due_date else None,
                'project_id': t.project_id,
                'owner': {'id': t.owner_id, 'username': t.owner.username} if t.owner_id else None,
                'current_approver': {'id': t.current_approver_id, 'username': t.current_approver.username}
                    if t.current_approver_id else None,
            }

        def _qs_to_stubs(qs):
            return [_task_stub(t) for t in qs]

        def _activity_entry(h):
            return {
                'task_id': h.task_id,
                'task_summary': h.task.summary,
                'field': h.field_name,
                'old_value': h.old_value,
                'new_value': h.new_value,
                'changed_by': h.changed_by.username if h.changed_by else None,
                'changed_at': h.changed_at.isoformat(),
            }

        overdue_qs = intel.overdue_tasks(project_ids, today)
        due_soon_qs = intel.due_soon_tasks(project_ids, due_soon_days, today)
        blocked_qs = intel.blocked_tasks(project_ids)
        high_priority_qs = intel.high_priority_incomplete_tasks(project_ids)
        awaiting_qs = intel.awaiting_approval_tasks(project_ids)
        stalled_qs = intel.stalled_tasks(project_ids, stall_days, today)

        return Response({
            'overdue': {
                'count': overdue_qs.count(),
                'tasks': _qs_to_stubs(overdue_qs),
            },
            'due_soon': {
                'count': due_soon_qs.count(),
                'tasks': _qs_to_stubs(due_soon_qs),
                'days_window': due_soon_days,
            },
            'blocked': {
                'count': blocked_qs.count(),
                'tasks': _qs_to_stubs(blocked_qs),
            },
            'high_priority': {
                'count': high_priority_qs.count(),
                'tasks': _qs_to_stubs(high_priority_qs),
            },
            'awaiting_approval': {
                'count': awaiting_qs.count(),
                'tasks': _qs_to_stubs(awaiting_qs),
            },
            'stalled': {
                'count': stalled_qs.count(),
                'tasks': _qs_to_stubs(stalled_qs),
                'stall_days': stall_days,
            },
            'progress': intel.progress_counts(project_ids),
            'recent_activity': [_activity_entry(h) for h in intel.recent_activity(project_ids, activity_limit)],
            'velocity': intel.velocity_trend(project_ids, velocity_weeks, today),
            'risk': intel.risk_summary(project_ids, today, stall_days),
        })

    @action(detail=False, methods=['get'], url_path='work-cycle')
    def work_cycle(self, request):
        """
        GET /api/tasks/work-cycle/?project_id=<id>&from=YYYY-MM-DD&to=YYYY-MM-DD

        Returns grouped task changes (added, completed, field changes) within
        the requested date window.
        """
        from datetime import date as _date

        user = request.user
        accessible_ids = set(
            ProjectMember.objects.filter(user=user, is_active=True)
            .values_list('project_id', flat=True)
        )

        project_id_param = request.query_params.get('project_id')
        if project_id_param:
            pid = resolve_project_pk(project_id_param)
            if pid is None:
                return Response({'detail': 'Invalid project_id.'}, status=status.HTTP_400_BAD_REQUEST)
            if pid not in accessible_ids:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            project_ids = [pid]
        else:
            try:
                active = user.active_project
            except Project.DoesNotExist:
                active = None
            project_ids = [active.id] if active and active.id in accessible_ids else list(accessible_ids)

        if not project_ids:
            return Response({'detail': 'No accessible projects.'}, status=status.HTTP_404_NOT_FOUND)

        today = _date.today()
        try:
            date_to = _date.fromisoformat(request.query_params.get('to', today.isoformat()))
        except ValueError:
            return Response({'detail': 'Invalid to date.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            default_from = (date_to.replace(day=1)).isoformat()
            date_from = _date.fromisoformat(request.query_params.get('from', default_from))
        except ValueError:
            return Response({'detail': 'Invalid from date.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(intel.work_cycle_history(project_ids, date_from, date_to))

    @action(detail=False, methods=['get'], url_path='my-actions')
    def my_actions(self, request):
        """GET /api/tasks/my-actions/?project_id=<id>&due_soon_days=7"""
        from datetime import date as _date

        user = request.user
        accessible_ids = set(
            ProjectMember.objects.filter(user=user, is_active=True)
            .values_list('project_id', flat=True)
        )

        project_id_param = request.query_params.get('project_id')
        if project_id_param:
            pid = resolve_project_pk(project_id_param)
            if pid is None:
                return Response({'detail': 'Invalid project_id.'}, status=status.HTTP_400_BAD_REQUEST)
            if pid not in accessible_ids:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            project_ids = [pid]
        else:
            try:
                active = user.active_project
            except Project.DoesNotExist:
                active = None
            project_ids = [active.id] if active and active.id in accessible_ids else list(accessible_ids)

        if not project_ids:
            return Response({'detail': 'No accessible projects.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            due_soon_days = max(1, int(request.query_params.get('due_soon_days', 7)))
        except (ValueError, TypeError):
            due_soon_days = 7

        today = _date.today()
        return Response(intel.my_actions(user, project_ids, today, due_soon_days))

    @action(detail=False, methods=['get'], url_path='status-report')
    def status_report(self, request):
        from datetime import date, timedelta
        from task.status_report import generate_status_report

        project_id_param = request.query_params.get('project_id')
        if not project_id_param:
            return Response({'error': 'project_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        project_id = resolve_project_pk(project_id_param)
        if project_id is None:
            return Response({'error': 'Invalid project_id'}, status=status.HTTP_400_BAD_REQUEST)

        has_membership = ProjectMember.objects.filter(
            user=request.user,
            project_id=project_id,
            is_active=True,
        ).exists()
        if not has_membership:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        period = request.query_params.get('period', 'week')
        today = date.today()

        if period == 'month':
            date_from = today - timedelta(days=30)
            date_to = today
        elif period == 'custom':
            from_str = request.query_params.get('date_from')
            to_str = request.query_params.get('date_to')
            try:
                date_from = date.fromisoformat(from_str)
                date_to = date.fromisoformat(to_str)
            except (TypeError, ValueError):
                return Response({'error': 'Invalid date_from or date_to'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            date_from = today - timedelta(days=7)
            date_to = today

        data = generate_status_report(project_id, date_from, date_to)
        return Response(data)

    def gantt(self, request, *args, **kwargs):
        """
        Return chart-ready task rows for the Gantt view (same filters as list).

        GET /api/tasks/gantt/?project_id=...
        """
        queryset = self.filter_queryset(self.get_queryset())
        tasks = list(queryset)
        today = timezone.now().date()
        label = resolve_sprint_label_from_tasks(tasks)
        data = build_gantt_payload(tasks, today=today, sprint_label=label)
        return Response(data)

    def get_object(self):
        """
        Retrieve a single task object.

        Unlike list(), this should not depend on the user's active_project.
        Instead, we:
        - fetch the task by slug or numeric primary key
        - verify the authenticated user has membership in the task's project
        """
        from rest_framework.exceptions import PermissionDenied  # local import to avoid circulars

        # Base queryset without project filtering so we can locate the task by ID
        base_qs = Task.objects.select_related(
            'project',
            'owner',
            'created_by',
            'current_approver',
            'meeting_origin__meeting__type_definition',
        )
        lookup_value = self.kwargs.get('pk')
        filter_kwargs = resolve_task_lookup_kwargs(lookup_value, 'pk')
        task = get_object_or_404(base_qs, **filter_kwargs)

        user = self.request.user
        if not user.is_authenticated:
            raise PermissionDenied('Authentication credentials were not provided.')

        # Ensure the user is an active member of the task's project,
        # OR is the current designated approver (cross-project approval chain).
        has_membership = ProjectMember.objects.filter(
            user=user,
            project=task.project,
            is_active=True,
        ).exists()
        is_current_approver = (
            task.current_approver_id is not None and
            task.current_approver_id == user.id
        )

        if not has_membership and not is_current_approver:
            raise PermissionDenied('You do not have access to this task.')

        self.check_object_permissions(self.request, task)
        return task
    
    # ── internal helper ───────────────────────────────────────────────────────
    @staticmethod
    def _user_display_name(user_id) -> str | None:
        """Return 'First Last' (or username) for a user id, or None if not found."""
        if user_id is None:
            return None
        User = get_user_model()
        row = User.objects.filter(pk=user_id).values(
            "username", "first_name", "last_name"
        ).first()
        if not row:
            return None
        full = f"{row['first_name']} {row['last_name']}".strip()
        return full or row["username"]

    def perform_create(self, serializer):
        """Create a new task and notify the assigned owner and approver (if not the creator)."""
        task = serializer.save()

        # Notify owner if assigned and not the creator
        if task.owner_id and task.owner_id != self.request.user.id:
            # Mark invite as pending before notifying
            task.owner_invite_pending = True
            task.save(update_fields=["owner_invite_pending"])
            create_notification(
                recipient_id=task.owner_id,
                actor_id=self.request.user.id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_ASSIGNED,
                title=f"Task assigned: {task.summary}",
                body="You have been assigned to a new task.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=task_action_url(task.slug),
                metadata={
                    "task_id": task.id,
                    "project_id": task.project_id,
                    "change_type": "task_assignee",
                    "old_value": None,
                    "new_value": self._user_display_name(task.owner_id),
                },
            )

        # Notify approver if assigned and not the creator
        if task.current_approver_id and task.current_approver_id != self.request.user.id:
            # Mark approver invite as pending before notifying
            task.approver_invite_pending = True
            task.save(update_fields=["approver_invite_pending"])
            create_notification(
                recipient_id=task.current_approver_id,
                actor_id=self.request.user.id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_ASSIGNED,
                title=f"Approval requested: {task.summary}",
                body="You have been assigned as approver for this task.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=task_action_url(task.slug),
                metadata={
                    "task_id": task.id,
                    "project_id": task.project_id,
                    "change_type": "task_approver",
                    "old_value": None,
                    "new_value": self._user_display_name(task.current_approver_id),
                },
            )

        return task

    def perform_update(self, serializer):
        """Update a task and fire targeted notifications for key field changes."""
        task = serializer.instance
        if not user_can_edit_task(self.request.user, task):
            raise PermissionDenied(
                'Only the task owner, current approver, or unassigned draft creator can edit this task.'
            )

        # Snapshot all watched fields BEFORE saving.
        old_owner_id   = task.owner_id
        old_approver_id = task.current_approver_id
        old_due_date   = task.due_date
        old_summary    = task.summary
        old_priority   = task.priority

        validated_field_names = set(serializer.validated_data.keys())
        attempted_history_fields = {
            field_name
            for field_name in TaskFieldHistory.TRACKED_FIELDS
            if (
                field_name in validated_field_names
                or Task._meta.get_field(field_name).attname
                in validated_field_names
            )
        }

        task._attempted_task_field_history_fields = (
            attempted_history_fields
        )

        try:
            serializer.save()
        finally:
            if hasattr(
                task,
                '_attempted_task_field_history_fields',
            ):
                del task._attempted_task_field_history_fields

        from meetings.models import MeetingTaskOrigin
        from meetings.services import record_task_updated
        origin = MeetingTaskOrigin.objects.filter(task=task).select_related('meeting').first()
        if origin:
            record_task_updated(
                meeting=origin.meeting,
                task_id=task.id,
                actor=self.request.user,
            )

        actor_id   = self.request.user.id
        project_id = task.project_id
        action_url = task_action_url(task.slug)
        task_meta  = {"task_id": task.id, "project_id": project_id}

        # ── Owner reassigned ──────────────────────────────────────────────────
        if task.owner_id and task.owner_id != old_owner_id:
            # New owner must accept before they receive further change notifications
            task.owner_invite_pending = True
            task.save(update_fields=["owner_invite_pending"])
            create_notification(
                recipient_id=task.owner_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task reassigned: {task.summary}",
                body="You are now the owner of this task.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_assignee",
                    "old_value": self._user_display_name(old_owner_id),
                    "new_value": self._user_display_name(task.owner_id),
                },
            )

        # ── Approver changed ──────────────────────────────────────────────────
        if task.current_approver_id and task.current_approver_id != old_approver_id:
            task.approver_invite_pending = True
            task.save(update_fields=["approver_invite_pending"])
            create_notification(
                recipient_id=task.current_approver_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_ASSIGNED,
                title=f"Approval requested: {task.summary}",
                body="You are the current approver for this task.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_approver",
                    "old_value": self._user_display_name(old_approver_id),
                    "new_value": self._user_display_name(task.current_approver_id),
                },
            )

        # Skip change notifications for users who have not yet accepted their assignment
        owner_accepted = task.owner_id and not task.owner_invite_pending
        approver_accepted = task.current_approver_id and not task.approver_invite_pending

        # ── Due date changed ──────────────────────────────────────────────────
        if task.due_date != old_due_date and owner_accepted and task.owner_id != actor_id:
            create_notification(
                recipient_id=task.owner_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task deadline updated: {task.summary}",
                body="The deadline for this task has been changed.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_due_date",
                    "old_value": str(old_due_date) if old_due_date else None,
                    "new_value": str(task.due_date) if task.due_date else None,
                },
            )

        # Notify approver of due date change
        if task.due_date != old_due_date and approver_accepted and task.current_approver_id != actor_id:
            create_notification(
                recipient_id=task.current_approver_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task deadline updated: {task.summary}",
                body="The deadline for this task has been changed.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_due_date",
                    "old_value": str(old_due_date) if old_due_date else None,
                    "new_value": str(task.due_date) if task.due_date else None,
                },
            )

        # ── Title / summary changed ───────────────────────────────────────────
        if task.summary != old_summary and owner_accepted and task.owner_id != actor_id:
            create_notification(
                recipient_id=task.owner_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task updated: {task.summary}",
                body="The title of this task was changed.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_title",
                    "old_value": old_summary,
                    "new_value": task.summary,
                },
            )

        # Notify approver of summary change
        if task.summary != old_summary and approver_accepted and task.current_approver_id != actor_id:
            create_notification(
                recipient_id=task.current_approver_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task updated: {task.summary}",
                body="The title of this task was changed.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_title",
                    "old_value": old_summary,
                    "new_value": task.summary,
                },
            )

        # ── Priority changed ──────────────────────────────────────────────────
        if task.priority != old_priority and owner_accepted and task.owner_id != actor_id:
            create_notification(
                recipient_id=task.owner_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task priority changed: {task.summary}",
                body=f"Priority changed from {old_priority} to {task.priority}.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_priority",
                    "old_value": old_priority,
                    "new_value": task.priority,
                },
            )

        # Notify approver of priority change
        if task.priority != old_priority and approver_accepted and task.current_approver_id != actor_id:
            create_notification(
                recipient_id=task.current_approver_id,
                actor_id=actor_id,
                category=NotificationCategory.TASKS,
                event_type=NotificationEventType.TASK_OWNER_CHANGED,
                title=f"Task priority changed: {task.summary}",
                body=f"Priority changed from {old_priority} to {task.priority}.",
                related_object_type="task",
                related_object_id=str(task.id),
                action_url=action_url,
                metadata={
                    **task_meta,
                    "change_type": "task_priority",
                    "old_value": old_priority,
                    "new_value": task.priority,
                },
            )

    def pin(self, request, pk=None):
        """Pin a task for the current user."""
        task = self.get_object()
        TaskPin.objects.get_or_create(task=task, user=request.user)
        task._is_pinned = True
        serializer = self.get_serializer(task, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def unpin(self, request, pk=None):
        """Remove the current user's pin from a task."""
        task = self.get_object()
        TaskPin.objects.filter(task=task, user=request.user).delete()
        task._is_pinned = False
        serializer = self.get_serializer(task, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='field-history')
    def field_history(self, request, pk=None):
        """Get field change history for a task."""
        task = self.get_object()
        qs = (
            TaskFieldHistory.objects
            .filter(task=task)
            .select_related('changed_by')
            .order_by('-changed_at', '-pk')
        )
        try:
            page_size = max(1, min(int(request.query_params.get('page_size', 20)), 100))
            page = max(1, int(request.query_params.get('page', 1)))
        except (ValueError, TypeError):
            page_size, page = 20, 1
        total = qs.count()
        offset = (page - 1) * page_size
        serializer = TaskFieldHistorySerializer(qs[offset:offset + page_size], many=True)
        return Response({
            'count': total,
            'page': page,
            'page_size': page_size,
            'has_more': offset + page_size < total,
            'results': serializer.data,
        })
    
    def perform_destroy(self, instance):
        """
        Delete task and its linked retrospective object if it's a retrospective task
        """
        # If this is a retrospective task, delete the linked RetrospectiveTask first
        if instance.type == 'retrospective' and instance.content_type and instance.object_id:
            try:
                # Get the ContentType for RetrospectiveTask
                from retrospective.models import RetrospectiveTask
                retrospective_content_type = ContentType.objects.get_for_model(RetrospectiveTask)
                
                # Check if the task is linked to a RetrospectiveTask
                if instance.content_type == retrospective_content_type:
                    try:
                        # Get and delete the RetrospectiveTask
                        retrospective = RetrospectiveTask.objects.get(id=instance.object_id)
                        retrospective.delete()
                        print(f"Deleted RetrospectiveTask {instance.object_id} linked to Task {instance.id}")
                    except RetrospectiveTask.DoesNotExist:
                        print(f"RetrospectiveTask {instance.object_id} not found, skipping deletion")
                    except Exception as e:
                        print(f"Error deleting RetrospectiveTask {instance.object_id}: {e}")
            except Exception as e:
                print(f"Error checking RetrospectiveTask for deletion: {e}")

        try:
            from linear_integration.models import LinearTaskLink

            LinearTaskLink.objects.filter(task_id=instance.pk).delete()
        except (ImportError, DatabaseError):
            pass

        from meetings.models import MeetingTaskOrigin
        from meetings.services import record_task_deleted
        origin = MeetingTaskOrigin.objects.filter(task=instance).select_related('meeting').first()
        meeting = origin.meeting if origin else None
        task_id = instance.id

        # Delete the task itself
        instance.delete()

        if meeting:
            record_task_deleted(
                meeting=meeting,
                task_id=task_id,
                actor=self.request.user,
            )

    @action(detail=False, methods=['post'], url_path='bulk_action')
    def bulk_action(self, request):
        """Apply bulk task updates in one atomic operation."""
        serializer = TaskBulkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        task_ids = data.pop('task_ids')
        result = bulk_update_tasks(
            user=request.user,
            task_ids=task_ids,
            updates=data,
        )

        if result['failed']:
            return Response(
                {
                    'detail': 'Bulk action failed. No tasks were updated.',
                    'result': result,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'detail': f"Bulk action completed successfully. Updated {result['updated_count']} task(s).",
                'result': result,
            },
            status=status.HTTP_200_OK,
        )
    
    @action(detail=True, methods=['post'])
    def link(self, request, pk=None):
        """Link task to an existing object"""
        task = self.get_object()
        
        # Validate link data
        serializer = TaskLinkSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Get the linked object from validated data
        linked_object = serializer.validated_data['linked_object']

        # Check if task is already linked
        if task.is_linked:
            # Idempotent: if already linked to the same object, return success
            ct = ContentType.objects.get_for_model(linked_object.__class__)
            if task.content_type_id == ct.id and task.object_id == str(linked_object.id):
                task_serializer = TaskSerializer(task, context={'request': request})
                return Response(task_serializer.data, status=status.HTTP_200_OK)
            return Response(
                {'error': 'Task is already linked to a different object'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Link the task to the object
        task.link_to_object(linked_object)
        
        # Save the task to persist the link
        task.save()
        
        # Task is already in SUBMITTED status from creation
        # No need to call submit() again
        
        # Return the updated task
        task_serializer = TaskSerializer(task, context={'request': request})
        return Response(task_serializer.data, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['post'])
    def make_approval(self, request, pk=None):
        """Make approval decision (approve or reject) for a task"""
        # Resolve the row up front so we lock the correct pk (routes may be slug-based).
        task = self.get_object()

        # Validate request data before opening a transaction
        serializer = TaskApprovalSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        action = serializer.validated_data['action']
        comment = serializer.validated_data.get('comment', '')

        try:
            # Serialize concurrent approvals: lock the row, then re-read status.
            # A second approver acting at the same time blocks until the first
            # transaction commits, then sees the already-decided status here and
            # is rejected with 409 instead of overwriting the winning transition.
            with transaction.atomic():
                task = Task.lock_for_transition(task.pk)
                if task is None:
                    return Response(
                        {'error': 'Task not found.'},
                        status=status.HTTP_404_NOT_FOUND
                    )

                # Re-validate under lock: task must still be awaiting a decision.
                if task.status != Task.Status.UNDER_REVIEW:
                    # A task that already moved to a decided state means another
                    # approver won a concurrent race → 409 so the loser refreshes.
                    # Never-review-ready states (DRAFT/SUBMITTED) are a plain 400.
                    decided_states = {
                        Task.Status.APPROVED,
                        Task.Status.REJECTED,
                        Task.Status.LOCKED,
                        Task.Status.CANCELLED,
                    }
                    if task.status in decided_states:
                        return Response(
                            {'error': 'This task has already been decided by another approver.'},
                            status=status.HTTP_409_CONFLICT
                        )
                    return Response(
                        {'error': 'Task must be in UNDER_REVIEW status to be approved or rejected'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Designated chain approver, or same-org org-admin override on budget (MED-240).
                is_current_approver = (
                    task.current_approver_id is not None
                    and request.user.id == task.current_approver_id
                )
                is_admin_override = False
                if task.current_approver_id and not is_current_approver:
                    if task.type == 'budget':
                        from budget_approval.approver_access import (
                            is_org_admin_override_action,
                            user_may_process_budget_approval,
                        )
                        from budget_approval.models import BudgetRequest
                        br = task.linked_object or task.budget_requests.first()
                        if br is None:
                            br = (
                                BudgetRequest.objects.filter(task_id=task.pk)
                                .order_by('-id')
                                .first()
                            )
                        if br is not None and user_may_process_budget_approval(request.user, br):
                            is_admin_override = is_org_admin_override_action(request.user, br)
                        else:
                            return Response(
                                {'error': 'Only the designated approver for this step can approve or reject.'},
                                status=status.HTTP_403_FORBIDDEN
                            )
                    else:
                        return Response(
                            {'error': 'Only the designated approver for this step can approve or reject.'},
                            status=status.HTTP_403_FORBIDDEN
                        )

                # Execute the action
                if action == 'approve':
                    task.approve()   # UNDER_REVIEW → APPROVED
                    is_approved = True
                else:  # action == 'reject'
                    task.reject()    # UNDER_REVIEW → REJECTED
                    is_approved = False

                # Record the decision for the current step
                step_number = (
                    task.current_approval_step
                    if task.current_approval_step
                    else task.approval_records.count() + 1
                )
                # Attribute the decision to the actor (org-admin on override, not the chain assignee).
                from budget_approval.approver_access import (
                    format_org_admin_override_marker,
                )
                record_comment = comment
                if is_admin_override:
                    decision = 'approve' if is_approved else 'reject'
                    marker = format_org_admin_override_marker(
                        user_id=request.user.id,
                        decision=decision,
                        replaced_step=step_number,
                        timestamp=timezone.now().isoformat(),
                    )
                    record_comment = f'{marker}\n{comment}'.strip() if comment else marker
                ApprovalRecord.objects.create(
                    task=task,
                    approved_by=request.user,
                    is_approved=is_approved,
                    comment=record_comment,
                    step_number=step_number,
                    revision_round=task.revision_round,
                    resubmitted_after_reject=task.revision_round > 0,
                    has_rejection_history=task.approval_records.filter(is_approved=False).exists()
                )

                # If approved and a chain is active, auto-advance to the next step
                if is_approved and task.approval_chain and task.current_approval_step:
                    next_step_num = task.current_approval_step + 1
                    next_step = task.approval_chain.get_step(next_step_num)
                    if next_step:
                        # More steps remain: APPROVED → UNDER_REVIEW with next approver
                        task.forward_to_next()
                        task.current_approver = next_step.approver
                        task.current_approval_step = next_step_num
                    # else: chain is complete — task stays APPROVED, ready to be locked

                # Save all changes
                task.save()

            # Sync budget request status when a budget task is approved or rejected
            if task.type == 'budget':
                try:
                    from budget_approval.models import BudgetRequest, BudgetRequestStatus
                    from budget_approval.services import BudgetRequestService
                    br = task.linked_object or task.budget_requests.first()
                    if isinstance(br, BudgetRequest):
                        next_approver = None
                        if is_approved and task.status == Task.Status.UNDER_REVIEW:
                            next_approver = task.current_approver
                        if br.status == BudgetRequestStatus.SUBMITTED:
                            br = BudgetRequestService.start_review(br)
                        if br.status == BudgetRequestStatus.UNDER_REVIEW:
                            br = BudgetRequestService.process_approval(
                                budget_request=br,
                                approver=request.user,
                                is_approved=is_approved,
                                comment=comment,
                                next_approver=next_approver,
                            )
                        elif (
                            not is_approved
                            and br.status == BudgetRequestStatus.SUBMITTED
                        ):
                            br = BudgetRequestService.start_review(br)
                            br = BudgetRequestService.process_approval(
                                budget_request=br,
                                approver=request.user,
                                is_approved=False,
                                comment=comment,
                            )
                except Exception as e:
                    logger.error('Budget sync failed on task %s approval: %s', task.id, e, exc_info=True)

            # Return both the approval record and updated task data
            approval_serializer = ApprovalRecordSerializer(
                task.approval_records.latest('step_number')
            )
            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({
                'approval_record': approval_serializer.data,
                'task': task_serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a task"""
        task = self.get_object()

        # Validate task can be cancelled (status check first)
        cancellable_statuses = [
            Task.Status.SUBMITTED,
            Task.Status.UNDER_REVIEW,
            Task.Status.APPROVED,
            Task.Status.REJECTED
        ]
        if task.status not in cancellable_statuses:
            return Response(
                {'error': 'Task cannot be cancelled in current status'},
                status=status.HTTP_400_BAD_REQUEST
            )

        is_approver = task.current_approver_id and task.current_approver_id == request.user.id
        is_owner = task.owner_id and task.owner_id == request.user.id
        if task.status == Task.Status.SUBMITTED:
            if not is_approver and not is_owner:
                return Response({'error': 'Only the task owner or approver can cancel this task.'}, status=status.HTTP_403_FORBIDDEN)
        elif task.current_approver_id and not is_approver:
            return Response({'error': 'Only the task approver can cancel this task.'}, status=status.HTTP_403_FORBIDDEN)
        
        try:
            # Cancel the task
            task.cancel()
            task.save()

            # If this is a budget task, cancel the linked budget request (reverses pool if locked)
            if task.type == 'budget':
                try:
                    from budget_approval.models import BudgetRequest
                    from budget_approval.services import BudgetRequestService
                    br = task.linked_object or task.budget_requests.first()
                    if isinstance(br, BudgetRequest):
                        BudgetRequestService.cancel_budget_request(
                            br,
                            actor_id=request.user.id,
                        )
                except Exception as e:
                    logger.error('Budget cancel sync failed on task %s: %s', task.id, e, exc_info=True)

            # Delete all approval records
            task.approval_records.all().delete()
            
            # Return task
            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({
                'task': task_serializer.data,
                'approval_record': None
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['get'])
    def approval_history(self, request, pk=None):
        """Get approval history for a task"""
        task = self.get_object()

        # Get approval records ordered by step_number
        approval_records = task.approval_records.all().order_by('step_number')

        # Build role_name lookup from chain name (e.g. "Buyer → Lead → Client")
        role_labels = {}
        if task.approval_chain:
            parts = [p.strip() for p in task.approval_chain.name.split('→')]
            role_labels = {i + 1: label for i, label in enumerate(parts)}

        # Serialize and annotate each record with its role_name
        approval_serializer = ApprovalRecordSerializer(approval_records, many=True)
        history = []
        for record_data in approval_serializer.data:
            step_num = record_data.get('step_number')
            entry = dict(record_data)
            entry['role_name'] = role_labels.get(step_num)
            history.append(entry)

        return Response({
            'history': history
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='origins')
    def meeting_origins(self, request, pk=None):
        """Return immutable meeting/action-item lineage snapshots (SMP-489)."""
        task = self.get_object()
        return Response(
            {
                'origin_meeting_id': task.origin_meeting_id,
                'origin_meeting_title': task.origin_meeting_title,
                'origin_action_item_id': task.origin_action_item_id,
                'origin_action_item_title': task.origin_action_item_title,
            },
            status=status.HTTP_200_OK,
        )
    
    @action(detail=True, methods=['post'])
    def revise(self, request, pk=None):
        """Revise a task (change status to DRAFT)"""
        task = self.get_object()
        
        # Validate task can be revised
        revisable_statuses = [Task.Status.REJECTED, Task.Status.CANCELLED]
        if task.status not in revisable_statuses:
            return Response(
                {'error': 'Task must be in REJECTED or CANCELLED status to be revised'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Revise the task (just change status to DRAFT)
            task.revise()

            # Increment revision round so next submission is tracked as a new round
            task.revision_round += 1

            # Save the task to persist the state change
            task.save()

            # If this is a budget task, reset the BR back to DRAFT so it can flow through approval again
            if task.type == 'budget':
                try:
                    from budget_approval.models import BudgetRequest, BudgetRequestStatus
                    br = task.linked_object
                    if isinstance(br, BudgetRequest) and br.status in (
                        BudgetRequestStatus.CANCELLED, BudgetRequestStatus.REJECTED
                    ):
                        br.revise()
                        br.save()
                except Exception as e:
                    logger.error('Budget revise sync failed on task %s: %s', task.id, e, exc_info=True)
            
            # Return updated task
            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({
                'task': task_serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def forward(self, request, pk=None):
        """Forward a task to next approver (update current_approver)"""
        task = self.get_object()
        
        # Validate task can be forwarded
        forwardable_statuses = [Task.Status.APPROVED]
        if task.status not in forwardable_statuses:
            return Response(
                {'error': 'Task must be in APPROVED status to be forwarded'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate request data
        serializer = TaskForwardSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        next_approver_id = serializer.validated_data['next_approver_id']
        comment = serializer.validated_data.get('comment', '')
        
        try:
            # Get the new approver
            User = get_user_model()
            new_approver = User.objects.get(id=next_approver_id)
            
            # Forward the task
            task.forward_to_next()
            
            # Update current_approver
            task.current_approver = new_approver
            task.save()
            
            # Return updated task
            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({
                'task': task_serializer.data
            }, status=status.HTTP_200_OK)

        except User.DoesNotExist:
            return Response(
                {'error': 'User with this ID does not exist'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def submit_task(self, request, pk=None):
        """Submit a task (change status from DRAFT to SUBMITTED)"""
        task = self.get_object()
        if task.owner_id and task.owner_id != request.user.id:
            return Response({'error': 'Only the task owner can submit this task.'}, status=status.HTTP_403_FORBIDDEN)
        if task.status != Task.Status.DRAFT:
            return Response(
                {'error': 'Task must be in DRAFT status to submit'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            task.submit()
            task.save()

            # Sync BR submitted_at to match the task submission moment.
            # Budget details can be saved while the task is still DRAFT; that
            # may auto-submit the BudgetRequest earlier. When the user finally
            # submits the task, the visible "Submitted at" should reflect this
            # task transition, not the earlier detail-save time.
            if task.type == 'budget':
                try:
                    from budget_approval.models import BudgetRequest, BudgetRequestStatus
                    from budget_approval import notifications as budget_notifications
                    br = task.linked_object or task.budget_requests.first()
                    if isinstance(br, BudgetRequest):
                        if br.status == BudgetRequestStatus.DRAFT:
                            br.submit()
                            br.save()
                            budget_notifications.notify_budget_submitted(
                                br,
                                actor_id=request.user.id,
                            )
                        elif br.status == BudgetRequestStatus.SUBMITTED:
                            br.submitted_at = timezone.now()
                            br.save(update_fields=['submitted_at'])
                except Exception as e:
                    logger.error('Budget sync failed on task %s submit: %s', task.id, e, exc_info=True)

            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({'task': task_serializer.data}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def start_review(self, request, pk=None):
        """Start review for a task (change status to UNDER_REVIEW).

        If a predefined ApprovalChain exists for this task's project + type,
        it is automatically assigned and the first step's approver becomes
        current_approver. Otherwise the task falls back to legacy single-approver mode.
        """
        task = self.get_object()

        # Validate task can start review (status check first)
        if task.status != Task.Status.SUBMITTED:
            return Response(
                {'error': 'Task must be in SUBMITTED status to start review'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if task.current_approver_id and task.current_approver_id != request.user.id:
            return Response({'error': 'Only the task approver can start review.'}, status=status.HTTP_403_FORBIDDEN)


        try:
            # Transition status: SUBMITTED → UNDER_REVIEW
            task.start_review()

            # Auto-assign approval chain if one is configured and not already set
            if not task.approval_chain:
                chain = Task.find_approval_chain(task.project, task.type)
                if chain and chain.total_steps > 0:
                    first_step = chain.get_step(1)
                    if first_step:
                        task.approval_chain = chain
                        task.current_approval_step = 1
                        if not task.current_approver:
                            task.current_approver = first_step.approver

            task.save()

            # Return updated task
            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({
                'task': task_serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        """Lock a task (change status to LOCKED)"""
        task = self.get_object()

        # Validate task can be locked (status check first)
        lockable_statuses = [Task.Status.APPROVED]
        if task.status not in lockable_statuses:
            return Response(
                {'error': 'Task must be in APPROVED status to be locked'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if task.current_approver_id and task.current_approver_id != request.user.id:
            return Response({'error': 'Only the task approver can lock this task.'}, status=status.HTTP_403_FORBIDDEN)

        # Enforce minimum approval count when an approval chain is assigned
        if task.approval_chain:
            approved_count = task.approval_records.filter(is_approved=True).count()
            required = task.approval_chain.effective_required_approvals
            if approved_count < required:
                return Response(
                    {
                        'error': (
                            f'Task requires {required} approval(s) before it can be locked. '
                            f'{approved_count} of {required} approvals completed.'
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Budget tasks require an approved BudgetRequest before locking
        if task.type == 'budget':
            from budget_approval.models import BudgetRequest, BudgetRequestStatus
            br = task.linked_object or task.budget_requests.first()
            if not isinstance(br, BudgetRequest):
                return Response(
                    {'error': 'Budget details must be filled in before this task can be locked.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if br.status != BudgetRequestStatus.APPROVED:
                return Response(
                    {'error': f'Budget request must be in APPROVED status to lock this task (current: {br.status}).'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        try:
            # Lock the task
            task.lock()
            task.save()  # Save the state change

            # Sync budget request to LOCKED so the pool deduction happens here
            if task.type == 'budget':
                try:
                    from budget_approval.models import BudgetRequest, BudgetRequestStatus
                    from budget_approval.services import BudgetRequestService
                    br = task.linked_object or task.budget_requests.first()
                    if isinstance(br, BudgetRequest) and br.status == BudgetRequestStatus.APPROVED:
                        BudgetRequestService.lock_budget_request(
                            br,
                            actor_id=request.user.id,
                        )
                except Exception as e:
                    logger.error('Budget sync failed on task %s lock: %s', task.id, e, exc_info=True)

            # Return updated task
            task_serializer = TaskSerializer(task, context={'request': request})
            return Response({
                'task': task_serializer.data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'], url_path='unlock')
    def unlock(self, request, pk=None):
        task = self.get_object()

        if task.current_approver_id and task.current_approver_id != request.user.id:
            return Response({'error': 'Only the task approver can unlock this task.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            task.unlock()
            task.save()

            # Reverse the pool deduction: reset the linked BudgetRequest from LOCKED → APPROVED
            if task.type == 'budget':
                try:
                    from budget_approval.models import BudgetRequest, BudgetRequestStatus
                    from decimal import Decimal
                    br = task.linked_object or task.budget_requests.first()
                    if isinstance(br, BudgetRequest) and br.status == BudgetRequestStatus.LOCKED:
                        pool = br.budget_pool
                        pool.used_amount = max(Decimal('0'), pool.used_amount - br.amount)
                        pool.save()
                        BudgetRequest.objects.filter(pk=br.pk).update(status=BudgetRequestStatus.APPROVED)
                except Exception as e:
                    logger.error('Budget sync failed on task %s unlock: %s', task.id, e, exc_info=True)
            return Response(
                {'task': TaskSerializer(task).data},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get', 'post'])
    def subtasks(self, request, pk=None):
        """List subtasks or add a subtask to a parent task"""
        parent_task = self.get_object()
        
        if request.method == 'GET':
            # List all subtasks
            subtasks = parent_task.get_subtasks()
            serializer = TaskSerializer(subtasks, many=True, context={'request': request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        
        elif request.method == 'POST':
            # Add a subtask
            serializer = SubtaskAddSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            child_task_id = serializer.validated_data['child_task_id']
            child_task = get_object_or_404(Task, id=child_task_id)
            
            # Ensure user has access to child task's project
            has_membership = ProjectMember.objects.filter(
                user=request.user,
                project=child_task.project,
                is_active=True,
            ).exists()
            if not has_membership:
                raise PermissionDenied('You do not have access to this task.')
            
            try:
                add_subtask_to_parent(parent_task=parent_task, child_task=child_task)
                child_serializer = TaskSerializer(child_task, context={'request': request})
                return Response(child_serializer.data, status=status.HTTP_201_CREATED)
            except TaskHierarchyCycleError:
                return _hierarchy_cycle_response()
            except ValidationError as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
    
    @action(detail=True, methods=['delete'], url_path='subtasks/(?P<subtask_id>[^/.]+)')
    def subtask_detail(self, request, pk=None, subtask_id=None):
        """Remove a subtask relationship (unlink only — does not delete the task)."""
        parent_task = self.get_object()
        child_task = get_object_or_404(Task, **resolve_task_lookup_kwargs(subtask_id))
        qs = TaskHierarchy.objects.filter(parent_task=parent_task, child_task=child_task)
        if not qs.exists():
            return Response({'error': 'Subtask relationship not found.'}, status=status.HTTP_404_NOT_FOUND)
        # Clear is_subtask before deleting so the post_delete signal knows this
        # is a deliberate unlink (not a cascade) and leaves the task intact.
        has_other_parents = TaskHierarchy.objects.filter(child_task=child_task).exclude(parent_task=parent_task).exists()
        if not has_other_parents:
            child_task.is_subtask = False
            child_task.save(update_fields=['is_subtask'])
        qs.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=True, methods=['post'], url_path='subtasks/(?P<subtask_id>[^/.]+)/move')
    def move_subtask(self, request, pk=None, subtask_id=None):
        """
        Move subtask from old parent to new parent (pk).

        Request body: ``{ "old_parent_id": <id> }``

        Success (200): ``{ "success": true }``

        Hierarchy cycle (422) — stable contract for UI and integrations::

            {
                "detail": "Cannot set this parent: it would create a circular task hierarchy.",
                "code": "task_hierarchy_cycle"
            }

        Other validation errors return 400 with ``{ "error": "<message>" }``.
        Missing relationship returns 404.
        """
        new_parent = self.get_object()
        old_parent_id = request.data.get('old_parent_id')
        if not old_parent_id:
            return Response({'error': 'old_parent_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        old_parent = get_object_or_404(Task, pk=old_parent_id)
        child_task = get_object_or_404(Task, **resolve_task_lookup_kwargs(subtask_id))

        has_membership = ProjectMember.objects.filter(
            user=request.user,
            project=child_task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise PermissionDenied('You do not have access to this task.')

        try:
            reassign_subtask_parent(
                child_task=child_task,
                new_parent=new_parent,
                old_parent=old_parent,
            )
        except TaskHierarchyCycleError:
            return _hierarchy_cycle_response()
        except ValidationError as e:
            error_message = str(e)
            if error_message == 'Subtask relationship not found.':
                return Response({'error': error_message}, status=status.HTTP_404_NOT_FOUND)
            return Response({'error': error_message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'success': True}, status=status.HTTP_200_OK)
    
    @action(detail=True, methods=['get', 'post'])
    def relations(self, request, pk=None):
        """List relations or add a relation to a task"""
        task = self.get_object()
        
        if request.method == 'GET':
            # List all relations grouped by type, including relation_id
            
            # Helper function to build relation data
            def build_relation_data(relation, related_task):
                return {
                    'relation_id': relation.id,
                    'task': TaskSerializer(related_task, context={'request': request}).data
                }
            
            # Outgoing relations (causes, blocks, clones)
            causes_relations = task.outgoing_relationships.filter(relationship_type=TaskRelation.CAUSES)
            causes_data = [build_relation_data(rel, rel.target_task) for rel in causes_relations]
            
            blocks_relations = task.outgoing_relationships.filter(relationship_type=TaskRelation.BLOCKS)
            blocks_data = [build_relation_data(rel, rel.target_task) for rel in blocks_relations]
            
            clones_relations = task.outgoing_relationships.filter(relationship_type=TaskRelation.CLONES)
            clones_data = [build_relation_data(rel, rel.target_task) for rel in clones_relations]
            
            # Incoming relations (is_caused_by, is_blocked_by, is_cloned_by)
            is_caused_by_relations = task.incoming_relationships.filter(relationship_type=TaskRelation.CAUSES)
            is_caused_by_data = [build_relation_data(rel, rel.source_task) for rel in is_caused_by_relations]
            
            is_blocked_by_relations = task.incoming_relationships.filter(relationship_type=TaskRelation.BLOCKS)
            is_blocked_by_data = [build_relation_data(rel, rel.source_task) for rel in is_blocked_by_relations]
            
            is_cloned_by_relations = task.incoming_relationships.filter(relationship_type=TaskRelation.CLONES)
            is_cloned_by_data = [build_relation_data(rel, rel.source_task) for rel in is_cloned_by_relations]
            
            # Bidirectional relation (relates_to) - merge both directions and deduplicate
            relates_to_outgoing = task.outgoing_relationships.filter(relationship_type=TaskRelation.RELATES_TO)
            relates_to_incoming = task.incoming_relationships.filter(relationship_type=TaskRelation.RELATES_TO)
            
            # Combine and deduplicate by relation_id
            relates_to_dict = {}
            for rel in relates_to_outgoing:
                relates_to_dict[rel.id] = build_relation_data(rel, rel.target_task)
            for rel in relates_to_incoming:
                relates_to_dict[rel.id] = build_relation_data(rel, rel.source_task)
            relates_to_data = list(relates_to_dict.values())
            
            relations_data = {
                'causes': causes_data,
                'is_caused_by': is_caused_by_data,
                'blocks': blocks_data,
                'is_blocked_by': is_blocked_by_data,
                'clones': clones_data,
                'is_cloned_by': is_cloned_by_data,
                'relates_to': relates_to_data,
            }
            return Response(relations_data, status=status.HTTP_200_OK)
        
        elif request.method == 'POST':
            # Add a relation
            serializer = TaskRelationAddSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            target_task_id = serializer.validated_data['target_task_id']
            relationship_type = serializer.validated_data['relationship_type']
            target_task = get_object_or_404(Task, id=target_task_id)
            
            # Ensure user has access to target task's project
            has_membership = ProjectMember.objects.filter(
                user=request.user,
                project=target_task.project,
                is_active=True,
            ).exists()
            if not has_membership:
                raise PermissionDenied('You do not have access to this task.')
            
            try:
                task.add_relationship(target_task, relationship_type)
                return Response({
                    'message': f'Relation {relationship_type} added successfully',
                    'source_task_id': task.id,
                    'target_task_id': target_task_id,
                    'relationship_type': relationship_type
                }, status=status.HTTP_201_CREATED)
            except ValidationError as e:
                return Response(
                    {'error': str(e)},
                    status=status.HTTP_400_BAD_REQUEST
                )
    
    @action(detail=True, methods=['delete'], url_path='relations/(?P<relation_id>[^/.]+)')
    def relation_detail(self, request, pk=None, relation_id=None):
        """Delete a specific relation"""
        task = self.get_object()
        
        # Get the relation
        relation = get_object_or_404(TaskRelation, id=relation_id)
        
        # Ensure the relation involves the current task
        if relation.source_task_id != task.id and relation.target_task_id != task.id:
            return Response(
                {'error': 'This relation does not belong to this task'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Ensure user has access to the other task's project
        other_task_id = relation.target_task_id if relation.source_task_id == task.id else relation.source_task_id
        other_task = get_object_or_404(Task, id=other_task_id)
        has_membership = ProjectMember.objects.filter(
            user=request.user,
            project=other_task.project,
            is_active=True,
        ).exists()
        if not has_membership:
            raise PermissionDenied('You do not have access to this task.')
        
        # Delete the relation
        relation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _user_can_access_task(user, task):
    """Return True if user is an active project member or the current designated approver.

    Mirrors the same permission check used in TaskViewSet.get_object().
    """
    if ProjectMember.objects.filter(user=user, project=task.project, is_active=True).exists():
        return True
    return task.current_approver_id is not None and task.current_approver_id == user.id


class TaskCommentListView(generics.ListCreateAPIView):
    """
    List comments for a task or create a new task-level comment.
    Comments are attached directly to the Task, regardless of type.
    """
    serializer_class = TaskCommentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        task_id = self.kwargs.get('task_id')
        task = get_object_or_404(Task, **resolve_task_lookup_kwargs(task_id))

        if not _user_can_access_task(self.request.user, task):
            raise PermissionDenied('You do not have access to this task.')

        return TaskComment.objects.filter(task=task)

    def perform_create(self, serializer):
        import re  # noqa: PLC0415

        task_id = self.kwargs.get('task_id')
        task = get_object_or_404(Task, **resolve_task_lookup_kwargs(task_id))

        if not _user_can_access_task(self.request.user, task):
            raise PermissionDenied('You do not have access to comment on this task.')

        comment = serializer.save(task=task, user=self.request.user)

        # Parse @username mentions and notify each mentioned user once
        body = comment.body or ""
        mentioned_usernames = set(re.findall(r'@(\w+)', body))
        if mentioned_usernames:
            User = get_user_model()
            for username in mentioned_usernames:
                try:
                    mentioned_user = User.objects.get(username=username)
                except User.DoesNotExist:
                    continue
                if mentioned_user.id == self.request.user.id:
                    continue
                create_notification(
                    recipient_id=mentioned_user.id,
                    actor_id=self.request.user.id,
                    category=NotificationCategory.TASKS,
                    event_type=NotificationEventType.TASK_COMMENT_MENTION,
                    title=f"You were mentioned in a comment on: {task.summary}",
                    body=body[:200] + ("…" if len(body) > 200 else ""),
                    related_object_type="task",
                    related_object_id=str(task.id),
                    action_url=task_action_url(task.slug),
                    metadata={
                        "task_id": task.id,
                        "project_id": task.project_id,
                        "comment_id": comment.id,
                        "comment_excerpt": body[:300],
                        "change_type": "comment_mention",
                    },
                )


class TaskAttachmentListView(generics.ListCreateAPIView):
    """
    List attachments for a task or create a new task attachment.
    Attachments are attached directly to the Task, regardless of type.
    """
    serializer_class = TaskAttachmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        task_id = self.kwargs.get('task_id')
        task = get_object_or_404(Task, **resolve_task_lookup_kwargs(task_id))

        if not _user_can_access_task(self.request.user, task):
            raise PermissionDenied('You do not have access to this task.')

        return TaskAttachment.objects.filter(task=task)

    def perform_create(self, serializer):
        task_id = self.kwargs.get('task_id')
        task = get_object_or_404(Task, **resolve_task_lookup_kwargs(task_id))

        if not _user_can_access_task(self.request.user, task):
            raise PermissionDenied('You do not have access to upload attachments to this task.')

        set_current_user(self.request.user)
        serializer.save(task=task, uploaded_by=self.request.user)


class TaskAttachmentDetailView(generics.RetrieveDestroyAPIView):
    """
    Retrieve or delete a specific task attachment.
    """
    serializer_class = TaskAttachmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        task_id = self.kwargs.get('task_id')
        task = get_object_or_404(Task, **resolve_task_lookup_kwargs(task_id))

        if not _user_can_access_task(self.request.user, task):
            raise PermissionDenied('You do not have access to this task.')

        return TaskAttachment.objects.filter(task=task)

    def perform_destroy(self, instance):
        task_id = instance.task_id
        set_current_user(self.request.user)
        instance.delete()

    def get_object(self):
        # Use get_queryset() to ensure permission checks are applied
        queryset = self.get_queryset()
        attachment_id = self.kwargs.get('pk')
        return get_object_or_404(queryset, pk=attachment_id)


class TaskAttachmentDownloadView(APIView):
    """Download a specific task attachment"""
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get']
    
    def get(self, request, *args, **kwargs):
        task_id = self.kwargs.get('task_id')
        attachment_id = self.kwargs.get('pk')
        
        # Get the specific attachment
        task = get_object_or_404(Task, **resolve_task_lookup_kwargs(task_id))
        attachment = get_object_or_404(TaskAttachment, pk=attachment_id, task=task)
        
        if not _user_can_access_task(request.user, attachment.task):
            raise PermissionDenied('You do not have access to this task.')
        
        # Check if the attachment has a file
        if not attachment.file:
            return Response(
                {'detail': 'No file available for download.'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Return download URL
        download_data = {
            'task_id': attachment.task.id,
            'task_summary': attachment.task.summary,
            'attachment_id': attachment.id,
            'file_name': attachment.original_filename,
            'file_size': attachment.file_size,
            'content_type': attachment.content_type,
            'checksum': attachment.checksum,
            'scan_status': attachment.scan_status,
            'uploaded_at': attachment.created_at,
            'uploaded_by': attachment.uploaded_by.username,
            'download_url': request.build_absolute_uri(attachment.file.url)
        }
        
        return Response(download_data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_task_types(request):
    """
    Get available task types with their labels.
    Returns all task types defined in the Task model.
    """
    # Get task type choices from the Task model
    task_type_choices = Task._meta.get_field('type').choices
    
    # Format as a list of objects with value and label
    # Include all model types so meeting → task conversion and task board stay in sync.
    task_types = [
        {'value': choice[0], 'label': choice[1]}
        for choice in task_type_choices
    ]
    
    return Response({'task_types': task_types}, status=status.HTTP_200_OK)


_AUTOSAVE_TTL = 604800  # 7 days
_AUTOSAVE_MAX_BYTES = 16 * 1024  # 16 KB
_VALID_TASK_TYPES = frozenset(
    choice[0] for choice in Task._meta.get_field('type').choices
)


class TaskFormAutosaveView(APIView):
    """
    Cache-backed form autosave for the task creation form.

    All data is scoped to request.user — a user can only read and write
    their own drafts.  Cache key: task_form_autosave:{user_id}:{task_type}
    """
    permission_classes = [IsAuthenticated]

    def _cache_key(self, user_id: int, task_type: str) -> str:
        return f'task_form_autosave:{user_id}:{task_type}'

    def _validated_type(self, request) -> tuple[str | None, Response | None]:
        task_type = request.query_params.get('type', '').strip()
        if not task_type:
            return None, Response(
                {'error': '`type` query parameter is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if task_type not in _VALID_TASK_TYPES:
            return None, Response(
                {'error': f'Invalid task type: {task_type!r}. '
                          f'Must be one of: {sorted(_VALID_TASK_TYPES)}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return task_type, None

    def get(self, request):
        task_type, err = self._validated_type(request)
        if err:
            return err
        key = self._cache_key(request.user.id, task_type)
        payload = cache.get(key)
        if payload is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(payload, status=status.HTTP_200_OK)

    def put(self, request):
        task_type, err = self._validated_type(request)
        if err:
            return err
        raw = request.body
        if len(raw) > _AUTOSAVE_MAX_BYTES:
            return Response(
                {'error': f'Payload too large. Maximum size is {_AUTOSAVE_MAX_BYTES} bytes.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(request.data, dict):
            return Response(
                {'error': 'Request body must be a JSON object.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        key = self._cache_key(request.user.id, task_type)
        cache.set(key, request.data, timeout=_AUTOSAVE_TTL)
        return Response(request.data, status=status.HTTP_200_OK)

    def delete(self, request):
        task_type, err = self._validated_type(request)
        if err:
            return err
        key = self._cache_key(request.user.id, task_type)
        cache.delete(key)
        return Response(status=status.HTTP_204_NO_CONTENT)
