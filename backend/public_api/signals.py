"""
Ticket webhooks from model saves.

Every ticket creation and status change in the product goes through
Ticket.save() (agent views, portal replies, auto-resolve), so hooking the save
covers them all without touching each call site. Bulk `.update()` calls bypass
this and emit explicitly (see csm.views.TicketStatusViewSet.destroy).
"""

from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from csm.models import Ticket
from public_api.models import WebhookEvent
from public_api.services import emit_ticket_event


@receiver(post_init, sender=Ticket, dispatch_uid='public_api_ticket_status_snapshot')
def remember_loaded_status(sender, instance, **kwargs):
    # __dict__ rather than the attribute: reading a deferred field here would query.
    instance._webhook_status = instance.__dict__.get('status')


@receiver(post_save, sender=Ticket, dispatch_uid='public_api_ticket_webhooks')
def emit_ticket_webhooks(sender, instance, created, raw=False, **kwargs):
    if raw:
        return  # fixture loading
    previous = getattr(instance, '_webhook_status', None)
    instance._webhook_status = instance.status
    if created:
        emit_ticket_event(WebhookEvent.TICKET_CREATED, instance)
    elif previous is not None and previous != instance.status:
        emit_ticket_event(WebhookEvent.TICKET_STATUS_CHANGED, instance, previous_status=previous)
