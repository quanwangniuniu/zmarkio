from celery import shared_task
from django.utils import timezone

from csm.models import ConversationMessage, Ticket
from csm.services.sla import notify_sla_breach
from csm.services.status_machine import (
    tickets_due_for_auto_resolve,
    RESOLVED_STATUS,
)


@shared_task
def auto_resolve_pending_tickets():
    """Move tickets stuck in Pending Customer Response past the configured cutoff
    to Resolved and send the configured notification to the customer.

    Runs on Celery Beat (see CELERY_BEAT_SCHEDULE). Returns the count resolved.
    """
    resolved = 0
    for ticket, config in tickets_due_for_auto_resolve():
        ticket.status = RESOLVED_STATUS
        ticket.pending_since = None
        ticket.save(update_fields=['status', 'pending_since'])

        # Notify the customer via the linked conversation, mirroring the manual
        # resolve flow. The conversation's own status is left untouched —
        # ticket and conversation lifecycles are independent.
        # Tickets with no conversation are resolved silently.
        if ticket.conversation_id:
            ConversationMessage.objects.create(
                conversation_id=ticket.conversation_id,
                sender_type='system',
                content=config.notification_message,
            )
        resolved += 1
    return resolved


@shared_task
def notify_sla_breaches():
    """Alert on tickets that have breached their first-response or resolution SLA.

    Runs on Celery Beat. Each breach notifies once (guarded by the
    *_breach_notified flags). Resolved/closed tickets are ignored; paused tickets
    (Pending Customer Response) are skipped for resolution since their clock is
    stopped and the deadline still shifts on resume. Tickets whose SLA policy is
    inactive are skipped — a stored deadline under switched-off tracking is not a
    live breach. Returns the count notified.
    """
    from csm.services.sla import _policy_for_ticket

    now = timezone.now()
    notified = 0

    first_response = Ticket.objects.filter(
        first_response_due__lt=now,
        first_responded_at__isnull=True,
        first_response_breach_notified=False,
    ).exclude(status__in=[RESOLVED_STATUS, 'closed']).select_related('queue', 'assigned_to')
    for ticket in first_response:
        if _policy_for_ticket(ticket) is None:
            continue
        notify_sla_breach(ticket, 'first_response')
        ticket.first_response_breach_notified = True
        ticket.save(update_fields=['first_response_breach_notified'])
        notified += 1

    resolution = Ticket.objects.filter(
        resolution_due__lt=now,
        resolution_breach_notified=False,
    ).exclude(status__in=[RESOLVED_STATUS, 'closed', 'pending_customer']).select_related('queue', 'assigned_to')
    for ticket in resolution:
        if _policy_for_ticket(ticket) is None:
            continue
        notify_sla_breach(ticket, 'resolution')
        ticket.resolution_breach_notified = True
        ticket.save(update_fields=['resolution_breach_notified'])
        notified += 1

    return notified
