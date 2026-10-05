"""Validated, project-scoped dashboard layout persistence."""

import re
import math

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
    {'id': 'task-status', 'x': 0, 'y': 18, 'w': 4, 'h': 9},
    {'id': 'task-priority', 'x': 4, 'y': 18, 'w': 4, 'h': 9},
    {'id': 'task-types', 'x': 8, 'y': 18, 'w': 4, 'h': 9},
    {'id': 'task-trend', 'x': 0, 'y': 27, 'w': 12, 'h': 8},
    {'id': 'custom-kpis', 'x': 0, 'y': 35, 'w': 12, 'h': 7},
    {'id': 'meetings', 'x': 0, 'y': 42, 'w': 6, 'h': 8},
    {'id': 'activity', 'x': 6, 'y': 42, 'w': 6, 'h': 8},
    {'id': 'audit', 'x': 0, 'y': 50, 'w': 6, 'h': 8},
    {'id': 'project-team', 'x': 6, 'y': 50, 'w': 6, 'h': 10},
]
WIDGET_IDS = {widget['id'] for widget in DEFAULT_WIDGETS}
POSITION_EPSILON = 1e-7  # Ignore floating-point noise from 10px moves/resizes.


def is_section_title(widget_id):
    return isinstance(widget_id, str) and re.fullmatch(r'section-title-[a-zA-Z0-9_-]{1,48}', widget_id) is not None


def widgets_for_response(raw):
    """Read saved v2/v3 group layouts as flat widgets without changing the row."""
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
        return DEFAULT_WIDGETS

    widgets = []
    seen = set()
    for item in raw['items']:
        if not isinstance(item, dict):
            continue
        children = item.get('children', []) if item.get('kind') == 'group' else [item]
        if not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict) or child.get('id') not in WIDGET_IDS or child['id'] in seen:
                continue
            try:
                x = child['x'] + (item['x'] if item.get('kind') == 'group' else 0)
                y = child['y'] + (item['y'] if item.get('kind') == 'group' else 0)
                w, h = child['w'], child['h']
            except (KeyError, TypeError):
                continue
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in (x, y, w, h)):
                continue
            if not (0 <= x < 12 and 0 <= y <= 999 and 1 <= w <= 12 - x and 3 <= h <= 30):
                continue
            widgets.append({'id': child['id'], 'x': x, 'y': y, 'w': w, 'h': h})
            seen.add(child['id'])
    return widgets


class WidgetPositionSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=64)
    x = serializers.FloatField(min_value=0, max_value=11)
    y = serializers.FloatField(min_value=0, max_value=999)
    w = serializers.FloatField(min_value=1, max_value=12)
    h = serializers.FloatField(min_value=1, max_value=30)
    title = serializers.CharField(max_length=80, required=False, allow_blank=False)

    def validate_id(self, value):
        if value not in WIDGET_IDS and not is_section_title(value):
            raise serializers.ValidationError('Unknown widget type.')
        return value

    def validate(self, attrs):
        if not all(math.isfinite(attrs[key]) for key in ('x', 'y', 'w', 'h')):
            raise serializers.ValidationError('Widget dimensions must be finite.')
        if attrs['x'] + attrs['w'] > 12 + POSITION_EPSILON:
            raise serializers.ValidationError('Widget extends beyond the grid.')
        if is_section_title(attrs['id']):
            if 'title' not in attrs:
                raise serializers.ValidationError('Section titles need text.')
        elif attrs['h'] < 3 or 'title' in attrs:
            raise serializers.ValidationError('Invalid widget height or title.')
        return attrs


class DashboardLayoutSerializer(serializers.Serializer):
    widgets = WidgetPositionSerializer(many=True, allow_empty=True)

    def validate_widgets(self, widgets):
        if len(widgets) > 100:
            raise serializers.ValidationError('Too many widgets.')
        seen = set()
        for widget in widgets:
            if widget['id'] in seen:
                raise serializers.ValidationError('Widget IDs must be unique.')
            seen.add(widget['id'])
        for index, a in enumerate(widgets):
            for b in widgets[index + 1:]:
                if a['x'] < b['x'] + b['w'] - POSITION_EPSILON and b['x'] < a['x'] + a['w'] - POSITION_EPSILON and a['y'] < b['y'] + b['h'] - POSITION_EPSILON and b['y'] < a['y'] + a['h'] - POSITION_EPSILON:
                    raise serializers.ValidationError('Widgets may not overlap.')
        return widgets


@transaction.atomic
def save_layout(project, user, widgets):
    layout, _ = DashboardLayout.objects.select_for_update().get_or_create(
        project=project, user=user, defaults={'widgets': widgets},
    )
    layout.widgets = widgets
    layout.save(update_fields=['widgets', 'updated_at'])
    return layout
