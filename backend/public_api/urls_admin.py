"""Mounted at /api/csm/integrations/."""

from django.urls import include, path
from rest_framework.routers import SimpleRouter

from public_api import views_admin

router = SimpleRouter()
router.register(r'api-keys', views_admin.ApiKeyViewSet, basename='integrations-api-key')
router.register(r'oauth-clients', views_admin.OAuthClientViewSet, basename='integrations-oauth-client')
router.register(r'webhooks', views_admin.WebhookEndpointViewSet, basename='integrations-webhook')
router.register(r'webhook-deliveries', views_admin.WebhookDeliveryViewSet, basename='integrations-webhook-delivery')

urlpatterns = [
    path('vocabulary/', views_admin.IntegrationsVocabularyView.as_view(), name='integrations-vocabulary'),
    path('', include(router.urls)),
]
