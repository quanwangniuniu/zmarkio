from django.apps import AppConfig
import os


class AccessControlConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "access_control"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)))