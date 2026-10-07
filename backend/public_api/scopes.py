"""Scope vocabulary for public API credentials."""

RESOURCES = (
    'customers',
    'organisations',
    'tickets',
    'conversations',
    'templates',
    'routing_rules',
    'queues',
    'agents',
)

READ = 'read'
WRITE = 'write'

ALL_SCOPES = tuple(f'{resource}:{access}' for resource in RESOURCES for access in (READ, WRITE))


def grants(scopes, resource, access):
    """True when `scopes` allows `access` on `resource`. A write scope implies read."""
    if access == READ:
        return f'{resource}:{READ}' in scopes or f'{resource}:{WRITE}' in scopes
    return f'{resource}:{WRITE}' in scopes
