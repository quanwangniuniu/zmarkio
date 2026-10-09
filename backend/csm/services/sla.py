"""SLA calculation service for CSM tickets."""

from datetime import timedelta
from django.utils import timezone

from csm.services.business_hours import add_open_minutes, minutes_open_between


def _derive_sla_anchor(ticket, old_first_response_minutes):
    """Infer the SLA start time from an existing deadline and the old target."""
    if ticket.first_response_due is not None and old_first_response_minutes is not None:
        return ticket.first_response_due - timedelta(minutes=old_first_response_minutes)
    return None


def recalculate_ticket_sla(ticket, base_time=None):
    """
    Compute and persist first_response_due / resolution_due on a ticket.

    base_time: the reference point for deadline calculation.
      - None (default): uses ticket.created_at — for initial ticket creation.
      - timezone.now(): pass explicitly on priority change so the countdown
        restarts from the moment of the change, not the original creation time.
      - For policy updates, use recalculate_ticket_sla_after_policy_change() so
        each ticket keeps its existing SLA anchor.
    """
    policy, target, calendar = _resolve_policy_target(ticket)
    if policy is None or target is None:
        ticket.first_response_due = None
        ticket.resolution_due = None
        return

    if base_time is None:
        base_time = ticket.created_at or timezone.now()

    if calendar is not None:
        ticket.first_response_due = add_open_minutes(
            base_time, target.first_response_minutes, calendar.schedule, calendar.timezone,
        )
        ticket.resolution_due = add_open_minutes(
            base_time, target.resolution_minutes, calendar.schedule, calendar.timezone,
        )
    else:
        ticket.first_response_due = base_time + timedelta(minutes=target.first_response_minutes)
        ticket.resolution_due = base_time + timedelta(minutes=target.resolution_minutes)


def recalculate_ticket_sla_after_policy_change(
    ticket, old_targets_by_priority, *, policy_reactivated=False,
):
    """
    Recompute SLA dues after a policy update while preserving each ticket's anchor.

    old_targets_by_priority: dict mapping priority -> (first_response_minutes,
      resolution_minutes), snapshotted before targets are replaced.
    policy_reactivated: True when is_active flips from False to True. Tickets whose
      dues were cleared on deactivation restart from now(), not created_at.
    """
    old_fr_minutes = None
    old_entry = old_targets_by_priority.get(ticket.priority)
    if old_entry is not None:
        old_fr_minutes = old_entry[0]

    base_time = _derive_sla_anchor(ticket, old_fr_minutes)
    if base_time is None:
        if policy_reactivated:
            base_time = timezone.now()
        else:
            base_time = ticket.created_at or timezone.now()
    recalculate_ticket_sla(ticket, base_time=base_time)


def _get_project_id(ticket):
    """Resolve project_id from ticket → queue → project chain."""
    try:
        return ticket.queue.project_id
    except Exception:
        return None


def _policy_for_ticket(ticket):
    """The active SLA policy governing a ticket, or None.

    A ticket inherits its queue's assigned policy; a queue with none (or an
    inactive one) falls back to the project's default policy, then to any
    active policy in the project.
    """
    from csm.models import SLAPolicy

    queue = getattr(ticket, 'queue', None)
    if queue is not None and queue.sla_policy_id:
        policy = SLAPolicy.objects.select_related('calendar').filter(
            pk=queue.sla_policy_id, is_active=True,
        ).first()
        if policy is not None:
            return policy

    project_id = _get_project_id(ticket)
    if not project_id:
        return None
    active = SLAPolicy.objects.filter(project_id=project_id, is_active=True).select_related('calendar')
    return active.filter(is_default=True).first() or active.first()


def _resolve_policy_target(ticket):
    """Active policy, matching priority target, and calendar, or (None, None, None)."""
    from csm.models import SLAPriorityTarget

    policy = _policy_for_ticket(ticket)
    if policy is None:
        return None, None, None
    target = SLAPriorityTarget.objects.filter(
        policy=policy, priority=ticket.priority,
    ).first()
    return policy, target, policy.calendar


