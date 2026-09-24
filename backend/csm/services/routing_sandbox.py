"""
Routing sandbox (CSM-S03-05): evaluate routing rules for a simulated
conversation without touching live data.

Read-only by design: this module only queries configuration (experience
groups, channels, queues, rules) and hands plain dataclasses to the pure
engine. It never creates Conversation / ConversationMessage / Ticket rows,
never sends channel-layer events and never triggers notifications.
"""

from dataclasses import asdict

from django.core.exceptions import ValidationError
from django.utils import timezone

from csm.models import Queue, SupportChannel, SupportChannelExperienceGroup
from customer.models import CustomerOrganisation
from experience_group.models import ExperienceGroup
from csm.services.routing_engine import (
    FallbackSpec,
    QueueRef,
    RoutingContext,
    RuleSpec,
    evaluate_rules,
)
from csm.services.routing_rules import list_rules
from csm.services.support_channels import evaluate_channel_availability


def _queue_ref(queue):
    if queue is None:
        return None
    return QueueRef(id=queue.id, name=queue.name, is_active=queue.is_active)


def _rule_spec(rule):
    return RuleSpec(
        id=rule.id,
        name=rule.name,
        position=rule.position,
        is_enabled=rule.is_enabled,
        match_mode=rule.match_mode,
        conditions=tuple(rule.conditions or ()),
        target_queue=_queue_ref(rule.target_queue),
        add_tags=tuple(rule.add_tags or ()),
    )


def _resolve_experience_group(project, experience_group_id):
    group = ExperienceGroup.objects.filter(pk=experience_group_id, project_id=project.id).first()
    if group is None:
        raise ValidationError({'experience_group': 'Experience group not found in this workspace.'})
    return group


def _resolve_channel(project, support_channel_id):
    if support_channel_id is None:
        return None
    channel = (
        SupportChannel.objects.filter(pk=support_channel_id, project_id=project.id)
        .select_related('default_queue')
        .first()
    )
    if channel is None:
        raise ValidationError({'support_channel': 'Channel not found in this workspace.'})
    return channel


def _resolve_organisation(project, customer_organisation_id):
    if customer_organisation_id is None:
        return None
    organisation = CustomerOrganisation.objects.filter(
        pk=customer_organisation_id, organization_id=project.organization_id,
    ).first()
    if organisation is None:
        raise ValidationError({
            'customer_organisation': 'Customer organisation not found in this workspace.',
        })
    return organisation


def _channel_warnings(channel, group):
    warnings = []
    if not channel.is_active:
        warnings.append(f"Channel '{channel.display_name}' is inactive.")
    if channel.channel_type != SupportChannel.ChannelType.LIVE_CHAT:
        warnings.append(
            f"Channel '{channel.display_name}' is not live chat; live conversations can only start on live chat channels.",
        )
    linked = SupportChannelExperienceGroup.objects.filter(
        channel=channel, experience_group=group,
    ).exists()
    if not linked:
        warnings.append(
            f"Channel '{channel.display_name}' is not assigned to experience group '{group.name}'.",
        )
    return warnings


def run_sandbox(project, *, experience_group_id, messages, subject='',
                support_channel_id=None, customer_organisation_id=None,
                simulated_at=None, evaluate_each_prefix=False):
    """
    Evaluate the experience group's current routing rules for `messages`.

    Returns a JSON-ready dict with one trace per evaluated message prefix
    (only the full conversation unless `evaluate_each_prefix`).
    """
    group = _resolve_experience_group(project, experience_group_id)
    channel = _resolve_channel(project, support_channel_id)
    organisation = _resolve_organisation(project, customer_organisation_id)
    evaluated_at = simulated_at or timezone.now()

    warnings = []
    availability = None
    if channel is not None:
        warnings.extend(_channel_warnings(channel, group))
        availability = evaluate_channel_availability(channel, at=evaluated_at)

    organisation_queue = None
    if channel is None and organisation is not None:
        # Mirrors PortalConversationViewSet.create: first queue of the
        # customer's organisation, in Queue.Meta ordering.
        organisation_queue = Queue.objects.filter(organisation=organisation).first()

    fallback = FallbackSpec(
        has_channel=channel is not None,
        channel_default_queue=_queue_ref(channel.default_queue) if channel else None,
        organisation_queue=_queue_ref(organisation_queue),
    )
    rules = [_rule_spec(rule) for rule in list_rules(project.id, group.id)]
    if not rules:
        warnings.append(f"Experience group '{group.name}' has no routing rules.")

    prefix_lengths = range(1, len(messages) + 1) if evaluate_each_prefix else [len(messages)]
    traces = []
    for length in prefix_lengths:
        ctx = RoutingContext(
            messages=tuple(messages[:length]),
            subject=subject or '',
            support_channel_id=channel.id if channel else None,
            channel_type=channel.channel_type if channel else None,
            channel_online=availability['is_online'] if availability else None,
            channel_offline_reason=availability['reason'] if availability else None,
            customer_organisation_id=organisation.id if organisation else None,
        )
        traces.append(asdict(evaluate_rules(rules, ctx, fallback, evaluated_at=evaluated_at)))

    return {
        'experience_group': {'id': group.id, 'name': group.name, 'status': group.status},
        'support_channel': (
            {
                'id': channel.id,
                'display_name': channel.display_name,
                'channel_type': channel.channel_type,
                'is_online': availability['is_online'],
                'offline_reason': availability['reason'],
            }
            if channel else None
        ),
        'rule_count': len(rules),
        'traces': traces,
        'warnings': warnings,
    }
