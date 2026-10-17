"""Webhooks: which product actions emit which events, and signed delivery with retries."""
import hashlib
import hmac
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from celery.exceptions import Retry
from django.urls import reverse
from django.utils import timezone

from chat.services import UnsafeUrlError
from core.crypto import decrypt_token
from csm.models import SLAPolicy, SLAPriorityTarget, Ticket, TicketStatus
from csm.tasks import notify_sla_breaches
from external_api.models import WebhookDelivery, WebhookEvent
from external_api.services import build_event, create_endpoint, post_json_safely
from external_api.tasks import deliver_webhook
from external_api.tests.conftest import resolving_to

pytestmark = pytest.mark.django_db

@pytest.fixture
def endpoint(organization, project):
    endpoint, _ = create_endpoint(
        organization_id=organization.id, project_id=project.id,
        url='https://hooks.example.com/zmarkio', events=list(WebhookEvent.values), user=None,
    )
    return endpoint


@pytest.fixture
def sent():
    """
    Collect (endpoint_id, payload) for every queued delivery. Tests run inside a
    transaction that never commits, so on-commit callbacks run immediately here;
    TestRouting.test_rolled_back_write_sends_nothing covers the real on-commit path.
    """
    calls = []
    with patch('external_api.tasks.deliver_webhook.delay', side_effect=lambda *args: calls.append(args)), \
            patch('external_api.services.transaction.on_commit', side_effect=lambda fn: fn()):
        yield calls


def _types(calls):
    return [payload['type'] for _, payload in calls]


class TestTicketCreated:
    def test_any_ticket_creation_emits_once(self, endpoint, csm_queue, organization, project, sent):
        ticket = Ticket.objects.create(queue=csm_queue, title='Printer on fire')
        ticket.save(update_fields=['title'])  # a later save of the same ticket is not a new event

        assert _types(sent) == ['ticket.created']
        endpoint_id, payload = sent[0]
        assert endpoint_id == endpoint.id
        assert payload['organization_id'] == organization.id
        assert payload['project_id'] == project.id
        assert payload['data']['ticket']['id'] == ticket.id
        assert payload['data']['ticket']['title'] == 'Printer on fire'


class TestStatusChanged:
    @pytest.fixture
    def ticket(self, csm_queue):
        return Ticket.objects.create(queue=csm_queue, title='T')

    def _reload(self, ticket):
        return Ticket.objects.get(pk=ticket.pk)

    def test_internal_patch(self, endpoint, ticket, admin_client, sent):
        admin_client.patch(reverse('ticket-detail', args=[ticket.id]), {'status': 'in_progress'}, format='json')
        assert _types(sent) == ['ticket.status_changed']
        assert sent[0][1]['data']['previous_status'] == 'todo'
        assert sent[0][1]['data']['ticket']['status'] == 'in_progress'

    def test_custom_status_delete_moves_tickets_and_emits(self, endpoint, ticket, project, admin_client, sent):
        custom = TicketStatus.objects.create(project_id=project.id, slug='waiting-on-vendor', name='Waiting', order=9)
        Ticket.objects.filter(pk=ticket.pk).update(status=custom.slug)
        sent.clear()

        response = admin_client.delete(
            f"{reverse('ticket-status-detail', args=[custom.id])}?project={project.id}&confirm=true",
        )

        assert response.status_code == 204, getattr(response, 'data', None)
        assert self._reload(ticket).status == 'in_progress'
        assert _types(sent) == ['ticket.status_changed']
        assert sent[0][1]['data']['previous_status'] == 'waiting-on-vendor'

    def test_status_and_priority_in_one_patch_announce_once(self, endpoint, ticket, admin_client, sent):
        # The internal PATCH re-saves the ticket to restart the SLA after a priority change.
        admin_client.patch(reverse('ticket-detail', args=[ticket.id]),
                           {'status': 'in_progress', 'priority': 'high'}, format='json')
        assert _types(sent) == ['ticket.status_changed']

    def test_saves_without_status_change_emit_nothing(self, endpoint, ticket, sent):
        sent.clear()
        ticket = self._reload(ticket)
        ticket.priority = 'high'
        ticket.save()
        assert sent == []


class TestSlaBreached:
    def test_breach_emits_once_per_kind(self, endpoint, csm_queue, project, user, sent):
        policy = SLAPolicy.objects.create(project=project, name='Default SLA')
        SLAPriorityTarget.objects.create(policy=policy, priority='medium', first_response_minutes=60, resolution_minutes=120)
        ticket = Ticket.objects.create(queue=csm_queue, title='Slow', priority='medium', assigned_to=user)
        Ticket.objects.filter(pk=ticket.pk).update(
            status='in_progress',
            first_response_due=timezone.now() - timedelta(minutes=5),
            resolution_due=timezone.now() - timedelta(minutes=5),
        )
        sent.clear()

        notify_sla_breaches()
        notify_sla_breaches()

        breaches = [p for _, p in sent if p['type'] == 'sla.breached']
        assert sorted(p['data']['breach_type'] for p in breaches) == ['first_response', 'resolution']


