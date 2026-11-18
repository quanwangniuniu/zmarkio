from dataclasses import dataclass
from operator import attrgetter

from django.db import transaction
from rest_framework import serializers

from csm.models import Ticket
from decision.models import Decision, Review, Signal
from task.models import Task

from .models import OptionConfig, TaskFieldOptionConfig, TaskPriorityConfig, TaskStatusConfig, TaskTypeConfig

OPTION_ATTRIBUTES = ('label', 'color', 'enabled', 'order')
LIST_ATTRIBUTES = ('label', 'enabled', 'order')
LOOKUP_ATTRIBUTES = ('label', 'color')


@dataclass(frozen=True)
class OptionField:
    model: type
    field: str
    project: str
    editable: tuple = OPTION_ATTRIBUTES

    @property
    def model_field(self):
        return self.model._meta.get_field(self.field)


MODULE_OPTION_FIELDS = {
    'csm': {
        'ticket_priority': OptionField(Ticket, 'priority', 'queue.project'),
    },
    'decisions': {
        'signal_metric': OptionField(Signal, 'metric', 'decision.project', LIST_ATTRIBUTES),
        'signal_movement': OptionField(Signal, 'movement', 'decision.project', LIST_ATTRIBUTES),
        'signal_period': OptionField(Signal, 'period', 'decision.project', LIST_ATTRIBUTES),
        'review_quality': OptionField(Review, 'quality', 'decision.project', LOOKUP_ATTRIBUTES),
        'risk_level': OptionField(Decision, 'risk_level', 'project', LOOKUP_ATTRIBUTES),
    },
}

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


def _project_rows(model, project):
    if project is None or project.organization_id is None:
        return model.objects.none()
    return model.objects.filter(organization_id=project.organization_id, project_id=project.id)


def _rows_by_key(model, project):
    return {row.key: row for row in _project_rows(model, project)}


def get_task_config(project):
    type_rows = _rows_by_key(TaskTypeConfig, project)
    status_rows = _rows_by_key(TaskStatusConfig, project)
    priority_rows = _rows_by_key(TaskPriorityConfig, project)

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

    task_field_options = list(
        _project_rows(TaskFieldOptionConfig, project)
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


def seed_task_config(project):
    if project.organization_id is None:
        return
    scope = {'organization_id': project.organization_id, 'project_id': project.id}
    TaskTypeConfig.objects.bulk_create([
        TaskTypeConfig(**scope, key=key, label=label, order=index)
        for index, (key, label) in enumerate(Task._meta.get_field('type').choices)
    ], ignore_conflicts=True)
    TaskStatusConfig.objects.bulk_create([
        TaskStatusConfig(**scope, key=key, label=label, category=DEFAULT_STATUS_CATEGORIES[key])
        for key, label in Task.Status.choices
    ], ignore_conflicts=True)
    TaskPriorityConfig.objects.bulk_create([
        TaskPriorityConfig(**scope, key=key, label=label) for key, label in Task.Priority.choices
    ], ignore_conflicts=True)


def get_task_status_categories(project):
    rows = _rows_by_key(TaskStatusConfig, project)
    return {
        key: (rows[key].category if key in rows and rows[key].category else default)
        for key, default in DEFAULT_STATUS_CATEGORIES.items()
    }


def is_task_type_enabled(project, key):
    return not _project_rows(TaskTypeConfig, project).filter(key=key, enabled=False).exists()


def get_enabled_task_types(project):
    return [
        {'value': item['key'], 'label': item['label'] or item['default_label']}
        for item in get_task_config(project)['task_types']
        if item['enabled']
    ]


def _save_rows(model, project, items, lookup_fields):
    for item in items:
        model.objects.update_or_create(
            organization_id=project.organization_id,
            project_id=project.id,
            **{field: item[field] for field in lookup_fields},
            defaults={
                field: (None if value == '' else value)
                for field, value in item.items()
                if field not in lookup_fields
            },
        )


@transaction.atomic
def save_task_config(project, data):
    _save_rows(TaskTypeConfig, project, data.get('task_types', []), ['key'])
    _save_rows(TaskStatusConfig, project, data.get('task_statuses', []), ['key'])
    _save_rows(TaskPriorityConfig, project, data.get('task_priorities', []), ['key'])
    _save_rows(
        TaskFieldOptionConfig,
        project,
        data.get('task_field_options', []),
        ['task_type', 'field', 'value'],
    )


def get_system_config(project):
    return {
        'task': get_task_config(project),
        **{module: get_option_config(project, module) for module in MODULE_OPTION_FIELDS},
    }


def get_option_config(project, module):
    rows = {
        (row.field, row.value): row
        for row in _project_rows(OptionConfig, project).filter(module=module)
    }
    options = []
    for field, option in MODULE_OPTION_FIELDS[module].items():
        items = []
        for index, (value, default_label) in enumerate(option.model_field.choices):
            row = rows.get((field, value))
            items.append({
                'field': field,
                'value': value,
                'default_label': default_label,
                'label': row.label if row else None,
                'color': row.color if row else None,
                'enabled': row.enabled if row else True,
                'order': row.order if row else index,
                'is_default': value == option.model_field.default,
                'editable': list(option.editable),
            })
        options.extend(sorted(items, key=lambda item: item['order']))
    return {'options': options}


def ensure_options_enabled(instance, update_fields=None):
    for module, fields in MODULE_OPTION_FIELDS.items():
        for key, option in fields.items():
            if not isinstance(instance, option.model):
                continue
            if update_fields is not None and option.field not in update_fields:
                continue
            value = getattr(instance, option.field)
            if value in (None, '', option.model_field.default):
                continue
            if getattr(instance, '_loaded_values', {}).get(option.field) == value:
                continue
            if _project_rows(OptionConfig, attrgetter(option.project)(instance)).filter(
                module=module, field=key, value=value, enabled=False
            ).exists():
                raise serializers.ValidationError({option.field: 'This option is disabled for this project.'})


def clean_options(module, items):
    fields = MODULE_OPTION_FIELDS[module]
    cleaned = []
    for item in items:
        option = fields.get(item['field'])
        choices = dict(option.model_field.choices) if option else {}
        if item['value'] not in choices:
            raise serializers.ValidationError(f"Unknown option {item['field']}.{item['value']}.")
        row = {key: value for key, value in item.items() if key in ('field', 'value', *option.editable)}
        if row.get('enabled') is False and item['value'] == option.model_field.default:
            raise serializers.ValidationError(f"{choices[item['value']]} is the default and cannot be disabled.")
        cleaned.append(row)
    return cleaned


@transaction.atomic
def save_option_config(project, module, items):
    _project_rows(OptionConfig, project).filter(module=module).delete()
    _save_rows(OptionConfig, project, [{**item, 'module': module} for item in items], ['module', 'field', 'value'])


@transaction.atomic
def save_system_config(project, data):
    if 'task' in data:
        save_task_config(project, data['task'])
    for module in MODULE_OPTION_FIELDS:
        if module in data:
            save_option_config(project, module, data[module]['options'])
