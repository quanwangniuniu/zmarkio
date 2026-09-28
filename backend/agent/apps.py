from django.apps import AppConfig
from django.core.checks import Error, register


def check_column_registry(app_configs, **kwargs):
    """Validate registry definitions during Django's startup and lint checks."""
    from .column_registry import ColumnRegistryCollisionError, validate_registry

    try:
        validate_registry()
    except ColumnRegistryCollisionError as exc:
        return [Error(
            str(exc),
            hint='Use unique schema keys and column names/aliases within each schema.',
            id='agent.E001',
        )]
    return []


class AgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agent'

    def ready(self):
        import agent.signals  # noqa: F401
        import agent.column_registry  # noqa: F401 — validates names on registry boot
        register(check_column_registry)
