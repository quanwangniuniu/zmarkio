"""Admin console (/api/csm/integrations/): credentials, webhook endpoints and the delivery log."""
import pytest
from django.urls import reverse

from core.crypto import decrypt_token
from core.models import OrganizationMembership, ProjectMember
from csm.models import CustomerUser
from public_api.models import ApiKey, OAuthClient, WebhookDelivery, WebhookEndpoint
from public_api.services import create_endpoint
from public_api.tests.conftest import client_for, resolving_to

pytestmark = pytest.mark.django_db

def _keys(project):
    return f"{reverse('integrations-api-key-list')}?project={project.id}"


@pytest.fixture
def agent(user2, project, customer_organisation):
    ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
    CustomerUser.objects.create(user=user2, user_type='agent', organisation=customer_organisation, is_active=True)
    return user2


class TestApiKeys:
    def test_csm_admin_creates_key_and_sees_it_once(self, admin_client, project, organization):
        response = admin_client.post(
            _keys(project), {'name': 'Zapier', 'scopes': ['tickets:write', 'tickets:read', 'tickets:read']},
            format='json',
        )

        assert response.status_code == 201, response.data
        assert response.data['key'].startswith('zmk_')
        assert response.data['scopes'] == ['tickets:read', 'tickets:write']
        key = ApiKey.objects.get(pk=response.data['id'])
        assert (key.organization_id, key.project_id) == (organization.id, project.id)

        listing = admin_client.get(_keys(project))
        assert listing.status_code == 200
        assert [row['id'] for row in listing.data] == [key.id]
        assert 'key' not in listing.data[0]

    def test_rejects_unknown_scope_and_empty_scopes(self, admin_client, project):
        bad = admin_client.post(_keys(project), {'name': 'x', 'scopes': ['tickets:delete']}, format='json')
        empty = admin_client.post(_keys(project), {'name': 'x', 'scopes': []}, format='json')
        assert bad.status_code == 400 and 'scopes' in bad.data
        assert empty.status_code == 400 and 'scopes' in empty.data

    def test_revoke(self, admin_client, project, make_key):
        key, raw = make_key()

        response = admin_client.post(
            f"{reverse('integrations-api-key-revoke', args=[key.id])}?project={project.id}",
        )

        assert response.status_code == 200
        assert response.data['is_active'] is False
        assert client_for(raw).get(reverse('public-api-ticket-list')).status_code == 401

    def test_keys_of_another_workspace_are_invisible(self, admin_client, project, make_key, other_workspace):
        make_key(organization_id=other_workspace['organization'].id, project_id=project.id)
        assert admin_client.get(_keys(project)).data == []

    def test_org_admin_may_manage(self, api_client, user2, project, organization):
        ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
        OrganizationMembership.objects.create(user=user2, organization=organization, role='admin', is_active=True)
        api_client.force_authenticate(user=user2)

        response = api_client.post(_keys(project), {'name': 'x', 'scopes': ['queues:read']}, format='json')

        assert response.status_code == 201

    def test_agent_is_forbidden(self, api_client, agent, project):
        api_client.force_authenticate(user=agent)
        assert api_client.get(_keys(project)).status_code == 403

    def test_csm_admin_of_another_workspace_is_forbidden(self, api_client, user2, project, other_workspace):
        ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
        CustomerUser.objects.create(
            user=user2, user_type='admin', organisation=other_workspace['customer_organisation'], is_active=True,
        )
        api_client.force_authenticate(user=user2)
        assert api_client.get(_keys(project)).status_code == 403

    def test_anonymous_is_rejected(self, api_client, project):
        assert api_client.get(_keys(project)).status_code in (401, 403)


def test_oauth_client_secret_shown_once_and_revoke_deletes_application(admin_client, project):
    url = f"{reverse('integrations-oauth-client-list')}?project={project.id}"
    response = admin_client.post(url, {'name': 'CRM', 'scopes': ['customers:read']}, format='json')

    assert response.status_code == 201, response.data
    assert response.data['client_secret']
    client = OAuthClient.objects.get(pk=response.data['id'])
    assert client.application.client_id == response.data['client_id']
    assert 'client_secret' not in admin_client.get(url).data[0]

    revoked = admin_client.post(f"{reverse('integrations-oauth-client-revoke', args=[client.id])}?project={project.id}")
    assert revoked.status_code == 200
    client.refresh_from_db()
    assert client.application is None and client.revoked_at is not None


def test_vocabulary_lists_resources_and_events(admin_client, project):
    response = admin_client.get(f"{reverse('integrations-vocabulary')}?project={project.id}")

    assert response.status_code == 200
    assert {'value': 'routing_rules', 'label': 'Routing rules'} in response.data['resources']
    assert {'value': 'sla.breached', 'label': 'SLA breached'} in response.data['events']


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

    def test_delete_and_no_edit(self, admin_client, project, endpoint):
        detail = _url('integrations-webhook-detail', project, endpoint.id)

        assert admin_client.patch(detail, {'events': ['sla.breached']}, format='json').status_code == 405
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
