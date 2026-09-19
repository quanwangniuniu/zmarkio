from django.apps import AppConfig


class AgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agent'

    def ready(self):
        """Load agent hooks and fail fast on an invalid column registry."""
        import agent.signals  # noqa: F401
        import agent.checks  # noqa: F401

        from .column_registry import validate_registry

        # Plugin registration happens during application boot. Validate again
        # after all app configs have loaded so hot-reload collisions cannot be
        # hidden by whichever plugin happened to load last.
        validate_registry()
