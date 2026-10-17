"""External API routes (/api/v1/) and their admin console (/api/external-api/), kept out of urls.py."""

from django.urls import include, path

urlpatterns = [
    path('api/v1/', include('external_api.urls')),
    path('api/external-api/', include('external_api.urls_admin')),
]
