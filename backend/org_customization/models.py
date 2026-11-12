from django.db import models

from core.models import Organization


class TaskTypeConfig(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    key = models.CharField(max_length=50)
    label = models.CharField(max_length=100, null=True, blank=True)
    short_label = models.CharField(max_length=30, null=True, blank=True)
    color = models.CharField(max_length=7, null=True, blank=True)
    enabled = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'config"."task_type'
        constraints = [
            models.UniqueConstraint(fields=['organization', 'key'], name='uniq_config_task_type_org_key'),
        ]


class TaskStatusConfig(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    key = models.CharField(max_length=20)
    label = models.CharField(max_length=100, null=True, blank=True)
    category = models.CharField(max_length=20, null=True, blank=True)

    class Meta:
        db_table = 'config"."task_status'
        constraints = [
            models.UniqueConstraint(fields=['organization', 'key'], name='uniq_config_task_status_org_key'),
        ]


class TaskPriorityConfig(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    key = models.CharField(max_length=20)
    label = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        db_table = 'config"."task_priority'
        constraints = [
            models.UniqueConstraint(fields=['organization', 'key'], name='uniq_config_task_priority_org_key'),
        ]


class TaskFieldOptionConfig(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    task_type = models.CharField(max_length=50)
    field = models.CharField(max_length=100)
    value = models.CharField(max_length=100)
    label = models.CharField(max_length=100, null=True, blank=True)
    enabled = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'config"."task_field_option'
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'task_type', 'field', 'value'],
                name='uniq_config_task_field_option',
            ),
        ]
