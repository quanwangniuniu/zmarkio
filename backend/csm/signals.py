"""
Domain events CSM announces for other modules (e.g. outbound webhooks).

CSM only sends these; it does not know who listens.
"""

from django.dispatch import Signal

# kwargs: ticket
ticket_created = Signal()
# kwargs: ticket, previous_status
ticket_status_changed = Signal()
# kwargs: ticket, breach_type ('first_response' | 'resolution')
sla_breached = Signal()
