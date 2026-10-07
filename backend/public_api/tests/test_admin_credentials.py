"""Admin console: create, list and revoke API keys and OAuth clients (MED-226)."""
import pytest
from rest_framework.test import APIClient

from core.models import OrganizationMembership, ProjectMember
from csm.models import CustomerUser
from public_api.models import ApiKey, OAuthClient

pytestmark = pytest.mark.django_db

KEYS = '/api/csm/integrations/api-keys/'
CLIENTS = '/api/csm/integrations/oauth-clients/'
VOCABULARY = '/api/csm/integrations/vocabulary/'


def _as(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def agent(user2, project, customer_organisation):
    ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
    CustomerUser.objects.create(user=user2, user_type='agent', organisation=customer_organisation, is_active=True)
    return user2


class TestApiKeys:
    def test_csm_admin_creates_key_and_sees_it_once(self, admin_client, project, organization):
        response = admin_client.post(
            f'{KEYS}?project={project.id}',
            {'name': 'Zapier', 'scopes': ['tickets:write', 'tickets:read', 'tickets:read']},
            format='json',
        )

        assert response.status_code == 201, response.data
        assert response.data['key'].startswith(f"zmk_{response.data['prefix']}_")
        assert response.data['scopes'] == ['tickets:read', 'tickets:write']
        key = ApiKey.objects.get(pk=response.data['id'])
        assert (key.organization_id, key.project_id) == (organization.id, project.id)

        listing = admin_client.get(f'{KEYS}?project={project.id}')
        assert listing.status_code == 200
        rows = listing.data['results'] if isinstance(listing.data, dict) else listing.data
        assert [row['id'] for row in rows] == [key.id]
        assert 'key' not in rows[0]

    def test_rejects_unknown_scope_and_empty_scopes(self, admin_client, project):
        bad = admin_client.post(f'{KEYS}?project={project.id}', {'name': 'x', 'scopes': ['tickets:delete']}, format='json')
        empty = admin_client.post(f'{KEYS}?project={project.id}', {'name': 'x', 'scopes': []}, format='json')
        assert bad.status_code == 400 and 'scopes' in bad.data
        assert empty.status_code == 400 and 'scopes' in empty.data

    def test_revoke(self, admin_client, project, make_key):
        key, raw = make_key()

        response = admin_client.post(f'{KEYS}{key.id}/revoke/?project={project.id}')

        assert response.status_code == 200
        assert response.data['is_active'] is False
        whoami = APIClient()
        whoami.credentials(HTTP_X_API_KEY=raw)
        assert whoami.get('/api/v1/csm/whoami/').status_code == 401

    def test_keys_of_another_workspace_are_invisible(self, admin_client, project, make_key, other_workspace):
        make_key(organization_id=other_workspace['organization'].id, project_id=project.id)

        listing = admin_client.get(f'{KEYS}?project={project.id}')

        rows = listing.data['results'] if isinstance(listing.data, dict) else listing.data
        assert rows == []

    def test_org_admin_may_manage(self, user2, project, organization):
        ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
        OrganizationMembership.objects.create(user=user2, organization=organization, role='admin', is_active=True)

        response = _as(user2).post(f'{KEYS}?project={project.id}', {'name': 'x', 'scopes': ['queues:read']}, format='json')

        assert response.status_code == 201

    def test_agent_is_forbidden(self, agent, project):
        assert _as(agent).get(f'{KEYS}?project={project.id}').status_code == 403

    def test_csm_admin_of_another_workspace_is_forbidden(self, user2, project, other_workspace):
        ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
        CustomerUser.objects.create(
            user=user2, user_type='admin', organisation=other_workspace['customer_organisation'], is_active=True,
        )

        assert _as(user2).get(f'{KEYS}?project={project.id}').status_code == 403

    def test_anonymous_is_rejected(self, project):
        assert APIClient().get(f'{KEYS}?project={project.id}').status_code in (401, 403)


class TestOAuthClients:
    def test_create_returns_secret_once_and_revoke_deletes_application(self, admin_client, project):
        response = admin_client.post(
            f'{CLIENTS}?project={project.id}', {'name': 'CRM', 'scopes': ['customers:read']}, format='json',
        )

        assert response.status_code == 201, response.data
        assert response.data['client_secret']
        client = OAuthClient.objects.get(pk=response.data['id'])
        assert client.application.client_id == response.data['client_id']

        listing = admin_client.get(f'{CLIENTS}?project={project.id}')
        rows = listing.data['results'] if isinstance(listing.data, dict) else listing.data
        assert 'client_secret' not in rows[0]

        revoked = admin_client.post(f'{CLIENTS}{client.id}/revoke/?project={project.id}')
        assert revoked.status_code == 200
        client.refresh_from_db()
        assert client.application is None and client.revoked_at is not None


def test_vocabulary_lists_scopes(admin_client, project):
    response = admin_client.get(f'{VOCABULARY}?project={project.id}')

    assert response.status_code == 200
    assert 'routing_rules:write' in response.data['scopes']