def _clock_running(ticket, policy, target, calendar, now):
    """Whether the SLA countdown is advancing at ``now``.

    Frozen when there is no SLA, while paused in Pending Customer Response, or
    outside the business-hours calendar. With no calendar the clock is 24/7.
    """
    if target is None:
        return False
    if ticket.status == 'pending_customer' and policy is not None and policy.pause_on_pending:
        return False
    if calendar is not None:
        window = minutes_open_between(
            now, now + timedelta(minutes=1), calendar.schedule, calendar.timezone,
        )
        return window > 0
    return True


def get_sla_status(ticket):
    """
    Return a dict describing the current SLA status for a ticket.

    Keys:
      first_response_due  – ISO datetime string or None
      resolution_due      – ISO datetime string or None
      first_response_breached – bool
      resolution_breached     – bool
      first_response_remaining_seconds – int or None (business time when a calendar applies)
      resolution_remaining_seconds     – int or None
      first_response_state – 'ok' | 'amber' | 'breached' | None
      resolution_state     – 'ok' | 'amber' | 'breached' | None
    """
    now = timezone.now()
    # One policy lookup per ticket; prefetch when serializing long queue lists.
    policy, target, calendar = _resolve_policy_target(ticket)

    # The active policy is the source of truth for whether a ticket has an SLA.
    # With no resolvable target (no policy, or SLA tracking switched off) the
    # ticket reads as no-SLA regardless of any deadlines still stored on it, so
    # toggling tracking off/on is non-destructive: the stored dues resume as-is.
    if target is None:
        return {
            'first_response_due': None,
            'resolution_due': None,
            'first_response_breached': False,
            'resolution_breached': False,
            'first_response_met': False,
            'clock_running': False,
            'first_response_remaining_seconds': None,
            'resolution_remaining_seconds': None,
            'first_response_state': None,
            'resolution_state': None,
        }

    def _remaining(due):
        if due is None:
            return None
        if calendar is not None and now < due:
            return int(minutes_open_between(now, due, calendar.schedule, calendar.timezone) * 60)
        return int((due - now).total_seconds())

    def _state(due, breached, total_minutes):
        if due is None:
            return None
        if breached:
            return 'breached'
        remaining = _remaining(due)
        if total_minutes and remaining is not None and remaining < total_minutes * 60 * 0.25:
            return 'amber'
        return 'ok'

    # A resolved or closed ticket is done: the resolution countdown stops rather
    # than keep ticking or read as breached, the same way first response freezes
    # once the agent has replied.
    resolution_settled = ticket.status in ('resolved', 'closed')
    if resolution_settled:
        res_breached = False
        res_remaining = None
        res_state = None
    else:
        res_breached = bool(ticket.resolution_due and now > ticket.resolution_due)
        res_remaining = _remaining(ticket.resolution_due)
        res_state = _state(
            ticket.resolution_due, res_breached,
            target.resolution_minutes if target else None,
        )

    # Once the agent has replied, the first-response countdown freezes: no live
    # remaining time, and breach reflects whether the reply beat the deadline.
    responded_at = ticket.first_responded_at
    if responded_at is not None:
        fr_breached = bool(ticket.first_response_due and responded_at > ticket.first_response_due)
        fr_remaining = None
        fr_state = 'breached' if fr_breached else 'ok'
    else:
        fr_breached = bool(ticket.first_response_due and now > ticket.first_response_due)
        fr_remaining = _remaining(ticket.first_response_due)
        fr_state = _state(
            ticket.first_response_due, fr_breached,
            target.first_response_minutes if target else None,
        )

    return {
        'first_response_due': ticket.first_response_due.isoformat() if ticket.first_response_due else None,
        'resolution_due': ticket.resolution_due.isoformat() if ticket.resolution_due else None,
        'first_response_breached': fr_breached,
        'resolution_breached': res_breached,
        # True once the agent has replied on time — the UI shows a met marker
        # instead of a countdown. False if they never replied or replied late.
        'first_response_met': responded_at is not None and not fr_breached,
        # Whether the clock is actually advancing right now, so the UI can freeze
        # the countdown outside business hours or while paused instead of ticking
        # wall-clock seconds off a snapshot that isn't moving.
        'clock_running': _clock_running(ticket, policy, target, calendar, now),
        'first_response_remaining_seconds': fr_remaining,
        'resolution_remaining_seconds': res_remaining,
        'first_response_state': fr_state,
        'resolution_state': res_state,
    }


