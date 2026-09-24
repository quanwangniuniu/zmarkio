"""Routing sandbox API tests (CSM-S03-05): trace shape and isolation."""

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status

from csm.models import (
    Conversation, ConversationMessage, CsmNotification, CustomerUser, Queue,
    RoutingRule, SupportChannel, SupportChannelExperienceGroup, Ticket,
)
from customer.models import CustomerOrganisation

pytestmark = pytest.mark.django_db

# A Monday, 10:00 UTC (inside default 09:00-17:00 weekday hours) and a Sunday.
MONDAY_OPEN = datetime(2026, 9, 21, 10, 0, tzinfo=dt_timezone.utc)
SUNDAY_CLOSED = datetime(2026, 9, 27, 10, 0, tzinfo=dt_timezone.utc)


def _url(project_id):
    return reverse('routing-sandbox-evaluate') + f'?project={project_id}'


@pytest.fixture
def csm_admin_client(api_client, user, customer_organisation):
    CustomerUser.objects.get_or_create(
        user=user,
        organisation=customer_organisation,
        defaults={'user_type': 'admin', 'is_active': True},
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def billing_queue(project, customer_organisation):
    return Queue.objects.create(
        project=project, organisation=customer_organisation, name='Billing', tier='T2',
    )


@pytest.fixture
def channel(project, csm_queue, experience_group):
    ch = SupportChannel.objects.create(
        project=project, channel_type='live_chat', display_name='Web chat',
        default_queue=csm_queue, timezone='UTC',
    )
    SupportChannelExperienceGroup.objects.create(channel=ch, experience_group=experience_group)
    return ch


@pytest.fixture
def refund_rule(project, experience_group, billing_queue):
    return RoutingRule.objects.create(
        project=project, experience_group=experience_group, name='Refunds', position=0,
        conditions=[{'field': 'latest_message', 'operator': 'contains_any', 'value': ['refund']}],
        target_queue=billing_queue, add_tags=['billing'],
    )


def _evaluate(client, project, experience_group, messages, **extra):
    payload = {'experience_group': experience_group.id, 'messages': messages, **extra}
    return client.post(_url(project.id), payload, format='json')


class TestTrace:
    def test_matching_rule_routes_and_reports_conditions(
        self, csm_admin_client, project, experience_group, channel, refund_rule, billing_queue,
    ):
        res = _evaluate(
            csm_admin_client, project, experience_group, ['Hi', 'I need a refund'],
            support_channel=channel.id, simulated_at=MONDAY_OPEN.isoformat(),
        )
        assert res.status_code == status.HTTP_200_OK, res.data
        assert res.data['rule_count'] == 1
        assert res.data['warnings'] == []
        assert res.data['support_channel']['is_online'] is True

        [trace] = res.data['traces']
        assert trace['message_count'] == 2
        step = trace['steps'][0]
        assert step['status'] == 'matched'
        assert step['conditions'][0]['passed'] is True
        assert step['conditions'][0]['detail'] == 'Matched: refund'
        assert trace['outcome'] == {
            'decided_by': 'rule', 'rule_id': refund_rule.id, 'rule_name': 'Refunds',
            'queue_id': billing_queue.id, 'queue_name': 'Billing', 'tags': ['billing'],
        }
        assert trace['fallback'] is None

    def test_no_match_falls_back_to_channel_default_queue(
        self, csm_admin_client, project, experience_group, channel, refund_rule, csm_queue,
    ):
        res = _evaluate(
            csm_admin_client, project, experience_group, ['Hello'], support_channel=channel.id,
        )
        trace = res.data['traces'][0]
        assert trace['steps'][0]['status'] == 'not_matched'
        assert trace['fallback']['source'] == 'channel_default_queue'
        assert trace['outcome']['decided_by'] == 'fallback'
        assert trace['outcome']['queue_id'] == csm_queue.id

    def test_no_channel_falls_back_to_organisation_queue(
        self, csm_admin_client, project, experience_group, customer_organisation, csm_queue,
    ):
        res = _evaluate(
            csm_admin_client, project, experience_group, ['Hello'],
            customer_organisation=customer_organisation.id,
        )
        trace = res.data['traces'][0]
        assert trace['fallback']['source'] == 'first_organisation_queue'
        assert trace['outcome']['queue_id'] == csm_queue.id
        assert "has no routing rules" in res.data['warnings'][0]

    def test_each_prefix_returns_one_trace_per_message(
        self, csm_admin_client, project, experience_group, channel, refund_rule,
    ):
        res = _evaluate(
            csm_admin_client, project, experience_group, ['Hi', 'refund', 'thanks'],
            support_channel=channel.id, evaluate_each_prefix=True,
        )
        outcomes = [t['outcome']['decided_by'] for t in res.data['traces']]
        assert outcomes == ['fallback', 'rule', 'fallback']
        assert [t['message_count'] for t in res.data['traces']] == [1, 2, 3]

    def test_channel_status_follows_simulated_time(
        self, csm_admin_client, project, experience_group, channel, billing_queue,
    ):
        RoutingRule.objects.create(
            project=project, experience_group=experience_group, name='After hours',
            conditions=[{'field': 'channel_status', 'operator': 'equals', 'value': 'offline'}],
            target_queue=billing_queue,
        )
        closed = _evaluate(
            csm_admin_client, project, experience_group, ['Hi'],
            support_channel=channel.id, simulated_at=SUNDAY_CLOSED.isoformat(),
        )
        assert closed.data['support_channel']['offline_reason'] == 'closed_day'
        assert closed.data['traces'][0]['outcome']['queue_id'] == billing_queue.id

        opened = _evaluate(
            csm_admin_client, project, experience_group, ['Hi'],
            support_channel=channel.id, simulated_at=MONDAY_OPEN.isoformat(),
        )
        assert opened.data['traces'][0]['outcome']['decided_by'] == 'fallback'

    def test_rule_edits_are_reflected_immediately(
        self, csm_admin_client, project, experience_group, channel, refund_rule,
    ):
        first = _evaluate(csm_admin_client, project, experience_group, ['refund'], support_channel=channel.id)
        assert first.data['traces'][0]['outcome']['decided_by'] == 'rule'

        refund_rule.is_enabled = False
        refund_rule.save()

        second = _evaluate(csm_admin_client, project, experience_group, ['refund'], support_channel=channel.id)
        assert second.data['traces'][0]['steps'][0]['status'] == 'disabled'
        assert second.data['traces'][0]['outcome']['decided_by'] == 'fallback'

    def test_channel_warnings(self, csm_admin_client, project, experience_group, csm_queue):
        email = SupportChannel.objects.create(
            project=project, channel_type='email', display_name='Inbox', is_active=False,
        )
        res = _evaluate(csm_admin_client, project, experience_group, ['Hi'], support_channel=email.id)
        warnings = ' '.join(res.data['warnings'])
        assert 'is inactive' in warnings
        assert 'not live chat' in warnings
        assert 'not assigned to experience group' in warnings


class TestValidation:
    def test_foreign_experience_group(self, csm_admin_client, project, organization, user):
        from core.models import Project
        from experience_group.models import ExperienceGroup
        other = Project.objects.create(
            name='Other', organization=organization, owner=user, objectives=['awareness'], kpis={},
        )
        group = ExperienceGroup.objects.create(project=other, name='Elsewhere')
        res = _evaluate(csm_admin_client, project, group, ['Hi'])
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'experience_group' in res.data

    def test_foreign_customer_organisation(self, csm_admin_client, project, experience_group):
        from core.models import Organization
        other_org = Organization.objects.create(name='Other Org')
        foreign = CustomerOrganisation.objects.create(name='Foreign', organization=other_org)
        res = _evaluate(
            csm_admin_client, project, experience_group, ['Hi'], customer_organisation=foreign.id,
        )
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'customer_organisation' in res.data

    def test_messages_are_required_and_bounded(self, csm_admin_client, project, experience_group):
        assert _evaluate(csm_admin_client, project, experience_group, []).status_code == 400
        too_many = ['x'] * 51
        assert _evaluate(csm_admin_client, project, experience_group, too_many).status_code == 400
        too_long = ['x' * 5001]
        assert _evaluate(csm_admin_client, project, experience_group, too_long).status_code == 400

    def test_requires_csm_admin(self, member_client, project, experience_group):
        res = _evaluate(member_client, project, experience_group, ['Hi'])
        assert res.status_code == status.HTTP_403_FORBIDDEN


class TestIsolation:
    """AC1/AC4: a sandbox session leaves no records and sends nothing."""

    def test_evaluate_writes_nothing_and_broadcasts_nothing(
        self, csm_admin_client, project, experience_group, channel, refund_rule,
        customer_organisation, django_capture_on_commit_callbacks,
    ):
        counted = (Conversation, ConversationMessage, Ticket, CsmNotification, RoutingRule)
        before = {model: model.objects.count() for model in counted}
        broadcasts = []

        with patch('channels.layers.get_channel_layer') as layer, \
                django_capture_on_commit_callbacks() as callbacks, \
                CaptureQueriesContext(connection) as queries:
            layer.return_value.group_send.side_effect = lambda *a, **k: broadcasts.append(a)
            res = _evaluate(
                csm_admin_client, project, experience_group, ['Hi', 'refund please'],
                support_channel=channel.id, customer_organisation=customer_organisation.id,
                evaluate_each_prefix=True,
            )

        assert res.status_code == status.HTTP_200_OK, res.data
        assert {model: model.objects.count() for model in counted} == before
        assert broadcasts == []
        assert callbacks == []
        writes = [
            q['sql'] for q in queries.captured_queries
            if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
        ]
        assert writes == []
