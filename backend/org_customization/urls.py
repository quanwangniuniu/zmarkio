from django.urls import path

from org_customization.views import RegistryView

app_name = 'org_customization'

urlpatterns = [
    path('registry/', RegistryView.as_view(), name='registry'),
]
