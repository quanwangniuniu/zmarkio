from django.db import transaction

from task.models import Task

from .models import TaskStatusConfig, TaskTypeConfig


def get_user_organization(user):
    return getattr(user, 'current_organization', None) or getattr(user, 'organization', None)


def _rows_by_key(model, organization):
    if organization is None:
        return {}
    return {row.key: row for row in model.objects.filter(organization=organization)}


def get_task_config(organization):
    type_rows = _rows_by_key(TaskTypeConfig, organization)
    status_rows = _rows_by_key(TaskStatusConfig, organization)

    task_types = []
    for index, (key, default_label) in enumerate(Task._meta.get_field('type').choices):
        row = type_rows.get(key)
        task_types.append({
            'key': key,
            'default_label': default_label,
            'label': row.label if row else None,
            'enabled': row.enabled if row else True,
            'order': row.order if row else index,
        })

    task_statuses = []
    for index, (key, default_label) in enumerate(Task.Status.choices):
        row = status_rows.get(key)
        task_statuses.append({
            'key': key,
            'default_label': default_label,
            'label': row.label if row else None,
            'color': row.color if row else None,
            'order': row.order if row else index,
        })

    task_types.sort(key=lambda item: item['order'])
    task_statuses.sort(key=lambda item: item['order'])
    return {'task_types': task_types, 'task_statuses': task_statuses}


def get_enabled_task_types(organization):
    return [
        {'value': item['key'], 'label': item['label'] or item['default_label']}
        for item in get_task_config(organization)['task_types']
        if item['enabled']
    ]


@transaction.atomic
def save_task_config(organization, data):
    for item in data.get('task_types', []):
        TaskTypeConfig.objects.update_or_create(
            organization=organization,
            key=item['key'],
            defaults={
                'label': item.get('label') or None,
                'enabled': item.get('enabled', True),
                'order': item.get('order', 0),
            },
        )
    for item in data.get('task_statuses', []):
        TaskStatusConfig.objects.update_or_create(
            organization=organization,
            key=item['key'],
            defaults={
                'label': item.get('label') or None,
                'color': item.get('color') or None,
                'order': item.get('order', 0),
            },
        )
