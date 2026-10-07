"""Mounted at /api/v1/."""

from django.urls import include, path
from oauth2_provider.views import TokenView
from rest_framework.routers import SimpleRouter

from public_api import views

router = SimpleRouter()
for prefix, viewset, basename in views.RESOURCE_VIEWSETS:
    router.register(prefix, viewset, basename=basename)

urlpatterns = [
    # Only the token endpoint: clients are created in the admin console, and the
    # client-credentials grant has no authorize step.
    path('oauth/token/', TokenView.as_view(), name='public-api-oauth-token'),
    path('csm/whoami/', views.WhoAmIView.as_view(), name='public-api-whoami'),
    path('csm/', include(router.urls)),
]
