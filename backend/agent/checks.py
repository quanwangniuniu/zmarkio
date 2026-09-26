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
                    "Use unique schema keys and unambiguous column names/aliases "
                    "within each schema, then restart the backend."
                ),
                id="agent.E001",
            )
        ]
    return []
