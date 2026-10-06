"""Validated, project-scoped dashboard layout persistence."""

import re
import math

from rest_framework import serializers

from .models import DashboardLayout


# Sizes match the frontend widgetRegistry presets; keep the two in sync.
DEFAULT_WIDGETS = [
    {'id': 'overall-progress', 'x': 0, 'y': 0, 'w': 4, 'h': 3},
    {'id': 'tasks-completed', 'x': 4, 'y': 0, 'w': 4, 'h': 3},
    {'id': 'task-completion-rate', 'x': 8, 'y': 0, 'w': 4, 'h': 3},
    {'id': 'overdue-tasks', 'x': 0, 'y': 3, 'w': 6, 'h': 3},
    {'id': 'needs-attention', 'x': 6, 'y': 3, 'w': 6, 'h': 3},
    {'id': 'decisions', 'x': 0, 'y': 6, 'w': 4, 'h': 7},
    {'id': 'tasks', 'x': 4, 'y': 6, 'w': 4, 'h': 7},
    {'id': 'operations', 'x': 8, 'y': 6, 'w': 4, 'h': 7},
    {'id': 'task-status', 'x': 0, 'y': 13, 'w': 4, 'h': 5},
    {'id': 'task-priority', 'x': 4, 'y': 13, 'w': 4, 'h': 5},
    {'id': 'task-types', 'x': 8, 'y': 13, 'w': 4, 'h': 6},
    {'id': 'task-trend', 'x': 0, 'y': 19, 'w': 12, 'h': 5},
    {'id': 'custom-kpis', 'x': 0, 'y': 24, 'w': 12, 'h': 4},
    {'id': 'meetings', 'x': 0, 'y': 28, 'w': 6, 'h': 4},
    {'id': 'activity', 'x': 6, 'y': 28, 'w': 6, 'h': 4},
    {'id': 'audit', 'x': 0, 'y': 32, 'w': 6, 'h': 4},
    {'id': 'project-team', 'x': 6, 'y': 32, 'w': 6, 'h': 6},
]
WIDGET_IDS = {widget['id'] for widget in DEFAULT_WIDGETS}
POSITION_EPSILON = 1e-7  # Ignore floating-point noise from 10px moves/resizes.
MAX_SECTION_TITLES = 10  # Keep in sync with the frontend builder's sectionTitle.ts.


def is_section_title(widget_id):
    return isinstance(widget_id, str) and re.fullmatch(r'section-title-[a-zA-Z0-9_-]{1,48}', widget_id) is not None


def _overlaps(a, b):
    return (
        a['x'] < b['x'] + b['w'] - POSITION_EPSILON and b['x'] < a['x'] + a['w'] - POSITION_EPSILON
        and a['y'] < b['y'] + b['h'] - POSITION_EPSILON and b['y'] < a['y'] + a['h'] - POSITION_EPSILON
    )


def _push_down_overlaps(widgets):
    """Keep every widget's column and size; move it below anything it would cover."""
    placed = []
    for widget in sorted(widgets, key=lambda item: (item['y'], item['x'])):
        widget = dict(widget)
        while True:
            blocker = next((other for other in placed if _overlaps(other, widget)), None)
            if blocker is None:
                break
            widget['y'] = blocker['y'] + blocker['h']
        placed.append(widget)
    return sorted(placed, key=lambda item: (item['y'], item['x']))


def _group_title_widget(item, index, seen):
    """Turn a saved group's title into a one-row section title, or None."""
    title = item.get('title')
    if not isinstance(title, str) or not title.strip():
        return None
    suffix = re.sub(r'[^a-zA-Z0-9_-]', '', str(item.get('id', '')))[:48] or f'group-{index}'
    widget_id = f'section-title-{suffix}'
    if widget_id in seen:
        widget_id = f'section-title-group-{index}'
    x, y, w = item.get('x'), item.get('y'), item.get('w')
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in (x, y, w)):
        return None
    if not (0 <= x < 12 and 0 <= y <= 999 and 1 <= w <= 12 - x):
        return None
    return {'id': widget_id, 'title': title.strip()[:80], 'x': x, 'y': y, 'w': w, 'h': 1}


def widgets_for_response(raw):
    """Read saved v2/v3 group layouts as flat widgets without changing the row.

    A group's title becomes a section title above its children, which move down
    one row to make room; anything that would then overlap is pushed below.
    """
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
        return DEFAULT_WIDGETS

    widgets = []
    seen = set()
    title_count = 0
    for index, item in enumerate(raw['items']):
        if not isinstance(item, dict):
            continue
        is_group = item.get('kind') == 'group'
        children = item.get('children', []) if is_group else [item]
        if not isinstance(children, list):
            continue
        title = _group_title_widget(item, index, seen) if is_group and title_count < MAX_SECTION_TITLES else None
        if title:
            title_count += 1
            widgets.append(title)
            seen.add(title['id'])
        title_rows = 1 if title else 0
        for child in children:
            if not isinstance(child, dict) or child.get('id') not in WIDGET_IDS or child['id'] in seen:
                continue
            try:
                x = child['x'] + (item['x'] if is_group else 0)
                y = child['y'] + (item['y'] + title_rows if is_group else 0)
                w, h = child['w'], child['h']
            except (KeyError, TypeError):
                continue
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in (x, y, w, h)):
                continue
            if not (0 <= x < 12 and 0 <= y <= 999 and 1 <= w <= 12 - x and 3 <= h <= 30):
                continue
            widgets.append({'id': child['id'], 'x': x, 'y': y, 'w': w, 'h': h})
            seen.add(child['id'])
    return _push_down_overlaps(widgets) if title_count else widgets


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
        if sum(is_section_title(widget['id']) for widget in widgets) > MAX_SECTION_TITLES:
            raise serializers.ValidationError(f'A dashboard can have at most {MAX_SECTION_TITLES} section titles.')
        seen = set()
        for widget in widgets:
            if widget['id'] in seen:
                raise serializers.ValidationError('Widget IDs must be unique.')
            seen.add(widget['id'])
        for index, a in enumerate(widgets):
            for b in widgets[index + 1:]:
                if _overlaps(a, b):
                    raise serializers.ValidationError('Widgets may not overlap.')
        return widgets


def save_layout(project, user, widgets):
    layout, _ = DashboardLayout.objects.update_or_create(
        project=project, user=user, defaults={'widgets': widgets},
    )
    return layout