def resume_sla_clock(ticket, now=None):
    """Push the due dates forward by the time a ticket spent awaiting the customer.

    Time in Pending Customer Response should not count against the SLA. On
    resume, extend each deadline by the amount consumed while paused — business
    time when a calendar applies, otherwise wall-clock. Mutates the ticket in
    memory; the caller persists. No-op when the ticket was not paused, has no
    deadlines, or the policy disables pausing.
    """
    if ticket.pending_since is None:
        return
    if ticket.first_response_due is None and ticket.resolution_due is None:
        return
    policy, _, calendar = _resolve_policy_target(ticket)
    if policy is None or not policy.pause_on_pending:
        return

    now = now or timezone.now()
    if calendar is not None:
        paused = minutes_open_between(
            ticket.pending_since, now, calendar.schedule, calendar.timezone,
        )
        if ticket.first_response_due is not None:
            ticket.first_response_due = add_open_minutes(
                ticket.first_response_due, paused, calendar.schedule, calendar.timezone,
            )
        if ticket.resolution_due is not None:
            ticket.resolution_due = add_open_minutes(
                ticket.resolution_due, paused, calendar.schedule, calendar.timezone,
            )
    else:
        paused = now - ticket.pending_since
        if ticket.first_response_due is not None:
            ticket.first_response_due = ticket.first_response_due + paused
        if ticket.resolution_due is not None:
            ticket.resolution_due = ticket.resolution_due + paused


def _breach_recipients(ticket):
    """Users to alert on a breach: the assigned agent and the queue's supervisors."""
    from csm.models import CustomerUser

    recipients = {}
    if ticket.assigned_to_id and ticket.assigned_to:
        recipients[ticket.assigned_to_id] = ticket.assigned_to
    supervisors = CustomerUser.objects.filter(
        queue_id=ticket.queue_id, user_type='supervisor', is_active=True,
    ).select_related('user')
    for cu in supervisors:
        if cu.user_id:
            recipients[cu.user_id] = cu.user
    return list(recipients.values())


def notify_sla_breach(ticket, kind):
    """Alert the assigned agent and queue supervisors that a ticket breached SLA.

    kind is 'first_response' or 'resolution'. Creates an in-app CsmNotification
    per recipient and sends them a single email. Safe to call with no recipients.
    """
    from django.conf import settings
    from django.core.mail import send_mail
    from csm.models import CsmNotification

    recipients = _breach_recipients(ticket)
    if not recipients:
        return

    label = 'First response' if kind == 'first_response' else 'Resolution'
    title = f'SLA breached: {label} — {ticket.title}'
    message = (
        f'Ticket "{ticket.title}" (#{ticket.id}) has breached its {label.lower()} '
        f'SLA target in queue {ticket.queue.name}.'
    )
    CsmNotification.objects.bulk_create([
        CsmNotification(
            recipient=user,
            notification_type='sla_breach',
            title=title,
            message=message,
            metadata={'ticket_id': ticket.id, 'breach_type': kind},
        )
        for user in recipients
    ])

    emails = [u.email for u in recipients if u.email]
    if emails:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@mediajira.com')
        send_mail(title, message, from_email, emails, fail_silently=True)
