"""Validated, project-scoped dashboard layouts, including one-level groups."""

import re
from copy import deepcopy
from django.db import transaction
from rest_framework import serializers

from .models import DashboardLayout


DEFAULT_WIDGETS = [
    {'id': 'overall-progress', 'x': 0, 'y': 0, 'w': 4, 'h': 4},
    {'id': 'tasks-completed', 'x': 4, 'y': 0, 'w': 4, 'h': 4},
    {'id': 'task-completion-rate', 'x': 8, 'y': 0, 'w': 4, 'h': 4},
    {'id': 'overdue-tasks', 'x': 0, 'y': 4, 'w': 6, 'h': 4},
    {'id': 'needs-attention', 'x': 6, 'y': 4, 'w': 6, 'h': 4},
    {'id': 'decisions', 'x': 0, 'y': 8, 'w': 4, 'h': 10},
    {'id': 'tasks', 'x': 4, 'y': 8, 'w': 4, 'h': 10},
    {'id': 'operations', 'x': 8, 'y': 8, 'w': 4, 'h': 10},
    {'id': 'task-status', 'x': 0, 'y': 18, 'w': 6, 'h': 9},
    {'id': 'task-priority', 'x': 6, 'y': 18, 'w': 6, 'h': 9},
    {'id': 'task-types', 'x': 6, 'y': 27, 'w': 6, 'h': 8},
    {'id': 'task-trend', 'x': 0, 'y': 27, 'w': 6, 'h': 8},
    {'id': 'custom-kpis', 'x': 0, 'y': 35, 'w': 12, 'h': 7},
    {'id': 'meetings', 'x': 0, 'y': 42, 'w': 6, 'h': 8},
    {'id': 'activity', 'x': 6, 'y': 42, 'w': 6, 'h': 8},
    {'id': 'audit', 'x': 0, 'y': 50, 'w': 6, 'h': 8},
    {'id': 'project-team', 'x': 6, 'y': 50, 'w': 6, 'h': 10},
]
WIDGET_IDS = {widget['id'] for widget in DEFAULT_WIDGETS}
WORKSPACE_WIDGET_IDS = {widget['id'] for widget in DEFAULT_WIDGETS[:12]}


def widget_item(position):
    return {'kind': 'widget', **position}


DEFAULT_ITEMS = [
    {
        'kind': 'group', 'id': 'group-project-overview', 'title': 'Project Overview',
        'x': 0, 'y': 0, 'w': 12, 'h': 9,
        'children': [
            widget_item({**widget, 'w': 6, 'x': 6 if widget['id'] == 'task-completion-rate' else widget['x']})
            if widget['id'] in ('overall-progress', 'task-completion-rate') else widget_item(widget)
            for widget in DEFAULT_WIDGETS[:5] if widget['id'] != 'tasks-completed'
        ],
    },
    {
        'kind': 'group', 'id': 'group-module-summary', 'title': 'Module Summary',
        'x': 0, 'y': 9, 'w': 12, 'h': 11,
        'children': [widget_item({**widget, 'y': 0}) for widget in DEFAULT_WIDGETS[5:8]],
    },
    {
        'kind': 'group', 'id': 'group-tasks', 'title': 'Tasks',
        'x': 0, 'y': 20, 'w': 12, 'h': 18,
        'children': [widget_item({**widget, 'y': widget['y'] - 18}) for widget in DEFAULT_WIDGETS[8:12]],
    },
    *[widget_item({**widget, 'y': widget['y'] + 3}) for widget in DEFAULT_WIDGETS[12:]],
]


