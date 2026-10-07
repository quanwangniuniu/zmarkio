from django.apps import AppConfig


class PublicApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'public_api'
    verbose_name = 'Public API & Webhooks'

    def ready(self):
        import public_api.signals  # noqa: F401
