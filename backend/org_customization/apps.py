from django.apps import AppConfig


class OrgCustomizationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'org_customization'
    verbose_name = 'Org Customization'

    def ready(self):
        # Duplicate keys / malformed key formats must fail at Django startup.
        from org_customization.registry import validate_registry

        validate_registry()