def normalize_legacy_workspace(widgets):
    """Expand old all-in-one Workspace while keeping removals and other widgets."""
    legacy = next((widget for widget in widgets if widget['id'] == 'workspace'), None)
    if legacy is None:
        return widgets
    existing_ids = {widget['id'] for widget in widgets if widget['id'] != 'workspace'}
    replacements = [
        {**widget, 'y': legacy['y'] + widget['y']}
        for widget in DEFAULT_WIDGETS
        if widget['id'] in WORKSPACE_WIDGET_IDS and widget['id'] not in existing_ids
    ]
    # The expanded group needs more height than the former 12-row tile.
    expanded_bottom = legacy['y'] + max(
        widget['y'] + widget['h'] for widget in DEFAULT_WIDGETS
        if widget['id'] in WORKSPACE_WIDGET_IDS
    )
    old_bottom = legacy['y'] + legacy['h']
    others = sorted(
        (dict(widget) for widget in widgets if widget['id'] != 'workspace'),
        key=lambda widget: (widget['y'], widget['x']),
    )
    placed = list(replacements)
    for widget in others:
        if widget['y'] >= old_bottom:
            widget['y'] += expanded_bottom - old_bottom
        elif widget['y'] + widget['h'] > legacy['y']:
            # A manually moved card might sit alongside the old tile. Move it
            # below the expanded group rather than returning overlapping tiles.
            widget['y'] = expanded_bottom
        while True:
            blocker = next((placed_widget for placed_widget in placed if (
                widget['x'] < placed_widget['x'] + placed_widget['w']
                and placed_widget['x'] < widget['x'] + widget['w']
                and widget['y'] < placed_widget['y'] + placed_widget['h']
                and placed_widget['y'] < widget['y'] + widget['h']
            )), None)
            if blocker is None:
                break
            widget['y'] = blocker['y'] + blocker['h']
        placed.append(widget)
    return placed


class WidgetPositionSerializer(serializers.Serializer):
    id = serializers.ChoiceField(choices=sorted(WIDGET_IDS))
    x = serializers.IntegerField(min_value=0, max_value=11)
    y = serializers.IntegerField(min_value=0, max_value=999)
    w = serializers.IntegerField(min_value=1, max_value=12)
    h = serializers.IntegerField(min_value=3, max_value=30)

    def validate(self, attrs):
        if attrs['x'] + attrs['w'] > 12:
            raise serializers.ValidationError('Widget extends beyond the grid.')
        return attrs


class DashboardLayoutSerializer(serializers.Serializer):
    widgets = WidgetPositionSerializer(many=True, allow_empty=True)

    def validate_widgets(self, widgets):
        if len(widgets) > len(WIDGET_IDS):
            raise serializers.ValidationError('Too many widgets.')
        seen = set()
        for widget in widgets:
            if widget['id'] in seen:
                raise serializers.ValidationError('Widget IDs must be unique.')
            seen.add(widget['id'])
        for index, a in enumerate(widgets):
            for b in widgets[index + 1:]:
                if a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']:
                    raise serializers.ValidationError('Widgets may not overlap.')
        return widgets


class LayoutPositionSerializer(serializers.Serializer):
    x = serializers.IntegerField(min_value=0, max_value=11)
    y = serializers.IntegerField(min_value=0, max_value=999)
    w = serializers.IntegerField(min_value=1, max_value=12)
    h = serializers.IntegerField(min_value=3, max_value=120)

    def validate(self, attrs):
        if attrs['x'] + attrs['w'] > 12:
            raise serializers.ValidationError('Item extends beyond the grid.')
        return attrs


