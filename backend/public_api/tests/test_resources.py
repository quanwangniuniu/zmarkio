"""CRUD behaviour of each /api/v1/csm/ resource (MED-226)."""
import pytest

from core.models import OrganizationMembership
from csm.models import CustomerUser, QuickReplyTemplateHistory, RoutingRule, Ticket
from customer.models import Customer

pytestmark = pytest.mark.django_db

BASE = '/api/v1/csm'


class TestTickets:
    def test_create_list_and_filter(self, key_client, workspace):
        created = key_client.post(f'{BASE}/tickets/', {
            'title': 'API ticket', 'queue': workspace['queue'].id, 'priority': 'high',
        }, format='json')

        assert created.status_code == 201, created.data
        assert created.data['status'] == 'todo'
        assert created.data['status_display'] == 'To Do'

        listed = key_client.get(f'{BASE}/tickets/', {'status': 'todo', 'queue': workspace['queue'].id})
        assert {row['id'] for row in listed.data['results']} >= {created.data['id']}

    def test_status_change_follows_the_status_machine(self, key_client, workspace):
        url = f"{BASE}/tickets/{workspace['ticket'].id}/"

        assert key_client.patch(url, {'status': 'resolved'}, format='json').status_code == 400
        response = key_client.patch(url, {'status': 'in_progress'}, format='json')

        assert response.status_code == 200, response.data
        assert Ticket.objects.get(pk=workspace['ticket'].id).status == 'in_progress'

    def test_unknown_status_on_create_is_rejected(self, key_client, workspace):
        response = key_client.post(f'{BASE}/tickets/', {
            'title': 'x', 'queue': workspace['queue'].id, 'status': 'no-such-status',
        }, format='json')
        assert response.status_code == 400 and 'status' in response.data

    def test_assignee_must_be_csm_user_of_the_queue_organisation(self, key_client, workspace, user, user2):
        url = f"{BASE}/tickets/{workspace['ticket'].id}/"
        assert key_client.patch(url, {'assigned_to': user2.id}, format='json').status_code == 400
        assert key_client.patch(url, {'assigned_to': user.id}, format='json').status_code == 200

    def test_no_delete(self, key_client, workspace):
        assert key_client.delete(f"{BASE}/tickets/{workspace['ticket'].id}/").status_code == 405

    def test_updated_since_filter(self, key_client, workspace):
        assert key_client.get(f'{BASE}/tickets/', {'updated_since': 'yesterday'}).status_code == 400
        future = key_client.get(f'{BASE}/tickets/', {'updated_since': '2999-01-01T00:00:00Z'})
        assert future.data['results'] == []


class TestConversations:
    def test_create_and_patch(self, key_client, workspace):
        created = key_client.post(f'{BASE}/conversations/', {
            'queue': workspace['queue'].id, 'customer': workspace['customer'].id, 'channel': 'email',
        }, format='json')
        assert created.status_code == 201, created.data

        patched = key_client.patch(
            f"{BASE}/conversations/{created.data['id']}/",
            {'status': 'resolved', 'tags': ['refund'], 'assigned_to': workspace['agent'].id},
            format='json',
        )
        assert patched.status_code == 200, patched.data
        assert patched.data['status'] == 'resolved'

    def test_queue_is_required(self, key_client, workspace):
        response = key_client.post(f'{BASE}/conversations/', {'customer': workspace['customer'].id}, format='json')
        assert response.status_code == 400 and 'queue' in response.data

    def test_messages_are_listed_read_only(self, key_client, workspace):
        url = f"{BASE}/conversations/{workspace['conversation'].id}/messages/"

        listed = key_client.get(url)
        assert listed.status_code == 200
        assert [m['content'] for m in listed.data['results']] == ['Where is my refund?']
        assert key_client.post(url, {'content': 'hi'}, format='json').status_code == 405


class TestCustomers:
    def test_create_pins_workspace_and_project(self, key_client, workspace, organization, project):
        response = key_client.post(f'{BASE}/customers/', {
            'email': 'Bob@Example.com', 'full_name': 'Bob', 'organisation': workspace['customer_organisation'].id,
        }, format='json')

        assert response.status_code == 201, response.data
        customer = Customer.objects.get(pk=response.data['id'])
        assert (customer.organization_id, customer.project_id) == (organization.id, project.id)
        assert customer.email == 'bob@example.com'

    def test_filter_update_delete(self, key_client, workspace):
        listed = key_client.get(f'{BASE}/customers/', {'email': 'ALICE@example.com'})
        assert [row['id'] for row in listed.data['results']] == [workspace['customer'].id]

        url = f"{BASE}/customers/{workspace['customer'].id}/"
        assert key_client.patch(url, {'phone': '123'}, format='json').status_code == 200
        assert key_client.delete(url).status_code == 204
        assert not Customer.objects.filter(pk=workspace['customer'].id).exists()

    def test_duplicate_email_in_project_rejected(self, key_client, workspace):
        response = key_client.post(f'{BASE}/customers/', {'email': 'alice@example.com', 'full_name': 'A'}, format='json')
        assert response.status_code == 400 and 'email' in response.data


class TestOrganisations:
    def test_create_is_pinned_to_workspace(self, key_client, organization):
        response = key_client.post(
            f'{BASE}/organisations/', {'name': 'Acme', 'organization': 999999}, format='json',
        )

        assert response.status_code == 201, response.data
        assert response.data['organization'] == organization.id
        assert 'customers' not in response.data

    def test_delete_refused_while_customers_exist(self, key_client, workspace):
        url = f"{BASE}/organisations/{workspace['customer_organisation'].id}/"
        assert key_client.delete(url).status_code == 400

    def test_duplicate_name_rejected(self, key_client, workspace):
        response = key_client.post(f'{BASE}/organisations/', {'name': 'test org'}, format='json')
        assert response.status_code == 400 and 'name' in response.data


