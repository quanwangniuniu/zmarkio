"""Turn CSM's domain events (csm.signals) into outbound webhook deliveries."""

from django.dispatch import receiver

from csm.signals import sla_breached, ticket_created, ticket_status_changed
from external_api.models import WebhookEvent
from external_api.services import emit_ticket_event


@receiver(ticket_created, dispatch_uid='external_api_ticket_created')
def on_ticket_created(sender, ticket, **kwargs):
    emit_ticket_event(WebhookEvent.TICKET_CREATED, ticket)


@receiver(ticket_status_changed, dispatch_uid='external_api_ticket_status_changed')
def on_ticket_status_changed(sender, ticket, previous_status, **kwargs):
    emit_ticket_event(WebhookEvent.TICKET_STATUS_CHANGED, ticket, previous_status=previous_status)


@receiver(sla_breached, dispatch_uid='external_api_sla_breached')
def on_sla_breached(sender, ticket, breach_type, **kwargs):
    emit_ticket_event(WebhookEvent.SLA_BREACHED, ticket, breach_type=breach_type)
