from django.urls import path

from .views import OptionConfigView, SystemConfigView, TaskConfigView

urlpatterns = [
    path('task-config/', TaskConfigView.as_view(), name='org-customization-task-config'),
    path('csm-config/', OptionConfigView.as_view(module='csm'), name='org-customization-csm-config'),
    path('decisions-config/', OptionConfigView.as_view(module='decisions'), name='org-customization-decisions-config'),
    path('campaigns-config/', OptionConfigView.as_view(module='campaigns'), name='org-customization-campaigns-config'),
    path('navigation-config/', OptionConfigView.as_view(module='navigation'), name='org-customization-navigation-config'),
    path('system-config/', SystemConfigView.as_view(), name='org-customization-system-config'),
]
