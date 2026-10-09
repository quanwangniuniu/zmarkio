"""
Scopes (403) and workspace isolation for /api/v1/csm/.

The isolation cases give a second workspace a credential for the SAME project
id: project ids repeat across organisation schemas, so scoping by project id
alone would leak every row below.
"""
import pytest
from django.urls import reverse

from csm.models import RoutingRule
from public_api.tests.conftest import client_for

pytestmark = pytest.mark.django_db

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
