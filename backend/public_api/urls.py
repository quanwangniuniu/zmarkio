"""Mounted at /api/v1/."""

from django.urls import include, path
from oauth2_provider.views import TokenView
from rest_framework.routers import SimpleRouter

from public_api import views

router = SimpleRouter()
router.register(r'customers', views.CustomerViewSet, basename='public-api-customer')
router.register(r'organisations', views.CustomerOrganisationViewSet, basename='public-api-organisation')
router.register(r'tickets', views.TicketViewSet, basename='public-api-ticket')
router.register(r'conversations', views.ConversationViewSet, basename='public-api-conversation')
router.register(r'templates', views.TemplateViewSet, basename='public-api-template')
router.register(r'routing-rules', views.RoutingRuleViewSet, basename='public-api-routing-rule')
router.register(r'queues', views.QueueViewSet, basename='public-api-queue')
router.register(r'agents', views.AgentViewSet, basename='public-api-agent')

urlpatterns = [
    # Only the token endpoint: clients are created in the admin console, and the
    # client-credentials grant has no authorize step.
    path('oauth/token/', TokenView.as_view(), name='public-api-oauth-token'),
    path('csm/', include(router.urls)),
]
