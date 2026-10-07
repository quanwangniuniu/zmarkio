"""
Scopes (403) and workspace isolation for /api/v1/csm/ (MED-226).

The isolation cases give a second workspace a credential for the SAME project
id: project ids repeat across organisation schemas, so scoping by project id
alone would leak every row below.
"""
import pytest

from csm.models import RoutingRule
from public_api.tests.conftest import client_for

pytestmark = pytest.mark.django_db

BASE = '/api/v1/csm'
RESOURCES = ['customers', 'organisations', 'tickets', 'conversations', 'templates', 'routing-rules', 'queues', 'agents']


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
        response = client_for(raw).get(f'{BASE}/tickets/')
        assert response.status_code == 403

    def test_read_scope_cannot_write(self, make_key, workspace):
        _, raw = make_key(scopes=['tickets:read'])
        client = client_for(raw)

        assert client.get(f'{BASE}/tickets/').status_code == 200
        response = client.post(f'{BASE}/tickets/', {'title': 'x', 'queue': workspace['queue'].id}, format='json')
        assert response.status_code == 403

    def test_write_scope_implies_read(self, make_key, workspace):
        _, raw = make_key(scopes=['tickets:write'])
        assert client_for(raw).get(f'{BASE}/tickets/').status_code == 200

    @pytest.mark.parametrize('resource', RESOURCES)
    def test_every_resource_requires_a_credential(self, api_client, resource):
        response = api_client.get(f'{BASE}/{resource}/')
        assert response.status_code == 401

    def test_user_jwt_is_not_a_credential(self, api_client):
        # Only API keys and toolkit tokens authenticate here; a user's JWT is just an unknown bearer.
        response = api_client.get(f'{BASE}/tickets/', HTTP_AUTHORIZATION='Bearer some.jwt.token')
        assert response.status_code == 401


class TestIsolation:
    @pytest.mark.parametrize('resource', RESOURCES)
    def test_own_workspace_sees_its_rows(self, key_client, workspace, rule, resource):
        assert _ids(key_client.get(f'{BASE}/{resource}/'))

    @pytest.mark.parametrize('resource', RESOURCES)
    def test_other_workspace_with_same_project_id_sees_none_of_ours(
        self, key_client, intruder, workspace, rule, resource,
    ):
        ours = set(_ids(key_client.get(f'{BASE}/{resource}/')))
        assert ours
        assert ours.isdisjoint(_ids(intruder.get(f'{BASE}/{resource}/')))

    @pytest.mark.parametrize('resource,key', [
        ('tickets', 'ticket'), ('conversations', 'conversation'), ('customers', 'customer'),
        ('templates', 'template'), ('queues', 'queue'), ('agents', 'agent'),
        ('organisations', 'customer_organisation'),
    ])
    def test_other_workspace_cannot_read_or_change_by_id(self, intruder, workspace, resource, key):
        pk = workspace[key].id
        assert intruder.get(f'{BASE}/{resource}/{pk}/').status_code == 404
        assert intruder.patch(f'{BASE}/{resource}/{pk}/', {}, format='json').status_code == 404

    def test_other_workspace_cannot_reach_routing_rule(self, intruder, rule):
        assert intruder.get(f'{BASE}/routing-rules/{rule.id}/').status_code == 404
        assert intruder.delete(f'{BASE}/routing-rules/{rule.id}/').status_code == 404

    def test_messages_of_other_workspace_are_404(self, intruder, workspace):
        assert intruder.get(f"{BASE}/conversations/{workspace['conversation'].id}/messages/").status_code == 404

    def test_writes_cannot_reference_another_workspaces_rows(self, intruder, workspace, experience_group):
        queue = workspace['queue'].id
        cases = [
            ('tickets', {'title': 'x', 'queue': queue}, 'queue'),
            ('conversations', {'queue': queue}, 'queue'),
            ('queues', {'name': 'Q', 'tier': 'T1', 'organisation': workspace['customer_organisation'].id}, 'organisation'),
            ('customers', {'email': 'e@x.test', 'full_name': 'E', 'organisation': workspace['customer_organisation'].id}, 'organisation'),
            ('templates', {'title': 't', 'content': 'c', 'tags': ['billing'], 'organisation': workspace['customer_organisation'].id}, 'organisation'),
        ]
        for resource, payload, field in cases:
            response = intruder.post(f'{BASE}/{resource}/', payload, format='json')
            assert response.status_code == 400, (resource, response.data)
            assert field in response.data, (resource, response.data)