class TestRouting:
    def test_unsubscribed_and_foreign_endpoints_get_nothing(
        self, organization, project, csm_queue, other_workspace, sent,
    ):
        create_endpoint(organization_id=organization.id, project_id=project.id,
                        url='https://a.example.com', events=['sla.breached'], user=None)
        # Same project id, other workspace.
        create_endpoint(organization_id=other_workspace['organization'].id, project_id=project.id,
                        url='https://c.example.com', events=['ticket.created'], user=None)

        Ticket.objects.create(queue=csm_queue, title='T')

        assert sent == []

    def test_rolled_back_write_sends_nothing(self, endpoint, csm_queue, django_capture_on_commit_callbacks):
        from django.db import transaction
        with patch('external_api.tasks.deliver_webhook.delay') as delay:
            with django_capture_on_commit_callbacks(execute=False) as callbacks:
                try:
                    with transaction.atomic():
                        Ticket.objects.create(queue=csm_queue, title='T')
                        raise RuntimeError
                except RuntimeError:
                    pass
            assert callbacks == []
            delay.assert_not_called()


@pytest.fixture
def payload(organization, project):
    return build_event('ticket.created', organization_id=organization.id, project_id=project.id, data={'ticket': {'id': 1}})


def _response(code):
    response = MagicMock()
    response.status_code = code
    return response


def _attempt(endpoint_id, payload, retries=0):
    """Run the task as Celery would on its (retries + 1)th attempt."""
    deliver_webhook.push_request(retries=retries)
    try:
        return deliver_webhook.run(endpoint_id, payload)
    finally:
        deliver_webhook.pop_request()


@pytest.fixture
def retry():
    with patch.object(deliver_webhook, 'retry', side_effect=lambda countdown: Retry()) as mock:
        yield mock


class TestDelivery:
    def test_success_is_signed_and_logged(self, endpoint, payload):
        with patch('external_api.tasks.post_json_safely', return_value=_response(204)) as post:
            assert _attempt(endpoint.id, payload) == WebhookDelivery.Status.SUCCEEDED

        url, body, headers = post.call_args.args
        assert url == endpoint.url
        timestamp, signature = (part.split('=', 1)[1] for part in headers['X-Zmarkio-Signature'].split(','))
        expected = hmac.new(decrypt_token(endpoint.secret_encrypted).encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
        assert hmac.compare_digest(signature, expected)
        assert headers['X-Zmarkio-Event'] == 'ticket.created'
        assert headers['X-Zmarkio-Delivery'] == payload['id']

        row = WebhookDelivery.objects.get()
        assert (row.event_type, row.response_code, row.attempt) == ('ticket.created', 204, 1)
        assert row.created_at is not None

    def test_failures_retry_three_times_with_backoff_then_fail(self, endpoint, payload, retry):
        with patch('external_api.tasks.post_json_safely', return_value=_response(500)):
            for retries in (0, 1, 2):
                with pytest.raises(Retry):
                    _attempt(endpoint.id, payload, retries)
            assert _attempt(endpoint.id, payload, 3) == WebhookDelivery.Status.FAILED

        rows = list(WebhookDelivery.objects.order_by('attempt'))
        assert [r.attempt for r in rows] == [1, 2, 3, 4]
        assert [r.status for r in rows] == ['retrying', 'retrying', 'retrying', 'failed']
        assert all(r.response_code == 500 for r in rows)
        assert [c.kwargs['countdown'] for c in retry.call_args_list] == [30, 120, 480]

    def test_network_error_is_retried(self, endpoint, payload, retry):
        with patch('external_api.tasks.post_json_safely', side_effect=requests.ConnectTimeout('slow')), \
                pytest.raises(Retry):
            _attempt(endpoint.id, payload)

        row = WebhookDelivery.objects.get()
        assert row.status == 'retrying' and row.response_code is None and 'ConnectTimeout' in row.error

    def test_blocked_target_is_not_retried(self, endpoint, payload, retry):
        with patch('external_api.tasks.post_json_safely', side_effect=UnsafeUrlError('private')):
            assert _attempt(endpoint.id, payload) == WebhookDelivery.Status.FAILED
        retry.assert_not_called()

    def test_deleted_endpoint_stops_the_chain(self, endpoint, payload):
        endpoint_id = endpoint.id
        endpoint.delete()
        with patch('external_api.tasks.post_json_safely') as post:
            assert _attempt(endpoint_id, payload, 1) is None
        post.assert_not_called()
        assert not WebhookDelivery.objects.exists()


def test_webhook_post_does_not_follow_redirects():
    with resolving_to('93.184.216.34'), patch('external_api.services.requests.post') as post:
        post_json_safely('https://hooks.example.com/x', b'{}', {})
    assert post.call_args.kwargs['allow_redirects'] is False
