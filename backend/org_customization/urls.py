from django.urls import path

from .views import OptionConfigView, TaskConfigView

urlpatterns = [
    path('task-config/', TaskConfigView.as_view(), name='org-customization-task-config'),
    path('csm-config/', OptionConfigView.as_view(module='csm'), name='org-customization-csm-config'),
]
