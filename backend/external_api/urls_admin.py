"""Admin console routes, mounted at /api/external-api/ by backend/external_urls.py."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from external_api import views_admin

router = SimpleRouter()
router.register(r'api-keys', views_admin.ApiKeyViewSet, basename='external-api-admin-api-key')
router.register(r'oauth-clients', views_admin.OAuthClientViewSet, basename='external-api-admin-oauth-client')
router.register(r'webhooks', views_admin.WebhookEndpointViewSet, basename='external-api-admin-webhook')
router.register(r'webhook-deliveries', views_admin.WebhookDeliveryViewSet, basename='external-api-admin-webhook-delivery')

urlpatterns = [
    path('vocabulary/', views_admin.IntegrationsVocabularyView.as_view(), name='external-api-admin-vocabulary'),
    path('', include(router.urls)),
]
