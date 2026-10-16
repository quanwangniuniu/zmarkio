from django.urls import path
from .views import DashboardSummaryView, ProjectWorkspaceDashboardView, DashboardLayoutView, CrossProjectRollupView

app_name = 'dashboard'

urlpatterns = [
    path('layout/', DashboardLayoutView.as_view(), name='dashboard-layout'),
    path('summary/', DashboardSummaryView.as_view(), name='dashboard-summary'),
    path('workspace/', ProjectWorkspaceDashboardView.as_view(), name='project-workspace'),
    path('rollup/', CrossProjectRollupView.as_view(), name='rollup'),
]
