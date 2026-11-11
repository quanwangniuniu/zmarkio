from django.urls import path

from .views import TaskConfigView

urlpatterns = [
    path('task-config/', TaskConfigView.as_view(), name='org-customization-task-config'),
]
