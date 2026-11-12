from django.db import transaction

from task.models import Task

from .models import TaskFieldOptionConfig, TaskPriorityConfig, TaskStatusConfig, TaskTypeConfig

STATUS_CATEGORIES = {
    'TO_DO': ('To Do', '#94A3B8'),
    'IN_PROGRESS': ('In Progress', '#3B82F6'),
    'DONE': ('Done', '#10B981'),
    'RESEARCH': ('Research', '#8B5CF6'),
    'CANCELLED': ('Cancelled', '#6B7280'),
}

DEFAULT_STATUS_CATEGORIES = {
    Task.Status.DRAFT: 'TO_DO',
    Task.Status.SUBMITTED: 'TO_DO',
    Task.Status.UNDER_REVIEW: 'IN_PROGRESS',
    Task.Status.APPROVED: 'DONE',
    Task.Status.LOCKED: 'DONE',
    Task.Status.REJECTED: 'RESEARCH',
    Task.Status.CANCELLED: 'CANCELLED',
}


def get_user_organization(user):
    return getattr(user, 'current_organization', None) or getattr(user, 'organization', None)


def _rows_by_key(model, organization):
    if organization is None:
        return {}
    return {row.key: row for row in model.objects.filter(organization=organization)}


def get_task_config(organization):
    type_rows = _rows_by_key(TaskTypeConfig, organization)
    status_rows = _rows_by_key(TaskStatusConfig, organization)
    priority_rows = _rows_by_key(TaskPriorityConfig, organization)

    task_types = []
    for index, (key, default_label) in enumerate(Task._meta.get_field('type').choices):
        row = type_rows.get(key)
        task_types.append({
            'key': key,
            'default_label': default_label,
            'label': row.label if row else None,
            'short_label': row.short_label if row else None,
            'color': row.color if row else None,
            'enabled': row.enabled if row else True,
            'order': row.order if row else index,
        })

    task_statuses = []
    for key, default_label in Task.Status.choices:
        row = status_rows.get(key)
        task_statuses.append({
            'key': key,
            'default_label': default_label,
            'label': row.label if row else None,
            'default_category': DEFAULT_STATUS_CATEGORIES[key],
            'category': row.category if row else None,
        })

    task_priorities = []
    for key, default_label in Task.Priority.choices:
        row = priority_rows.get(key)
        task_priorities.append({
            'key': key,
            'default_label': default_label,
            'label': row.label if row else None,
        })

    task_field_options = []
    if organization is not None:
        task_field_options = list(
            TaskFieldOptionConfig.objects.filter(organization=organization)
            .order_by('task_type', 'field', 'order')
            .values('task_type', 'field', 'value', 'label', 'enabled', 'order')
        )

    task_types.sort(key=lambda item: item['order'])
    return {
        'task_types': task_types,
        'task_statuses': task_statuses,
        'task_priorities': task_priorities,
        'task_field_options': task_field_options,
        'status_categories': [{'key': key, 'label': label} for key, (label, _) in STATUS_CATEGORIES.items()],
    }


def get_task_status_categories(organization):
    rows = _rows_by_key(TaskStatusConfig, organization)
    return {
        key: (rows[key].category if key in rows and rows[key].category else default)
        for key, default in DEFAULT_STATUS_CATEGORIES.items()
    }


def is_task_type_enabled(organization, key):
    if organization is None:
        return True
    return not TaskTypeConfig.objects.filter(organization=organization, key=key, enabled=False).exists()


def get_enabled_task_types(organization):
    return [
        {'value': item['key'], 'label': item['label'] or item['default_label']}
        for item in get_task_config(organization)['task_types']
        if item['enabled']
    ]


def _save_rows(model, organization, items, lookup_fields):
    for item in items:
        model.objects.update_or_create(
            organization=organization,
            **{field: item[field] for field in lookup_fields},
            defaults={
                field: (None if value == '' else value)
                for field, value in item.items()
                if field not in lookup_fields
            },
        )


@transaction.atomic
def save_task_config(organization, data):
    _save_rows(TaskTypeConfig, organization, data.get('task_types', []), ['key'])
    _save_rows(TaskStatusConfig, organization, data.get('task_statuses', []), ['key'])
    _save_rows(TaskPriorityConfig, organization, data.get('task_priorities', []), ['key'])
    _save_rows(
        TaskFieldOptionConfig,
        organization,
        data.get('task_field_options', []),
        ['task_type', 'field', 'value'],
    )
