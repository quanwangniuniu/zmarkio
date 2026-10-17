"""
What the external layer adds on top of the reused CSM viewsets: the per-resource
overrides and relation limits. CRUD behaviour itself is the internal viewsets'
and is covered by the csm and customer tests.
"""
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import OrganizationMembership
from csm.models import CustomerUser, QuickReplyTemplateHistory, RoutingRule
from customer.models import Customer, CustomerOrganisation
from external_api.tests.conftest import _detail, _list

pytestmark = pytest.mark.django_db


def test_ticket_create_and_assignee_limited_to_workspace_csm_users(key_client, workspace, user, user2):
    created = key_client.post(_list('ticket'), {'title': 'API ticket', 'queue': workspace['queue'].id}, format='json')
    assert created.status_code == 201, created.data

    url = _detail('ticket', created.data['id'])
    assert key_client.patch(url, {'assigned_to': user2.id}, format='json').status_code == 400  # no CSM profile here
    assert key_client.patch(url, {'assigned_to': user.id}, format='json').status_code == 200


def test_conversation_requires_a_queue(key_client, workspace):
    customer = workspace['customer'].id
    missing = key_client.post(_list('conversation'), {'customer': customer}, format='json')
    assert missing.status_code == 400 and 'queue' in missing.data
    created = key_client.post(_list('conversation'), {'customer': customer, 'queue': workspace['queue'].id}, format='json')
    assert created.status_code == 201, created.data


def test_customer_create_pins_workspace_and_project(key_client, workspace, organization, project):
    response = key_client.post(_list('customer'), {
        'email': 'bob@example.com', 'full_name': 'Bob', 'organisation': workspace['customer_organisation'].id,
    }, format='json')

    assert response.status_code == 201, response.data
    customer = Customer.objects.get(pk=response.data['id'])
    assert (customer.organization_id, customer.project_id) == (organization.id, project.id)


class TestOrganisations:
    def test_create_is_pinned_to_workspace_and_makes_no_admin(self, key_client, organization):
        response = key_client.post(_list('organisation'), {'name': 'Acme', 'organization': 999999}, format='json')

        assert response.status_code == 201, response.data
        assert response.data['organization'] == organization.id
        assert 'customers' not in response.data
        assert not CustomerUser.objects.filter(organisation_id=response.data['id']).exists()

    def test_duplicate_name_in_workspace_rejected(self, key_client, workspace):
        response = key_client.post(_list('organisation'), {'name': 'test org'}, format='json')
        assert response.status_code == 400 and 'name' in response.data

    def test_delete_refused_while_customers_exist(self, key_client, workspace):
        assert key_client.delete(_detail('organisation', workspace['customer_organisation'].id)).status_code == 400
        empty = CustomerOrganisation.objects.create(name='Empty', organization=workspace['customer_organisation'].organization)
        assert key_client.delete(_detail('organisation', empty.id)).status_code == 204


def test_template_create_and_edit_record_no_person(key_client, workspace):
    created = key_client.post(_list('template'), {
        'organisation': workspace['customer_organisation'].id, 'title': 'Hi', 'content': 'Hello', 'tags': ['billing'],
    }, format='json')
    assert created.status_code == 201, created.data
    assert created.data['created_by'] is None

    assert key_client.patch(_detail('template', created.data['id']), {'content': 'Hi there'}, format='json').status_code == 200
    assert QuickReplyTemplateHistory.objects.get(template_id=created.data['id']).edited_by is None


def test_queue_requires_organisation_and_is_pinned_to_project(key_client, workspace, project):
    assert key_client.post(_list('queue'), {'name': 'Q2', 'tier': 'T2'}, format='json').status_code == 400
    created = key_client.post(_list('queue'), {
        'name': 'Q2', 'tier': 'T2', 'organisation': workspace['customer_organisation'].id,
    }, format='json')
    assert created.status_code == 201, created.data
    assert created.data['project'] == project.id


class TestAgents:
    def test_only_existing_workspace_members_and_no_new_accounts(self, key_client, workspace, user2, organization):
        payload = {'email': user2.email, 'organisation': workspace['customer_organisation'].id,
                   'queue': workspace['queue'].id, 'user_type': 'agent'}
        accounts = get_user_model().objects.count()

        refused = key_client.post(_list('agent'), {**payload, 'email': 'stranger@example.com'}, format='json')
        assert refused.status_code == 400 and 'email' in refused.data
        assert key_client.post(_list('agent'), payload, format='json').status_code == 400  # not a member yet
        assert get_user_model().objects.count() == accounts

        OrganizationMembership.objects.create(user=user2, organization=organization, role='member', is_active=True)
        created = key_client.post(_list('agent'), payload, format='json')
        assert created.status_code == 201, created.data
        assert CustomerUser.objects.get(pk=created.data['id']).user_id == user2.id

    def test_duplicate_profile_rejected(self, key_client, workspace, user2, organization):
        OrganizationMembership.objects.create(user=user2, organization=organization, role='member', is_active=True)
        payload = {'email': user2.email, 'organisation': workspace['customer_organisation'].id,
                   'queue': workspace['queue'].id, 'user_type': 'agent'}

        assert key_client.post(_list('agent'), payload, format='json').status_code == 201
        assert key_client.post(_list('agent'), payload, format='json').status_code == 400


def test_routing_rule_create_uses_credential_project_and_records_no_person(
    key_client, workspace, tenant_project, experience_group,
):
    created = key_client.post(_list('routing-rule'), {
        'experience_group': experience_group.id, 'name': 'Refunds', 'target_queue': workspace['queue'].id,
    }, format='json')

    assert created.status_code == 201, created.data
    rule = RoutingRule.objects.get(pk=created.data['id'])
    assert (rule.project_id, rule.created_by) == (tenant_project.id, None)


def test_user_only_actions_are_not_exposed(key_client, workspace):
    ticket = workspace['ticket'].id
    assert key_client.post(f"{_detail('ticket', ticket)}claim/").status_code == 404
    assert key_client.post(f"{_list('agent')}invite/", {}, format='json').status_code in (404, 405)
