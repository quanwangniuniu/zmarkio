from django.urls import path
from .views import DashboardSummaryView, ProjectWorkspaceDashboardView, CrossProjectRollupView

app_name = 'dashboard'

urlpatterns = [
    path('summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('workspace/', ProjectWorkspaceDashboardView.as_view(), name='project-workspace'),
    path('rollup/', CrossProjectRollupView.as_view(), name='rollup'),
]