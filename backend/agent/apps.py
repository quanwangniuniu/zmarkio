from django.apps import AppConfig


class AgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agent'

    def ready(self):
        """Register checks without disabling the diagnostics endpoint."""
        import agent.signals  # noqa: F401
        import agent.checks  # noqa: F401
