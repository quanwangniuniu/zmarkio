from django.conf import settings
from django.db import models


class DashboardLayout(models.Model):
    """One user's versioned dashboard JSON for one project, including an empty layout."""

    project = models.ForeignKey('core.Project', on_delete=models.CASCADE, related_name='dashboard_layouts')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='dashboard_layouts')
    widgets = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['project', 'user'], name='dashboard_layout_user_project')]
