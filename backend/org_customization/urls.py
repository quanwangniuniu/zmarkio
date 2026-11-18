from django.urls import path

from .views import OptionConfigView, SystemConfigView, TaskConfigView

urlpatterns = [
    path('task-config/', TaskConfigView.as_view(), name='org-customization-task-config'),
    path('csm-config/', OptionConfigView.as_view(module='csm'), name='org-customization-csm-config'),
    path('decisions-config/', OptionConfigView.as_view(module='decisions'), name='org-customization-decisions-config'),
    path('system-config/', SystemConfigView.as_view(), name='org-customization-system-config'),
]
