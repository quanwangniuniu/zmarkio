from django.db.models import Count, Exists, OuterRef
from django.utils import timezone
from datetime import timedelta

from task.models import Task, TaskRelation
from campaign.models import Campaign
from decision.models import Decision
from meetings.models import Meeting
from spreadsheet.models import Spreadsheet
from core.models import Project


def _batch_count(qs):
    """Run a GROUP BY project_id COUNT query, return dict {project_id: count}."""
    rows = qs.values('project_id').annotate(count=Count('id'))
    return {row['project_id']: row['count'] for row in rows}


def _query_task_total(project_ids):
    return _batch_count(Task.objects.filter(project_id__in=project_ids))


def _query_task_completed_7d(project_ids):
    cutoff = timezone.now() - timedelta(days=7)
    # NOTE: Task has no completed_at field; updated_at is used as a proxy.
    # This means a task edited after completion will be recounted. Known limitation.
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        status__in=[Task.Status.APPROVED, Task.Status.LOCKED],
        updated_at__gte=cutoff,
    ))


def _query_task_created_7d(project_ids):
    cutoff = timezone.now() - timedelta(days=7)
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        created_at__gte=cutoff,
    ))


def _query_task_due_soon(project_ids):
    now = timezone.now()
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        due_date__gte=now.date(),
        due_date__lte=(now + timedelta(days=7)).date(),
    ).exclude(status__in=[Task.Status.APPROVED, Task.Status.LOCKED, Task.Status.CANCELLED]))


def _query_campaign_active(project_ids):
    # Active = not paused, completed, or archived
    inactive = [Campaign.Status.PAUSED, Campaign.Status.COMPLETED, Campaign.Status.ARCHIVED]
    return _batch_count(Campaign.objects.filter(
        project_id__in=project_ids,
    ).exclude(status__in=inactive))


def _query_decision_pending(project_ids):
    return _batch_count(Decision.objects.filter(
        project_id__in=project_ids,
        is_deleted=False,
        status=Decision.Status.AWAITING_APPROVAL,
    ))


def _query_decision_total(project_ids):
    return _batch_count(Decision.objects.filter(
        project_id__in=project_ids,
        is_deleted=False,
    ))


def _query_meeting_upcoming(project_ids):
    today = timezone.now().date()
    return _batch_count(Meeting.objects.filter(
        project_id__in=project_ids,
        is_archived=False,
        is_deleted=False,
        scheduled_date__gte=today,
    ))


def _query_spreadsheet_total(project_ids):
    return _batch_count(Spreadsheet.objects.filter(
        project_id__in=project_ids,
        is_deleted=False,
    ))


def _query_task_done(project_ids):
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        status__in=[Task.Status.APPROVED, Task.Status.LOCKED],
    ))


def _query_task_overdue(project_ids):
    cutoff = timezone.localdate()
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        due_date__lt=cutoff,
    ).exclude(status__in=[Task.Status.APPROVED, Task.Status.LOCKED, Task.Status.CANCELLED]))


def _query_task_blocked(project_ids):
    blocked_subq = TaskRelation.objects.filter(
        target_task=OuterRef('pk'),
        relationship_type=TaskRelation.BLOCKS,
    )
    return _batch_count(
        Task.objects.filter(project_id__in=project_ids)
        .exclude(status__in=[Task.Status.APPROVED, Task.Status.LOCKED, Task.Status.CANCELLED])
        .annotate(is_blocked=Exists(blocked_subq))
        .filter(is_blocked=True)
    )


def _query_task_under_review(project_ids):
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        status=Task.Status.UNDER_REVIEW,
    ))


def _query_task_rejected(project_ids):
    return _batch_count(Task.objects.filter(
        project_id__in=project_ids,
        status=Task.Status.REJECTED,
    ))


def _query_decision_high_risk(project_ids):
    return _batch_count(Decision.objects.filter(
        project_id__in=project_ids,
        is_deleted=False,
        risk_level='HIGH',
    ))


FIELD_REGISTRY = {
    # Tasks — primary metrics (used for summary cards)
    'task_total':             {'label': 'Total Tasks',             'group': 'Tasks',        'fn': _query_task_total},
    'task_done':              {'label': 'Completed Tasks',         'group': 'Tasks',        'fn': _query_task_done},
    'task_overdue':           {'label': 'Overdue Tasks',           'group': 'Tasks',        'fn': _query_task_overdue},
    'task_blocked':           {'label': 'Blocked Tasks',           'group': 'Tasks',        'fn': _query_task_blocked},
    # Tasks — detail metrics
    'task_under_review':      {'label': 'Under Review',            'group': 'Tasks',        'fn': _query_task_under_review},
    'task_rejected':          {'label': 'Rejected',                'group': 'Tasks',        'fn': _query_task_rejected},
    'task_due_soon':          {'label': 'Due Soon (7d)',           'group': 'Tasks',        'fn': _query_task_due_soon},
    'task_created_7d':        {'label': 'Created (last 7d)',       'group': 'Tasks',        'fn': _query_task_created_7d},
    'task_completed_7d':      {'label': 'Completed (last 7d)',     'group': 'Tasks',        'fn': _query_task_completed_7d},
    # Decisions
    'decision_total':         {'label': 'Active Decisions',        'group': 'Decisions',    'fn': _query_decision_total},
    'decision_pending':       {'label': 'Awaiting Approval',       'group': 'Decisions',    'fn': _query_decision_pending},
    'decision_high_risk':     {'label': 'High Risk',               'group': 'Decisions',    'fn': _query_decision_high_risk},
    # Operations
    'spreadsheet_total':      {'label': 'Active Spreadsheets',     'group': 'Operations',   'fn': _query_spreadsheet_total},
    'campaign_active':        {'label': 'Active Campaigns',        'group': 'Campaigns',    'fn': _query_campaign_active},
    'meeting_upcoming':       {'label': 'Upcoming Meetings',       'group': 'Meetings',     'fn': _query_meeting_upcoming},
}


def get_available_fields() -> list[dict]:
    """Return all available field definitions for the frontend."""
    return [
        {'key': key, 'label': meta['label'], 'group': meta['group']}
        for key, meta in FIELD_REGISTRY.items()
    ]


def get_rollup(project_ids: list[int], fields: list[str]) -> dict:
    """
    Query requested fields for all given project_ids in batch.
    Returns:
        {
            'results': [ {project_id, project_name, field_key: value, ...}, ... ],
            'errors':  { field_key: error_message }  — only present when a field query fails
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

    # One batch query per field regardless of number of projects.
    # If a single field query fails, record the error and continue with the rest.
    field_data: dict[str, dict[int, int]] = {}
    errors: dict[str, str] = {}
    for field_key in requested:
        try:
            field_data[field_key] = FIELD_REGISTRY[field_key]['fn'](project_ids)
        except Exception as e:
            field_data[field_key] = {}
            errors[field_key] = str(e)

    result = []
    for pid in project_ids:
        if pid not in projects:
            continue
        entry = {
            'project_id': pid,
            'project_name': projects[pid],
        }
        for field_key in requested:
            entry[field_key] = field_data[field_key].get(pid, 0)
        result.append(entry)

    return {'results': result, 'errors': errors}
