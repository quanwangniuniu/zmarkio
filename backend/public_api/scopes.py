"""Scope vocabulary for public API credentials. A write scope implies read."""

from public_api.models import WebhookEvent

RESOURCES = {
    'customers': 'Customers',
    'organisations': 'Organisations',
    'tickets': 'Tickets',
    'conversations': 'Conversations',
    'templates': 'Quick reply templates',
    'routing_rules': 'Routing rules',
    'queues': 'Queues',
    'agents': 'Agents',
}

ALL_SCOPES = tuple(f'{resource}:{access}' for resource in RESOURCES for access in ('read', 'write'))


def vocabulary_payload():
    """What the admin console offers: resources to grant and events to subscribe to."""
    return {
        'resources': [{'value': value, 'label': label} for value, label in RESOURCES.items()],
        'events': [{'value': value, 'label': label} for value, label in WebhookEvent.choices],
    }
