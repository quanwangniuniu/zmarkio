"""External API routes, mounted at /api/v1/ by backend/external_urls.py."""

from django.urls import include, path
from oauth2_provider.views import TokenView
from rest_framework.routers import SimpleRouter

from external_api import views

router = SimpleRouter()
router.register(r'customers', views.CustomerResource, basename='external-api-customer')
router.register(r'organisations', views.CustomerOrganisationResource, basename='external-api-organisation')
router.register(r'tickets', views.TicketResource, basename='external-api-ticket')
router.register(r'conversations', views.ConversationResource, basename='external-api-conversation')
router.register(r'templates', views.TemplateResource, basename='external-api-template')
router.register(r'routing-rules', views.RoutingRuleResource, basename='external-api-routing-rule')
router.register(r'queues', views.QueueResource, basename='external-api-queue')
router.register(r'agents', views.AgentResource, basename='external-api-agent')

urlpatterns = [
    # Only the token endpoint: clients are created in the admin console, and the
    # client-credentials grant has no authorize step.
    path('oauth/token/', TokenView.as_view(), name='external-api-oauth-token'),
    path('csm/', include(router.urls)),
]
