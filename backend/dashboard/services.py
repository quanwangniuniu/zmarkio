from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone
from datetime import timedelta

from task.models import Task, TaskRelation
from campaign.models import Campaign
from decision.models import Decision
from meetings.models import Meeting
from spreadsheet.models import Spreadsheet
from core.models import Project

# ---------------------------------------------------------------------------
# Grouped batch queries — one SQL per table instead of one per field.
# Each function returns a dict keyed by field name:
#   { field_key: { project_id: count, ... }, ... }
# ---------------------------------------------------------------------------

_TASK_FIELDS = [
    'task_total', 'task_done', 'task_overdue', 'task_blocked',
    'task_under_review', 'task_rejected', 'task_due_soon',
    'task_created_7d', 'task_completed_7d',
]

_DECISION_FIELDS = ['decision_total', 'decision_pending', 'decision_high_risk']

_SINGLE_FIELDS = ['campaign_active', 'meeting_upcoming', 'spreadsheet_total']


def _query_task_group(project_ids):
    """All task metrics in one GROUP BY query using conditional COUNT."""
    today = timezone.localdate()
    cutoff_7d = timezone.now() - timedelta(days=7)
    due_soon_end = today + timedelta(days=7)
    terminal = [Task.Status.APPROVED, Task.Status.LOCKED, Task.Status.CANCELLED]
    done = [Task.Status.APPROVED, Task.Status.LOCKED]

    blocked_subq = TaskRelation.objects.filter(
        target_task=OuterRef('pk'),
        relationship_type=TaskRelation.BLOCKS,
    )

    rows = (
        Task.objects
        .filter(project_id__in=project_ids)
        .annotate(is_blocked=Exists(blocked_subq))
        .values('project_id')
        .annotate(
            task_total=Count('id'),
            task_done=Count('id', filter=Q(status__in=done)),
            task_overdue=Count('id', filter=Q(due_date__lt=today) & ~Q(status__in=terminal)),
            task_blocked=Count('id', filter=Q(is_blocked=True) & ~Q(status__in=terminal)),
            task_under_review=Count('id', filter=Q(status=Task.Status.UNDER_REVIEW)),
            task_rejected=Count('id', filter=Q(status=Task.Status.REJECTED)),
            task_due_soon=Count('id', filter=Q(due_date__gte=today, due_date__lte=due_soon_end) & ~Q(status__in=terminal)),
            task_created_7d=Count('id', filter=Q(created_at__gte=cutoff_7d)),
            # NOTE: updated_at used as completion-date proxy; tasks re-edited after
            # approval will be recounted. Known limitation — no completed_at field.
            task_completed_7d=Count('id', filter=Q(status__in=done, updated_at__gte=cutoff_7d)),
        )
    )

    result = {key: {} for key in _TASK_FIELDS}
    for row in rows:
        pid = row['project_id']
        for key in _TASK_FIELDS:
            result[key][pid] = row[key]
    return result


def _query_decision_group(project_ids):
    """All decision metrics in one GROUP BY query using conditional COUNT."""
    rows = (
        Decision.objects
        .filter(project_id__in=project_ids, is_deleted=False)
        .values('project_id')
        .annotate(
            decision_total=Count('id'),
            decision_pending=Count('id', filter=Q(status=Decision.Status.AWAITING_APPROVAL)),
            decision_high_risk=Count('id', filter=Q(risk_level='HIGH')),
        )
    )

    result = {key: {} for key in _DECISION_FIELDS}
    for row in rows:
        pid = row['project_id']
        for key in _DECISION_FIELDS:
            result[key][pid] = row[key]
    return result


def _query_campaign_active(project_ids):
    inactive = [Campaign.Status.PAUSED, Campaign.Status.COMPLETED, Campaign.Status.ARCHIVED]
    rows = (
        Campaign.objects
        .filter(project_id__in=project_ids)
        .exclude(status__in=inactive)
        .values('project_id')
        .annotate(count=Count('id'))
    )
    return {'campaign_active': {row['project_id']: row['count'] for row in rows}}


def _query_meeting_upcoming(project_ids):
    today = timezone.now().date()
    rows = (
        Meeting.objects
        .filter(project_id__in=project_ids, is_archived=False, is_deleted=False, scheduled_date__gte=today)
        .values('project_id')
        .annotate(count=Count('id'))
    )
    return {'meeting_upcoming': {row['project_id']: row['count'] for row in rows}}


