"""Admin console: webhook endpoints and the delivery log."""
import pytest
from django.urls import reverse

from core.crypto import decrypt_token
from core.models import ProjectMember
from csm.models import CustomerUser
from public_api.models import WebhookDelivery, WebhookEndpoint
from public_api.services.webhooks import create_endpoint
from public_api.tests.conftest import resolving_to

pytestmark = pytest.mark.django_db


def _url(name, project, *args):
    return f"{reverse(name, args=args)}?project={project.id}"


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


class TestEndpoints:
    def test_register_returns_secret_once(self, admin_client, project):
        response = admin_client.post(_url('integrations-webhook-list', project), {
            'url': 'https://hooks.example.com/zmarkio', 'events': ['sla.breached', 'ticket.created'],
        }, format='json')

        assert response.status_code == 201, response.data
        assert response.data['secret'].startswith('whsec_')
        assert response.data['events'] == ['ticket.created', 'sla.breached']
        endpoint = WebhookEndpoint.objects.get(pk=response.data['id'])
        assert decrypt_token(endpoint.secret_encrypted) == response.data['secret']

        listing = admin_client.get(_url('integrations-webhook-list', project))
        assert [row['id'] for row in listing.data] == [endpoint.id]
        assert 'secret' not in listing.data[0]

    def test_rejects_unknown_event_unsafe_url_and_http(self, admin_client, project):
        url = _url('integrations-webhook-list', project)
        bad_event = admin_client.post(url, {'url': 'https://hooks.example.com/x', 'events': ['ticket.deleted']}, format='json')
        assert bad_event.status_code == 400 and 'events' in bad_event.data

        plain_http = admin_client.post(url, {'url': 'http://hooks.example.com/x', 'events': ['ticket.created']}, format='json')
        assert plain_http.status_code == 400 and 'url' in plain_http.data

        with resolving_to('10.0.0.1'):
            private = admin_client.post(url, {'url': 'https://internal.example.com/x', 'events': ['ticket.created']}, format='json')
        assert private.status_code == 400 and 'url' in private.data

    def test_update_and_delete(self, admin_client, project, endpoint):
        detail = _url('integrations-webhook-detail', project, endpoint.id)

        patched = admin_client.patch(detail, {'events': ['sla.breached'], 'is_active': False}, format='json')
        assert patched.status_code == 200
        assert (patched.data['events'], patched.data['is_active']) == (['sla.breached'], False)

        assert admin_client.delete(detail).status_code == 204
        assert not WebhookEndpoint.objects.filter(pk=endpoint.id).exists()

    def test_supervisor_cannot_manage(self, api_client, user2, project, customer_organisation):
        ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
        CustomerUser.objects.create(user=user2, user_type='supervisor', organisation=customer_organisation, is_active=True)
        api_client.force_authenticate(user=user2)
        assert api_client.get(_url('integrations-webhook-list', project)).status_code == 403
        assert api_client.get(_url('integrations-webhook-delivery-list', project)).status_code == 403


class TestDeliveryLog:
    def _attempt(self, endpoint, attempt, status, code):
        return WebhookDelivery.objects.create(
            endpoint=endpoint, event_id='00000000-0000-0000-0000-000000000001', event_type='ticket.created',
            target_url=endpoint.url, attempt=attempt, status=status, response_code=code,
        )

    def test_lists_attempts_with_url_event_code_and_time(self, admin_client, project, endpoint, organization):
        self._attempt(endpoint, 1, 'retrying', 500)
        self._attempt(endpoint, 2, 'succeeded', 200)
        other, _ = create_endpoint(
            organization_id=organization.id, project_id=project.id,
            url='https://hooks.example.com/b', events=['ticket.created'], user=None,
        )
        self._attempt(other, 1, 'succeeded', 204)

        rows = admin_client.get(_url('integrations-webhook-delivery-list', project)).data['results']
        assert [(r['target_url'], r['attempt'], r['response_code']) for r in rows] == [
            (other.url, 1, 204), (endpoint.url, 2, 200), (endpoint.url, 1, 500),
        ]
        assert all(r['created_at'] and r['event_type'] == 'ticket.created' for r in rows)

    def test_other_workspace_log_is_invisible(self, admin_client, project, other_workspace):
        foreign, _ = create_endpoint(
            organization_id=other_workspace['organization'].id, project_id=project.id,
            url='https://hooks.example.com/b', events=['ticket.created'], user=None,
        )
        self._attempt(foreign, 1, 'failed', 500)
        assert admin_client.get(_url('integrations-webhook-delivery-list', project)).data['results'] == []
