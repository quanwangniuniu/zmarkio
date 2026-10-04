"""Validated, project-scoped dashboard layout persistence."""

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
    {'id': 'task-trend', 'x': 0, 'y': 27, 'w': 12, 'h': 8},
    {'id': 'custom-kpis', 'x': 0, 'y': 35, 'w': 12, 'h': 7},
    {'id': 'meetings', 'x': 0, 'y': 42, 'w': 6, 'h': 8},
    {'id': 'activity', 'x': 6, 'y': 42, 'w': 6, 'h': 8},
    {'id': 'audit', 'x': 0, 'y': 50, 'w': 6, 'h': 8},
    {'id': 'project-team', 'x': 6, 'y': 50, 'w': 6, 'h': 10},
]
WIDGET_IDS = {widget['id'] for widget in DEFAULT_WIDGETS}


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


@transaction.atomic
def save_layout(project, user, widgets):
    layout, _ = DashboardLayout.objects.select_for_update().get_or_create(
        project=project, user=user, defaults={'widgets': widgets},
    )
    layout.widgets = widgets
    layout.save(update_fields=['widgets', 'updated_at'])
    return layout