class WidgetItemSerializer(LayoutPositionSerializer):
    kind = serializers.ChoiceField(choices=['widget'])
    id = serializers.ChoiceField(choices=sorted(WIDGET_IDS))
    title = serializers.CharField(max_length=80, required=False, allow_blank=False, trim_whitespace=True)
    settings = serializers.DictField(required=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs['h'] > 30:
            raise serializers.ValidationError('Widget height must be at most 30 rows.')
        settings = attrs.get('settings', {})
        if set(settings) - {'accent'} or settings.get('accent', 'slate') not in ('slate', 'cyan', 'lime'):
            raise serializers.ValidationError('Unsupported widget settings.')
        return attrs


class GroupItemSerializer(LayoutPositionSerializer):
    kind = serializers.ChoiceField(choices=['group'])
    id = serializers.CharField(max_length=54)
    title = serializers.CharField(max_length=80, trim_whitespace=True)
    children = WidgetItemSerializer(many=True, allow_empty=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not re.fullmatch(r'group-[a-zA-Z0-9_-]{1,48}', attrs['id']):
            raise serializers.ValidationError('Invalid group ID.')
        if any(child['y'] + child['h'] + 1 > attrs['h'] for child in attrs['children']):
            raise serializers.ValidationError('Group is too short for its children.')
        validate_no_overlap(attrs['children'])
        return attrs


def validate_no_overlap(items):
    for index, a in enumerate(items):
        for b in items[index + 1:]:
            if a['x'] < b['x'] + b['w'] and b['x'] < a['x'] + a['w'] and a['y'] < b['y'] + b['h'] and b['y'] < a['y'] + a['h']:
                raise serializers.ValidationError('Layout items may not overlap.')


class DashboardDocumentSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=2, max_value=3)
    items = serializers.ListField(child=serializers.DictField(), allow_empty=True)

    def validate_items(self, items):
        if len(items) > len(WIDGET_IDS) + 12:
            raise serializers.ValidationError('Too many layout items.')
        result = []
        widget_ids = set()
        group_ids = set()
        for item in items:
            serializer_class = GroupItemSerializer if item.get('kind') == 'group' else WidgetItemSerializer
            serializer = serializer_class(data=item)
            serializer.is_valid(raise_exception=True)
            value = serializer.validated_data
            if value['kind'] == 'group':
                if value['id'] in group_ids:
                    raise serializers.ValidationError('Group IDs must be unique.')
                group_ids.add(value['id'])
                children = value['children']
            else:
                children = [value]
            for child in children:
                if child['id'] in widget_ids:
                    raise serializers.ValidationError('A widget can appear only once.')
                widget_ids.add(child['id'])
            result.append(value)
        validate_no_overlap(result)
        return result


def upgrade_document(raw):
    """Expose the split chart in saved v2 layouts; a v3 removal remains intentional."""
    document = deepcopy(raw)
    if document['version'] == 3:
        return document
    document['version'] = 3
    items = document['items']
    if any(item['id'] == 'task-types' or (
        item['kind'] == 'group' and any(child['id'] == 'task-types' for child in item['children'])
    ) for item in items):
        return document
    for item in items:
        children = item['children'] if item['kind'] == 'group' else [item]
        priority = next((child for child in children if child['id'] == 'task-priority'), None)
        if priority is None:
            continue
        y = priority['y'] + priority['h']
        type_widget = widget_item({
            'id': 'task-types', 'x': priority['x'], 'y': y,
            'w': min(6, 12 - priority['x']), 'h': 7,
        })
        if item['kind'] == 'group':
            old_h = item['h']
            for child in children:
                if child is not priority and child['y'] >= y:
                    child['y'] += type_widget['h']
            children.append(type_widget)
            item['h'] = max(item['h'], max(child['y'] + child['h'] + 1 for child in children))
            delta = item['h'] - old_h
            if delta:
                for other in items:
                    if other is not item and other['y'] >= item['y'] + old_h:
                        other['y'] += delta
        else:
            for other in items:
                if other is not item and other['y'] >= y:
                    other['y'] += type_widget['h']
            items.append(type_widget)
        break
    return document


def layout_response(raw):
    """Read older rows without mutating them; version 3 keeps chart removals."""
    if isinstance(raw, dict) and raw.get('version') in (2, 3):
        return upgrade_document(raw)
    widgets = normalize_legacy_workspace(raw) if raw is not None else DEFAULT_WIDGETS
    items = [widget_item(widget) for widget in widgets] if raw is not None else DEFAULT_ITEMS
    return {**upgrade_document({'version': 2, 'items': items}), 'widgets': widgets}


@transaction.atomic
def save_layout(project, user, widgets):
    layout, _ = DashboardLayout.objects.select_for_update().get_or_create(
        project=project, user=user, defaults={'widgets': widgets},
    )
    layout.widgets = widgets
    layout.save(update_fields=['widgets', 'updated_at'])
    return layout
