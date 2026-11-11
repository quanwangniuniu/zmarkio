from django.db import models

from core.models import Organization


class TaskTypeConfig(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    key = models.CharField(max_length=50)
    label = models.CharField(max_length=100, null=True, blank=True)
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
    color = models.CharField(max_length=7, null=True, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'config"."task_status'
        constraints = [
            models.UniqueConstraint(fields=['organization', 'key'], name='uniq_config_task_status_org_key'),
        ]
