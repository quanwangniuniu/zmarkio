"""Admin console: webhook endpoints and the delivery log (MED-226)."""
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from core.models import ProjectMember
from csm.models import CustomerUser
from public_api.models import WebhookDelivery, WebhookEndpoint
from public_api.tests.conftest import resolving_to
from public_api.services.webhooks import build_event, create_endpoint, endpoint_secret

pytestmark = pytest.mark.django_db

HOOKS = '/api/csm/integrations/webhooks/'
LOG = '/api/csm/integrations/webhook-deliveries/'


@pytest.fixture(autouse=True)
def public_dns():
    with resolving_to('93.184.216.34'):
        yield


@pytest.fixture
def endpoint(organization, project):
    endpoint, _ = create_endpoint(
        organization_id=organization.id, project_id=project.id,
        url='https://hooks.example.com/a', events=['ticket.created'], user=None,
    )
    return endpoint


def _rows(response):
    assert response.status_code == 200, response.data
    return response.data['results']


class TestEndpoints:
    def test_register_returns_secret_once(self, admin_client, project):
        response = admin_client.post(f'{HOOKS}?project={project.id}', {
            'url': 'https://hooks.example.com/zmarkio', 'events': ['sla.breached', 'ticket.created'],
        }, format='json')

        assert response.status_code == 201, response.data
        assert response.data['secret'].startswith('whsec_')
        assert response.data['events'] == ['ticket.created', 'sla.breached']
        endpoint = WebhookEndpoint.objects.get(pk=response.data['id'])
        assert endpoint_secret(endpoint) == response.data['secret']

        rows = _rows(admin_client.get(f'{HOOKS}?project={project.id}'))
        assert 'secret' not in rows[0]

    def test_rejects_unknown_event_and_unsafe_url(self, admin_client, project):
        bad_event = admin_client.post(f'{HOOKS}?project={project.id}', {
            'url': 'https://hooks.example.com/x', 'events': ['ticket.deleted'],
        }, format='json')
        assert bad_event.status_code == 400 and 'events' in bad_event.data

        with resolving_to('10.0.0.1'):
            private = admin_client.post(f'{HOOKS}?project={project.id}', {
                'url': 'https://internal.example.com/x', 'events': ['ticket.created'],
            }, format='json')
        assert private.status_code == 400 and 'url' in private.data

    def test_update_rotate_test_and_delete(self, admin_client, project, endpoint, django_capture_on_commit_callbacks):
        url = f'{HOOKS}{endpoint.id}/'
        old_secret = endpoint_secret(endpoint)

        patched = admin_client.patch(f'{url}?project={project.id}', {'events': ['sla.breached'], 'is_active': False}, format='json')
        assert patched.status_code == 200 and patched.data['events'] == ['sla.breached']

        rotated = admin_client.post(f'{url}rotate-secret/?project={project.id}')
        assert rotated.status_code == 200 and rotated.data['secret'] != old_secret

        assert admin_client.post(f'{url}test/?project={project.id}').status_code == 400  # inactive
        admin_client.patch(f'{url}?project={project.id}', {'is_active': True}, format='json')
        with patch('public_api.tasks.deliver_webhook.delay') as delay, \
                django_capture_on_commit_callbacks(execute=True):
            tested = admin_client.post(f'{url}test/?project={project.id}')
        assert tested.status_code == 202
        assert delay.call_args.args[1]['type'] == 'ping'

        assert admin_client.delete(f'{url}?project={project.id}').status_code == 204
        assert not WebhookEndpoint.objects.filter(pk=endpoint.id).exists()

    def test_agent_cannot_manage(self, api_client, user2, project, customer_organisation):
        ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
        CustomerUser.objects.create(user=user2, user_type='supervisor', organisation=customer_organisation, is_active=True)
        api_client.force_authenticate(user=user2)
        assert api_client.get(f'{HOOKS}?project={project.id}').status_code == 403
        assert api_client.get(f'{LOG}?project={project.id}').status_code == 403


class TestDeliveryLog:
    @pytest.fixture
    def deliveries(self, endpoint, organization, project):
        payload = build_event('ticket.created', organization_id=organization.id, project_id=project.id, data={})
        common = {'endpoint': endpoint, 'event_id': payload['id'], 'event_type': 'ticket.created',
                  'target_url': endpoint.url, 'payload': payload}
        return [
            WebhookDelivery.objects.create(attempt=1, status='retrying', response_code=500, **common),
            WebhookDelivery.objects.create(attempt=2, status='succeeded', response_code=200, **common),
        ]

    def test_lists_attempts_with_url_event_code_and_time(self, admin_client, project, endpoint, deliveries):
        rows = _rows(admin_client.get(f'{LOG}?project={project.id}'))

        assert [r['attempt'] for r in rows] == [2, 1]
        assert {r['target_url'] for r in rows} == {endpoint.url}
        assert {r['event_type'] for r in rows} == {'ticket.created'}
        assert [r['response_code'] for r in rows] == [200, 500]
        assert all(r['created_at'] for r in rows)

        failed_only = _rows(admin_client.get(f'{LOG}?project={project.id}&status=retrying'))
        assert [r['attempt'] for r in failed_only] == [1]

        listing = _rows(admin_client.get(f'{HOOKS}?project={project.id}'))
        assert listing[0]['last_delivery_status'] == 'succeeded'

    def test_redeliver_keeps_the_event_id(self, admin_client, project, deliveries, django_capture_on_commit_callbacks):
        with patch('public_api.tasks.deliver_webhook.delay') as delay, \
                django_capture_on_commit_callbacks(execute=True):
            response = admin_client.post(f'{LOG}{deliveries[0].id}/redeliver/?project={project.id}')

        assert response.status_code == 202
        assert delay.call_args.args[1]['id'] == str(deliveries[0].event_id)

    def test_other_workspace_log_is_invisible(self, admin_client, project, other_workspace):
        foreign, _ = create_endpoint(
            organization_id=other_workspace['organization'].id, project_id=project.id,
            url='https://hooks.example.com/b', events=['ticket.created'], user=None,
        )
        WebhookDelivery.objects.create(
            endpoint=foreign, event_id='00000000-0000-0000-0000-000000000001', event_type='ticket.created',
            target_url=foreign.url, payload={},
        )
        assert _rows(admin_client.get(f'{LOG}?project={project.id}')) == []
