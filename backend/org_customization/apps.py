from django.apps import AppConfig


class OrgCustomizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "org_customization"

    def ready(self):
        from .registry import get_surface_registry

        get_surface_registry()
