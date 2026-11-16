from rest_framework import serializers

from task.models import Task

from .services import MODULE_OPTION_FIELDS, STATUS_CATEGORIES, clean_options

TASK_TYPE_CHOICES = Task._meta.get_field('type').choices


def label_field():
    return serializers.CharField(max_length=100, required=False, allow_null=True, allow_blank=True)


def color_field():
    return serializers.RegexField(r'^#[0-9A-Fa-f]{6}$', required=False, allow_null=True, allow_blank=True)


def order_field():
    return serializers.IntegerField(required=False, default=0, min_value=0)


class TaskTypeConfigItemSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=TASK_TYPE_CHOICES)
    label = label_field()
    short_label = serializers.CharField(max_length=30, required=False, allow_null=True, allow_blank=True)
    color = color_field()
    enabled = serializers.BooleanField(required=False, default=True)
    order = order_field()


class TaskStatusConfigItemSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=Task.Status.choices)
    label = label_field()
    category = serializers.ChoiceField(
        choices=list(STATUS_CATEGORIES), required=False, allow_null=True, allow_blank=True
    )


class TaskPriorityConfigItemSerializer(serializers.Serializer):
    key = serializers.ChoiceField(choices=Task.Priority.choices)
    label = label_field()


class TaskFieldOptionConfigItemSerializer(serializers.Serializer):
    task_type = serializers.ChoiceField(choices=TASK_TYPE_CHOICES)
    field = serializers.CharField(max_length=100)
    value = serializers.CharField(max_length=100)
    label = label_field()
    enabled = serializers.BooleanField(required=False, default=True)
    order = order_field()


class TaskConfigSerializer(serializers.Serializer):
    task_types = TaskTypeConfigItemSerializer(many=True, required=False)
    task_statuses = TaskStatusConfigItemSerializer(many=True, required=False)
    task_priorities = TaskPriorityConfigItemSerializer(many=True, required=False)
    task_field_options = TaskFieldOptionConfigItemSerializer(many=True, required=False)


class OptionConfigItemSerializer(serializers.Serializer):
    field = serializers.CharField(max_length=50)
    value = serializers.CharField(max_length=100)
    label = label_field()
    color = color_field()
    enabled = serializers.BooleanField(required=False, default=True)
    order = order_field()


class OptionConfigSerializer(serializers.Serializer):
    options = OptionConfigItemSerializer(many=True)


class SystemConfigSerializer(serializers.Serializer):
    task = TaskConfigSerializer(required=False)

    def get_fields(self):
        fields = super().get_fields()
        fields.update({module: OptionConfigSerializer(required=False) for module in MODULE_OPTION_FIELDS})
        return fields

    def validate(self, attrs):
        for module in MODULE_OPTION_FIELDS:
            if module in attrs:
                attrs[module]['options'] = clean_options(module, attrs[module]['options'])
        return attrs