def _query_spreadsheet_total(project_ids):
    rows = (
        Spreadsheet.objects
        .filter(project_id__in=project_ids, is_deleted=False)
        .values('project_id')
        .annotate(count=Count('id'))
    )
    return {'spreadsheet_total': {row['project_id']: row['count'] for row in rows}}


# ---------------------------------------------------------------------------
# FIELD_REGISTRY — maps each field key to its label/group for the API.
# 'group_fn' is the grouped query function; 'fn' is kept for single-field
# fallback (used only when a field is requested in isolation during testing).
# ---------------------------------------------------------------------------

FIELD_REGISTRY = {
    # Tasks — primary metrics
    'task_total':        {'label': 'Total Tasks',         'group': 'Tasks',      'group_fn': _query_task_group},
    'task_done':         {'label': 'Completed Tasks',     'group': 'Tasks',      'group_fn': _query_task_group},
    'task_overdue':      {'label': 'Overdue Tasks',       'group': 'Tasks',      'group_fn': _query_task_group},
    'task_blocked':      {'label': 'Blocked Tasks',       'group': 'Tasks',      'group_fn': _query_task_group},
    # Tasks — detail metrics
    'task_under_review': {'label': 'Under Review',        'group': 'Tasks',      'group_fn': _query_task_group},
    'task_rejected':     {'label': 'Rejected',            'group': 'Tasks',      'group_fn': _query_task_group},
    'task_due_soon':     {'label': 'Due Soon (7d)',       'group': 'Tasks',      'group_fn': _query_task_group},
    'task_created_7d':   {'label': 'Created (last 7d)',   'group': 'Tasks',      'group_fn': _query_task_group},
    'task_completed_7d': {'label': 'Completed (last 7d)', 'group': 'Tasks',      'group_fn': _query_task_group},
    # Decisions
    'decision_total':    {'label': 'Active Decisions',    'group': 'Decisions',  'group_fn': _query_decision_group},
    'decision_pending':  {'label': 'Awaiting Approval',   'group': 'Decisions',  'group_fn': _query_decision_group},
    'decision_high_risk':{'label': 'High Risk',           'group': 'Decisions',  'group_fn': _query_decision_group},
    # Operations
    'spreadsheet_total': {'label': 'Active Spreadsheets', 'group': 'Operations', 'group_fn': _query_spreadsheet_total},
    'campaign_active':   {'label': 'Active Campaigns',    'group': 'Campaigns',  'group_fn': _query_campaign_active},
    'meeting_upcoming':  {'label': 'Upcoming Meetings',   'group': 'Meetings',   'group_fn': _query_meeting_upcoming},
}


def get_available_fields() -> list[dict]:
    """Return all available field definitions for the frontend."""
    return [
        {'key': key, 'label': meta['label'], 'group': meta['group']}
        for key, meta in FIELD_REGISTRY.items()
    ]


def get_rollup(project_ids: list[int], fields: list[str]) -> dict:
    """
    Query requested fields for all given project_ids.

    Groups requested fields by their shared query function so each distinct
    table is hit only once (one SQL per table, not one per field).

    Returns:
        {
            'results': [ {project_id, project_name, field_key: value, ...}, ... ],
            'errors':  { field_key: error_message }  — only present when a group query fails
        }
    """
    if not project_ids:
        return {'results': [], 'errors': {}}

    requested = [f for f in fields if f in FIELD_REGISTRY]
    if not requested:
        return {'results': [], 'errors': {}}

    projects = {
        p['id']: p['name']
        for p in Project.objects.filter(id__in=project_ids).values('id', 'name')
    }

    # Deduplicate group functions: each unique group_fn runs exactly once,
    # even when multiple fields share it (e.g. all task_* fields).
    seen_fns: set = set()
    field_data: dict[str, dict[int, int]] = {}
    errors: dict[str, str] = {}

    for field_key in requested:
        group_fn = FIELD_REGISTRY[field_key]['group_fn']
        fn_id = id(group_fn)
        if fn_id not in seen_fns:
            seen_fns.add(fn_id)
            try:
                group_result = group_fn(project_ids)
                field_data.update(group_result)
            except Exception as e:
                # Mark all fields produced by this function as errored.
                for fk in requested:
                    if id(FIELD_REGISTRY[fk]['group_fn']) == fn_id:
                        field_data.setdefault(fk, {})
                        errors[fk] = str(e)

    result = []
    for pid in project_ids:
        if pid not in projects:
            continue
        entry = {
            'project_id': pid,
            'project_name': projects[pid],
        }
        for field_key in requested:
            entry[field_key] = field_data.get(field_key, {}).get(pid, 0)
        result.append(entry)

    return {'results': result, 'errors': errors}
