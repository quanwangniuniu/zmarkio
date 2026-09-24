from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    QueueViewSet, QueueAgentViewSet,
    QueueTeamViewSet, CustomerUserViewSet,
    CsmNotificationViewSet, ConversationViewSet,
    QuickReplyTemplateViewSet, TemplateTagViewSet, TicketViewSet,
    TicketFormViewSet,
    SupportProjectViewSet,
    CsmWorkTypeViewSet,
    SupportChannelViewSet,
    SLAPolicyViewSet,
    BusinessHoursCalendarViewSet,
    TicketStatusViewSet,
    StatusMachineView,
    RoutingRuleViewSet,
    RoutingSandboxViewSet,
)

router = DefaultRouter()
router.register(r'queues', QueueViewSet, basename='queue')
router.register(r'customer-users', CustomerUserViewSet, basename='customer-user')
router.register(r'notifications', CsmNotificationViewSet, basename='csm-notification')
router.register(r'conversations', ConversationViewSet, basename='conversation')
router.register(r'templates', QuickReplyTemplateViewSet, basename='quick-reply-template')
router.register(r'template-tags', TemplateTagViewSet, basename='template-tag')
router.register(r'tickets', TicketViewSet, basename='ticket')
router.register(r'ticket-forms', TicketFormViewSet, basename='ticket-form')
router.register(r'support-projects', SupportProjectViewSet, basename='support-project')
router.register(r'work-types', CsmWorkTypeViewSet, basename='csm-work-type')
router.register(r'support-channels', SupportChannelViewSet, basename='support-channel')
router.register(r'sla-policy', SLAPolicyViewSet, basename='sla-policy')
router.register(r'business-hours-calendars', BusinessHoursCalendarViewSet, basename='business-hours-calendar')
router.register(r'ticket-statuses', TicketStatusViewSet, basename='ticket-status')
router.register(r'routing-rules', RoutingRuleViewSet, basename='routing-rule')
router.register(r'routing-sandbox', RoutingSandboxViewSet, basename='routing-sandbox')

urlpatterns = [
    # Standard routes
    path('', include(router.urls)),

    # Status machine: whole-machine GET + transition-set PUT + auto-resolve PATCH.
    # Operates per-project (?project=), so it is not a pk-detail resource.
    path(
        'ticket-status-machine/',
        StatusMachineView.as_view({'get': 'list', 'put': 'update'}),
        name='ticket-status-machine',
    ),
    path(
        'ticket-status-machine/auto-resolve/',
        StatusMachineView.as_view({'patch': 'auto_resolve'}),
        name='ticket-status-machine-auto-resolve',
    ),

    # Project-scoped routes
    path(
        'projects/<int:project_id>/queues/',
        QueueViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='project-queues',
    ),
    path(
        'projects/<int:project_id>/customer-users/',
        CustomerUserViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='project-customer-users',
    ),
    # Queue-scoped routes
    path(
        'queues/<int:queue_id>/agents/',
        QueueAgentViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='queue-agents',
    ),
    path(
        'queues/<int:queue_id>/agents/<int:pk>/',
        QueueAgentViewSet.as_view({'delete': 'destroy'}),
        name='queue-agent-detail',
    ),
    path(
        'queues/<int:queue_id>/teams/',
        QueueTeamViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='queue-teams',
    ),
    path(
        'queues/<int:queue_id>/teams/<int:pk>/',
        QueueTeamViewSet.as_view({'delete': 'destroy'}),
        name='queue-team-detail',
    ),
]
