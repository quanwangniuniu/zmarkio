from django.urls import path

from .views import RegistryView

app_name = "org_customization"

urlpatterns = [
    path("registry/", RegistryView.as_view(), name="registry"),
]
