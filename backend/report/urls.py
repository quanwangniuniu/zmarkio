from django.urls import path
from report import views

app_name = "report"

urlpatterns = [
    path(
        "reports/",
        views.ReportListCreateView.as_view(),
        name="report-list-create",
    ),
    path(
        "reports/<int:id>/",
        views.ReportRetrieveUpdateView.as_view(),
        name="report-detail",
    ),
    path(
        "reports/<int:id>/key-actions/",
        views.ReportKeyActionListCreateView.as_view(),
        name="report-key-actions-list-create",
    ),
    path(
        "reports/<int:id>/key-actions/<int:action_id>/",
        views.ReportKeyActionRetrieveUpdateDestroyView.as_view(),
        name="report-key-action-detail",
    ),
    # Custom KPIs. `kpis/preview/` precedes `kpis/<int:id>/` only for clarity --
    # the int converter would not match "preview" either way.
    path(
        "kpi-metrics/",
        views.KPIMetricCatalogView.as_view(),
        name="kpi-metric-catalog",
    ),
    path(
        "kpis/preview/",
        views.CustomKPIPreviewView.as_view(),
        name="custom-kpi-preview",
    ),
    path(
        "kpis/",
        views.CustomKPIListCreateView.as_view(),
        name="custom-kpi-list-create",
    ),
    path(
        "kpis/<int:id>/",
        views.CustomKPIDetailView.as_view(),
        name="custom-kpi-detail",
    ),
]
