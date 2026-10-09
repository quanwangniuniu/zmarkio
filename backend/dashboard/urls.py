from django.urls import path
from .views import DashboardSummaryView, ProjectWorkspaceDashboardView, DashboardLayoutView

app_name = 'dashboard'

urlpatterns = [
    path('layout/', DashboardLayoutView.as_view(), name='dashboard-layout'),
    path('summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    # SMP-472: Project Workspace Dashboard
    path('workspace/', ProjectWorkspaceDashboardView.as_view(), name='project-workspace'),
]
