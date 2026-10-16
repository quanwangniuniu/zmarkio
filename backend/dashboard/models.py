from django.conf import settings
from django.db import models


class DashboardLayout(models.Model):
    """One positioned card on a user's project dashboard."""

    project = models.ForeignKey(
        'core.Project', on_delete=models.CASCADE, related_name='dashboard_layouts'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='dashboard_layouts',
    )
    widget_id = models.CharField(max_length=64)
    x = models.PositiveSmallIntegerField()
    y = models.PositiveSmallIntegerField()
    w = models.PositiveSmallIntegerField()
    h = models.PositiveSmallIntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'user', 'widget_id'],
                name='dashboard_layout_user_project_widget',
            ),
        ]
