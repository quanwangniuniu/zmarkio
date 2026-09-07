from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import (
    AgentSessionViewSet,
    AgentWorkflowDefinitionViewSet,
    AgentConfigStatusView,
    GenerationOutputsCatalogView,
    ChatView,
    SpreadsheetListView,
    AiConsentView,
    DataReportListView,
    DataReportDetailView,
    DataReportSummaryView,
    DataUploadView,
    FileUploadAnalyzeView,
    AnomalyLatestView,
    WorkflowStepView,
    WorkflowStepDetailView,
    StepReorderView,
    WorkflowRunDetailView,
    WorkflowRunListView,
    AgentWorkflowTemplateViewSet,
    WorkflowTriggerLogViewSet,
    WebhookReceiverView,
)

router = DefaultRouter()
router.register(r'sessions', AgentSessionViewSet, basename='agent-session')
router.register(r'workflows', AgentWorkflowDefinitionViewSet, basename='agent-workflow')
router.register(r'templates', AgentWorkflowTemplateViewSet, basename='agent-template')
router.register(r'trigger-logs', WorkflowTriggerLogViewSet, basename='agent-trigger-log')

urlpatterns = [
    path('', include(router.urls)),
    path('sessions/<uuid:session_id>/chat/', ChatView.as_view(), name='agent-chat'),
    path('workflows/<uuid:workflow_id>/steps/', WorkflowStepView.as_view(), name='agent-workflow-steps'),
    path(
        'workflows/<uuid:workflow_id>/steps/<uuid:step_id>/',
        WorkflowStepDetailView.as_view(),
        name='agent-workflow-step-detail',
    ),
    path('workflows/<uuid:workflow_id>/steps/reorder/', StepReorderView.as_view(), name='agent-workflow-steps-reorder'),
    path(
        'workflows/<uuid:workflow_id>/runs/',
        WorkflowRunListView.as_view(),
        name='agent-workflow-runs-list',
    ),
    path('workflow-runs/<uuid:run_id>/', WorkflowRunDetailView.as_view(), name='agent-workflow-run-detail'),

    path('spreadsheets/', SpreadsheetListView.as_view(), name='agent-spreadsheets'),
    path('ai-consent/', AiConsentView.as_view(), name='agent-ai-consent'),
    path('data/reports/', DataReportListView.as_view(), name='agent-data-reports'),
    path('data/reports/summary/', DataReportSummaryView.as_view(), name='agent-data-reports-summary'),
    path('data/reports/<uuid:file_id>/', DataReportDetailView.as_view(), name='agent-data-report-detail'),
    path('data/upload/', DataUploadView.as_view(), name='agent-data-upload'),
    path('upload-analyze/', FileUploadAnalyzeView.as_view(), name='agent-upload-analyze'),
    path('anomalies/latest/', AnomalyLatestView.as_view(), name='agent-anomaly-latest'),
    path('config/status/', AgentConfigStatusView.as_view(), name='agent-config-status'),
    path('generation-outputs/', GenerationOutputsCatalogView.as_view(), name='agent-generation-outputs'),

    # Webhook receiver
    path('webhooks/<uuid:workflow_id>/', WebhookReceiverView.as_view(), name='agent-webhook-receiver'),
]
