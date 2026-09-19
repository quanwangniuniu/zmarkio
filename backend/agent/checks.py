"""Django system checks for agent startup configuration."""

from django.core.checks import Error, register

from .column_registry import ColumnRegistryCollisionError, validate_registry


@register()
def check_column_registry(app_configs, **kwargs):
    """Report column-name collisions during ``manage.py check``."""
    try:
        validate_registry()
    except ColumnRegistryCollisionError as exc:
        return [
            Error(
                str(exc),
                hint=(
                    "Give each plugin column a unique canonical name and alias, "
                    "or set AGENT_COLUMN_REGISTRY_TEST_MODE=1 only for tests."
                ),
                id="agent.E001",
            )
        ]
    return []
