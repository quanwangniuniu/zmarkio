"""Mounted at /api/csm/integrations/."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from public_api import views_admin

router = DefaultRouter()
router.register(r'api-keys', views_admin.ApiKeyViewSet, basename='integrations-api-key')
router.register(r'oauth-clients', views_admin.OAuthClientViewSet, basename='integrations-oauth-client')

urlpatterns = [
    path('vocabulary/', views_admin.IntegrationsVocabularyView.as_view(), name='integrations-vocabulary'),
    path('', include(router.urls)),
]
