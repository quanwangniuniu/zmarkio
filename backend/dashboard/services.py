from django.db.models import Count
from django.utils import timezone
from datetime import timedelta

from task.models import Task
from campaign.models import Campaign
from decision.models import Decision
from budget_approval.models import BudgetRequest, BudgetRequestStatus
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


def _query_campaign_total(project_ids):
    return _batch_count(Campaign.objects.filter(project_id__in=project_ids))


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


def _query_budget_request_pending(project_ids):
    # BudgetRequest has no direct project_id; traverse budget_pool → project
    rows = (
        BudgetRequest.objects
        .filter(
            budget_pool__project_id__in=project_ids,
            status__in=[BudgetRequestStatus.SUBMITTED, BudgetRequestStatus.UNDER_REVIEW],
        )
        .values('budget_pool__project_id')
        .annotate(count=Count('id'))
    )
    return {row['budget_pool__project_id']: row['count'] for row in rows}


def _query_meeting_upcoming(project_ids):
    today = timezone.now().date()
    return _batch_count(Meeting.objects.filter(
        project_id__in=project_ids,
        is_archived=False,
        scheduled_date__gte=today,
    ))


def _query_spreadsheet_total(project_ids):
    return _batch_count(Spreadsheet.objects.filter(
        project_id__in=project_ids,
        is_deleted=False,
    ))


FIELD_REGISTRY = {
    'task_total':             {'label': 'Total Tasks',             'group': 'Tasks',        'fn': _query_task_total},
    'task_completed_7d':      {'label': 'Completed (last 7d)',     'group': 'Tasks',        'fn': _query_task_completed_7d},
    'task_created_7d':        {'label': 'Created (last 7d)',       'group': 'Tasks',        'fn': _query_task_created_7d},
    'task_due_soon':          {'label': 'Due Soon',                'group': 'Tasks',        'fn': _query_task_due_soon},
    'campaign_active':        {'label': 'Active Campaigns',        'group': 'Campaigns',    'fn': _query_campaign_active},
    'campaign_total':         {'label': 'Total Campaigns',         'group': 'Campaigns',    'fn': _query_campaign_total},
    'decision_pending':       {'label': 'Pending Decisions',       'group': 'Decisions',    'fn': _query_decision_pending},
    'decision_total':         {'label': 'Total Decisions',         'group': 'Decisions',    'fn': _query_decision_total},
    'budget_request_pending': {'label': 'Pending Budget Requests', 'group': 'Budget',       'fn': _query_budget_request_pending},
    'meeting_upcoming':       {'label': 'Upcoming Meetings',       'group': 'Meetings',     'fn': _query_meeting_upcoming},
    'spreadsheet_total':      {'label': 'Total Spreadsheets',      'group': 'Spreadsheets', 'fn': _query_spreadsheet_total},
}


def get_available_fields() -> list[dict]:
    """Return all available field definitions for the frontend."""
    return [
        {'key': key, 'label': meta['label'], 'group': meta['group']}
        for key, meta in FIELD_REGISTRY.items()
    ]


def get_rollup(project_ids: list[int], fields: list[str]) -> list[dict]:
    """
    Query requested fields for all given project_ids in batch.
    Returns one entry per project with only the requested fields.
    """
    if not project_ids:
        return []

    requested = [f for f in fields if f in FIELD_REGISTRY]
    if not requested:
        return []

    projects = {
        p['id']: p['name']
        for p in Project.objects.filter(id__in=project_ids).values('id', 'name')
    }

    # One batch query per field regardless of number of projects
    field_data: dict[str, dict[int, int]] = {}
    for field_key in requested:
        field_data[field_key] = FIELD_REGISTRY[field_key]['fn'](project_ids)

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

    return result