class TestTemplates:
    def test_create_update_writes_history_and_soft_delete(self, key_client, workspace):
        created = key_client.post(f'{BASE}/templates/', {
            'organisation': workspace['customer_organisation'].id, 'title': 'Hi', 'content': 'Hello', 'tags': ['billing'],
        }, format='json')
        assert created.status_code == 201, created.data
        assert created.data['created_by'] is None

        url = f"{BASE}/templates/{created.data['id']}/"
        assert key_client.patch(url, {'content': 'Hello there'}, format='json').status_code == 200
        history = QuickReplyTemplateHistory.objects.get(template_id=created.data['id'])
        assert history.content == 'Hello' and history.edited_by is None

        assert key_client.delete(url).status_code == 204
        assert key_client.get(url).data['is_active'] is False

    def test_unknown_tag_rejected(self, key_client, workspace):
        response = key_client.post(f'{BASE}/templates/', {
            'organisation': workspace['customer_organisation'].id, 'title': 'x', 'content': 'y', 'tags': ['nope'],
        }, format='json')
        assert response.status_code == 400 and 'tags' in response.data


class TestQueues:
    def test_create_requires_organisation_and_soft_deletes(self, key_client, workspace, project):
        assert key_client.post(f'{BASE}/queues/', {'name': 'Q2', 'tier': 'T2'}, format='json').status_code == 400

        created = key_client.post(f'{BASE}/queues/', {
            'name': 'Q2', 'tier': 'T2', 'organisation': workspace['customer_organisation'].id, 'project': 424242,
        }, format='json')
        assert created.status_code == 201, created.data
        assert created.data['project'] == project.id

        url = f"{BASE}/queues/{created.data['id']}/"
        assert key_client.delete(url).status_code == 204
        assert key_client.get(url).data['is_active'] is False


class TestAgents:
    def test_create_for_existing_workspace_member_only(self, key_client, workspace, user2, organization):
        payload = {
            'email': user2.email, 'organisation': workspace['customer_organisation'].id,
            'queue': workspace['queue'].id, 'user_type': 'supervisor',
        }
        refused = key_client.post(f'{BASE}/agents/', payload, format='json')
        assert refused.status_code == 400 and 'email' in refused.data

        OrganizationMembership.objects.create(user=user2, organization=organization, role='member', is_active=True)
        created = key_client.post(f'{BASE}/agents/', payload, format='json')
        assert created.status_code == 201, created.data
        assert CustomerUser.objects.get(pk=created.data['id']).user_id == user2.id

    def test_queue_is_optional_and_duplicates_rejected(self, key_client, workspace, user2, organization):
        OrganizationMembership.objects.create(user=user2, organization=organization, role='member', is_active=True)
        payload = {'email': user2.email, 'organisation': workspace['customer_organisation'].id, 'user_type': 'admin'}

        created = key_client.post(f'{BASE}/agents/', payload, format='json')
        assert created.status_code == 201, created.data
        assert created.data['queue'] is None
        duplicate = key_client.post(f'{BASE}/agents/', payload, format='json')
        assert duplicate.status_code == 400

    def test_never_creates_accounts(self, key_client, workspace):
        from django.contrib.auth import get_user_model
        before = get_user_model().objects.count()

        response = key_client.post(f'{BASE}/agents/', {
            'email': 'stranger@example.com', 'organisation': workspace['customer_organisation'].id,
        }, format='json')

        assert response.status_code == 400
        assert get_user_model().objects.count() == before

    def test_creator_cannot_be_deleted(self, key_client, workspace):
        agent = workspace['agent']
        agent.is_creator = True
        agent.save(update_fields=['is_creator'])
        assert key_client.delete(f'{BASE}/agents/{agent.id}/').status_code == 400


class TestRoutingRules:
    def test_create_update_reorder_delete(self, key_client, workspace, tenant_project, experience_group):
        url = f'{BASE}/routing-rules/'
        first = key_client.post(url, {
            'experience_group': experience_group.id, 'name': 'Refunds', 'target_queue': workspace['queue'].id,
            'conditions': [{'field': 'latest_message', 'operator': 'contains_any', 'value': ['refund']}],
        }, format='json')
        assert first.status_code == 201, first.data
        second = key_client.post(url, {
            'experience_group': experience_group.id, 'name': 'Everything else', 'target_queue': workspace['queue'].id,
        }, format='json')
        assert second.status_code == 201, second.data
        assert RoutingRule.objects.get(pk=first.data['id']).created_by is None

        patched = key_client.patch(f"{url}{first.data['id']}/", {'is_enabled': False}, format='json')
        assert patched.status_code == 200 and patched.data['is_enabled'] is False

        reordered = key_client.put(f'{url}reorder/', {
            'experience_group': experience_group.id, 'ids': [second.data['id'], first.data['id']],
        }, format='json')
        assert reordered.status_code == 200, reordered.data
        assert [r['id'] for r in reordered.data] == [second.data['id'], first.data['id']]

        assert key_client.delete(f"{url}{first.data['id']}/").status_code == 204
        assert not RoutingRule.objects.filter(pk=first.data['id']).exists()

    def test_invalid_condition_is_400(self, key_client, workspace, tenant_project, experience_group):
        response = key_client.post(f'{BASE}/routing-rules/', {
            'experience_group': experience_group.id, 'name': 'Bad', 'target_queue': workspace['queue'].id,
            'conditions': [{'field': 'nope', 'operator': 'eq', 'value': 'x'}],
        }, format='json')
        assert response.status_code == 400
