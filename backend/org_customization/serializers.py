from rest_framework import serializers

from task.models import Task


class TaskTypeConfigItemSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=Task._meta.get_field('type').choices)
    label = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    enabled = serializers.BooleanField(required=False, default=True)
    order = serializers.IntegerField(required=False, default=0, min_value=0)


class TaskStatusConfigItemSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=Task.Status.choices)
    label = serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)
    color = serializers.RegexField(
        r'^#[0-9A-Fa-f]{6}$', required=False, allow_null=True, allow_blank=True
    )
    order = serializers.IntegerField(required=False, default=0, min_value=0)


class TaskConfigSerializer(serializers.Serializer):
    task_types = TaskTypeConfigItemSerializer(many=True, required=False)
    task_statuses = TaskStatusConfigItemSerializer(many=True, required=False)
