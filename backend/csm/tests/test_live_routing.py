"""
Live intake applies the customer's experience-group routing rules when a portal
conversation starts. Same engine and fallback as the sandbox, so the two cannot
disagree on the queue. Rule tags are not added to the conversation.
"""
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status

from csm.models import Conversation, Queue, RoutingRule, SupportChannel, SupportChannelExperienceGroup

pytestmark = pytest.mark.django_db

REFUND = [{'field': 'latest_message', 'operator': 'contains_any', 'value': ['refund']}]


def _start(client, message, **extra):
    return client.post(reverse('portal-conversation-list'), {'message': message, **extra}, format='json')


def _always_open_hours():
    from csm.services.support_channels import WEEKDAYS
    return {day: {'enabled': True, 'start': '00:00', 'end': '23:59'} for day in WEEKDAYS}


def _live_chat(project, queue, name='Web chat'):
    return SupportChannel.objects.create(
        project=project, channel_type=SupportChannel.ChannelType.LIVE_CHAT,
        display_name=name, default_queue=queue, operating_hours=_always_open_hours(),
    )


@pytest.fixture
def chat(project, csm_queue, experience_group):
    channel = _live_chat(project, csm_queue)
    SupportChannelExperienceGroup.objects.create(channel=channel, experience_group=experience_group)
    return channel


@pytest.fixture
def billing_queue(project, customer_organisation):
    return Queue.objects.create(
        project=project, organisation=customer_organisation, name='Billing', tier='T2', display_order=1,
    )


@pytest.fixture
def vip_customer(customer, experience_group):
    customer.experience_group = experience_group
    customer.save()
    return customer


def _rule(project, group, queue, **overrides):
    return RoutingRule.objects.create(**{
        'organization': project.organization, 'project': project, 'experience_group': group,
        'name': 'Refunds', 'position': 0, 'conditions': REFUND, 'target_queue': queue,
        'add_tags': ['billing'], **overrides,
    })


def _conversation(response):
    assert response.status_code == status.HTTP_201_CREATED, response.data
    return Conversation.objects.get(pk=response.data['id'])


def test_matching_rule_routes_the_conversation_but_keeps_its_tags(
    portal_customer_client, vip_customer, chat, project, experience_group, billing_queue,
):
    _rule(project, experience_group, billing_queue)
    conversation = _conversation(
        _start(portal_customer_client, 'I need a refund', support_channel_id=chat.id, subject='Order 42'),
    )
    assert conversation.queue_id == billing_queue.id
    assert conversation.tags == ['Order 42']  # rule tags are sandbox-only


@pytest.mark.parametrize('overrides', [
    {'conditions': [{'field': 'latest_message', 'operator': 'contains_any', 'value': ['invoice']}]},
    {'is_enabled': False},
])
def test_no_applicable_rule_keeps_the_channel_default_queue(
    portal_customer_client, vip_customer, chat, project, experience_group, billing_queue, csm_queue, overrides,
):
    _rule(project, experience_group, billing_queue, **overrides)
    conversation = _conversation(_start(portal_customer_client, 'I need a refund', support_channel_id=chat.id))
    assert conversation.queue_id == csm_queue.id
    assert conversation.tags == []


def test_rule_with_an_inactive_queue_is_skipped(
    portal_customer_client, vip_customer, chat, project, experience_group, billing_queue, csm_queue,
):
    billing_queue.is_active = False
    billing_queue.save()
    _rule(project, experience_group, billing_queue)
    conversation = _conversation(_start(portal_customer_client, 'I need a refund', support_channel_id=chat.id))
    assert conversation.queue_id == csm_queue.id


def test_customer_without_a_group_gets_todays_routing(
    portal_customer_client, customer, chat, project, experience_group, billing_queue, csm_queue,
):
    _rule(project, experience_group, billing_queue)
    conversation = _conversation(_start(portal_customer_client, 'I need a refund', support_channel_id=chat.id))
    assert conversation.queue_id == csm_queue.id


def test_another_organisations_rules_never_apply(
    portal_customer_client, vip_customer, chat, project, experience_group, billing_queue, csm_queue,
):
    from core.models import Organization
    elsewhere = Organization.objects.create(name='Elsewhere', email_domain='elsewhere.test')
    _rule(project, experience_group, billing_queue, organization=elsewhere)
    conversation = _conversation(_start(portal_customer_client, 'I need a refund', support_channel_id=chat.id))
    assert conversation.queue_id == csm_queue.id


def test_rules_also_apply_without_a_channel(
    portal_customer_client, vip_customer, project, experience_group, billing_queue, csm_queue,
):
    _rule(project, experience_group, billing_queue)
    routed = _conversation(_start(portal_customer_client, 'I need a refund'))
    assert routed.queue_id == billing_queue.id
    fallback = _conversation(_start(portal_customer_client, 'Hello'))
    assert fallback.queue_id == csm_queue.id  # the organisation's first queue, as before


def test_a_routing_failure_never_blocks_the_conversation(
    portal_customer_client, vip_customer, chat, project, experience_group, billing_queue, csm_queue,
):
    _rule(project, experience_group, billing_queue)
    with patch('csm.services.routing_rules.evaluate_rules', side_effect=RuntimeError('boom')):
        conversation = _conversation(
            _start(portal_customer_client, 'I need a refund', support_channel_id=chat.id),
        )
    assert conversation.queue_id == csm_queue.id


def test_a_channel_outside_the_customers_group_keeps_its_default_queue(
    portal_customer_client, vip_customer, project, experience_group, billing_queue, csm_queue,
):
    # As in the sandbox (which rejects this pairing), the group's rules only
    # apply on the group's own channels.
    _rule(project, experience_group, billing_queue, conditions=[])
    other_chat = _live_chat(project, csm_queue, name='Other chat')
    conversation = _conversation(
        _start(portal_customer_client, 'I need a refund', support_channel_id=other_chat.id),
    )
    assert conversation.queue_id == csm_queue.id


def test_a_database_error_inside_routing_still_creates_the_conversation(
    portal_customer_client, vip_customer, chat, project, experience_group, billing_queue, csm_queue,
):
    from django.db import connection

    def failing_query(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1 / 0')  # a real DB error, not just a Python exception

    _rule(project, experience_group, billing_queue)
    with patch('csm.services.routing_rules.evaluate_rules', side_effect=failing_query):
        conversation = _conversation(
            _start(portal_customer_client, 'I need a refund', support_channel_id=chat.id),
        )
    assert conversation.queue_id == csm_queue.id
