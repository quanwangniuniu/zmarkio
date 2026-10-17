from django.apps import AppConfig


class ExternalApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'external_api'
    verbose_name = 'External API & Webhooks'

    def ready(self):
        import external_api.receivers  # noqa: F401
