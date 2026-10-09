"""
Who may call /api/v1/csm/: API key and OAuth 2.0 authentication (401), scopes
(403), and workspace isolation.

The isolation cases give a second workspace a credential for the SAME project
id: project ids repeat across organisation schemas, so scoping by project id
alone would leak every row.
"""
import pytest
from django.urls import reverse

from csm.models import RoutingRule
from public_api.services import revoke_api_key, revoke_oauth_client
from public_api.tests.conftest import client_for

pytestmark = pytest.mark.django_db

TICKETS = reverse('public-api-ticket-list')


def _token(api_client, client_id, secret):
    return api_client.post(reverse('public-api-oauth-token'), {
        'grant_type': 'client_credentials', 'client_id': client_id, 'client_secret': secret,
    })


class TestApiKey:
    def test_valid_key_authenticates(self, key_client):
        assert key_client.get(TICKETS).status_code == 200

    def test_missing_credential_is_401_with_challenge(self, api_client):
        response = api_client.get(TICKETS)

        assert response.status_code == 401
        assert response['WWW-Authenticate'] == 'Api-Key realm="api"'

    @pytest.mark.parametrize('raw', ['nonsense', 'zmk_deadbeef_wrong', 'zmk__', 'Bearer x'])
    def test_malformed_or_unknown_key_is_401(self, raw):
        assert client_for(raw).get(TICKETS).status_code == 401

    def test_wrong_secret_for_real_prefix_is_401(self, api_key):
        key, _ = api_key
        assert client_for(f'zmk_{key.prefix}_not-the-secret').get(TICKETS).status_code == 401

    def test_revoked_key_is_401(self, api_key):
        revoke_api_key(api_key[0])
        assert client_for(api_key[1]).get(TICKETS).status_code == 401

    def test_inactive_workspace_is_401(self, api_key, organization):
        organization.is_active = False
        organization.save(update_fields=['is_active'])
        assert client_for(api_key[1]).get(TICKETS).status_code == 401

    def test_only_a_hash_of_the_secret_is_stored(self, api_key):
        key, raw = api_key
        assert raw.split('_', 2)[2] not in key.key_hash


class TestOAuthClient:
    def test_client_credentials_token_authenticates(self, api_client, oauth_client):
        client, secret = oauth_client
        token_response = _token(api_client, client.client_id, secret)

        assert token_response.status_code == 200, token_response.content
        body = token_response.json()
        assert body['token_type'] == 'Bearer'
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access_token']}")
        assert api_client.get(TICKETS).status_code == 200

    def test_wrong_secret_gets_no_token(self, api_client, oauth_client):
        assert _token(api_client, oauth_client[0].client_id, 'wrong').status_code == 401

    def test_unknown_bearer_is_401(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION='Bearer not-a-token')
        assert api_client.get(TICKETS).status_code == 401

    def test_revoke_invalidates_issued_tokens_and_blocks_new_ones(self, api_client, oauth_client):
        client, secret = oauth_client
        access_token = _token(api_client, client.client_id, secret).json()['access_token']

        revoke_oauth_client(client)

        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        assert api_client.get(TICKETS).status_code == 401
        api_client.credentials()
        assert _token(api_client, client.client_id, secret).status_code == 401


RESOURCES = ['customer', 'organisation', 'ticket', 'conversation', 'template', 'routing-rule', 'queue', 'agent']


def _list(resource):
    return reverse(f'public-api-{resource}-list')


def _detail(resource, pk):
    return reverse(f'public-api-{resource}-detail', args=[pk])


def _ids(response):
    assert response.status_code == 200, response.data
    return [row['id'] for row in response.data['results']]


@pytest.fixture
def rule(workspace, tenant_project, organization, experience_group):
    return RoutingRule.objects.create(
        organization=organization, project_id=tenant_project.id, experience_group=experience_group,
        name='Refunds', target_queue=workspace['queue'],
    )


@pytest.fixture
def intruder(make_key, other_workspace, project):
    """A credential of another workspace whose project id equals ours."""
    _, raw = make_key(organization_id=other_workspace['organization'].id, project_id=project.id)
    return client_for(raw)


class TestScopes:
    def test_missing_resource_scope_is_403(self, make_key, workspace):
        _, raw = make_key(scopes=['queues:read'])
        assert client_for(raw).get(_list('ticket')).status_code == 403

    def test_read_scope_cannot_write(self, make_key, workspace):
        _, raw = make_key(scopes=['tickets:read'])
        client = client_for(raw)

        assert client.get(_list('ticket')).status_code == 200
        response = client.post(_list('ticket'), {'title': 'x', 'queue': workspace['queue'].id}, format='json')
        assert response.status_code == 403

    def test_write_scope_implies_read(self, make_key, workspace):
        _, raw = make_key(scopes=['tickets:write'])
        assert client_for(raw).get(_list('ticket')).status_code == 200

    @pytest.mark.parametrize('resource', RESOURCES)
    def test_every_resource_requires_a_credential(self, api_client, resource):
        assert api_client.get(_list(resource)).status_code == 401

    def test_user_jwt_is_not_a_credential(self, api_client):
        # Only API keys and toolkit tokens authenticate here; a user's JWT is just an unknown bearer.
        response = api_client.get(_list('ticket'), HTTP_AUTHORIZATION='Bearer some.jwt.token')
        assert response.status_code == 401


class TestIsolation:
    @pytest.mark.parametrize('resource', RESOURCES)
    def test_other_workspace_with_same_project_id_sees_none_of_ours(
        self, key_client, intruder, workspace, rule, resource,
    ):
        ours = set(_ids(key_client.get(_list(resource))))
        assert ours
        assert ours.isdisjoint(_ids(intruder.get(_list(resource))))

    @pytest.mark.parametrize('resource,key', [
        ('ticket', 'ticket'), ('conversation', 'conversation'), ('customer', 'customer'),
        ('template', 'template'), ('queue', 'queue'), ('agent', 'agent'),
        ('organisation', 'customer_organisation'),
    ])
    def test_other_workspace_cannot_read_or_change_by_id(self, intruder, workspace, resource, key):
        url = _detail(resource, workspace[key].id)
        assert intruder.get(url).status_code == 404
        assert intruder.patch(url, {}, format='json').status_code == 404

    def test_other_workspace_cannot_reach_routing_rule(self, intruder, rule):
        assert intruder.get(_detail('routing-rule', rule.id)).status_code == 404
        assert intruder.delete(_detail('routing-rule', rule.id)).status_code == 404

    def test_writes_cannot_reference_another_workspaces_rows(self, intruder, workspace):
        queue = workspace['queue'].id
        organisation = workspace['customer_organisation'].id
        cases = [
            ('ticket', {'title': 'x', 'queue': queue}, 'queue'),
            ('conversation', {'queue': queue}, 'queue'),
            ('queue', {'name': 'Q', 'tier': 'T1', 'organisation': organisation}, 'organisation'),
            ('customer', {'email': 'e@x.test', 'full_name': 'E', 'organisation': organisation}, 'organisation'),
            ('template', {'title': 't', 'content': 'c', 'tags': ['billing'], 'organisation': organisation}, 'organisation'),
        ]
        for resource, payload, field in cases:
            response = intruder.post(_list(resource), payload, format='json')
            assert response.status_code == 400, (resource, response.data)
            assert field in response.data, (resource, response.data)
