from django.apps import AppConfig


class OrgCustomizationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'org_customization'

    def ready(self):
        import org_customization.signals
