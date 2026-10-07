from django.apps import AppConfig


class PublicApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'public_api'
    verbose_name = 'Public API & Webhooks'

    def ready(self):
        from public_api import signals  # noqa: F401  (registers ticket webhook receivers)
