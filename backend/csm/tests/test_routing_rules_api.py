"""Routing rule CRUD API tests (CSM-S03-05)."""

import pytest
from django.urls import reverse
from rest_framework import status

from core.models import Project, ProjectMember
from csm.models import CustomerUser, Queue, RoutingRule, SupportChannel
from experience_group.models import ExperienceGroup

pytestmark = pytest.mark.django_db


def _list_url(project_id, **params):
    query = '&'.join([f'project={project_id}'] + [f'{k}={v}' for k, v in params.items()])
    return reverse('routing-rule-list') + f'?{query}'


def _detail_url(pk):
    return reverse('routing-rule-detail', kwargs={'pk': pk})


def _reorder_url(project_id):
    return reverse('routing-rule-reorder') + f'?project={project_id}'


@pytest.fixture
def csm_admin_client(api_client, user, customer_organisation):
    CustomerUser.objects.get_or_create(
        user=user,
        organisation=customer_organisation,
        defaults={'user_type': 'admin', 'is_active': True},
    )
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def tech_queue(project, customer_organisation):
    return Queue.objects.create(
        project=project, organisation=customer_organisation, name='Tech', tier='T2',
    )


@pytest.fixture
def other_project(organization, user):
    return Project.objects.create(
        name='Other Project', organization=organization, owner=user,
        objectives=['awareness'], kpis={},
    )


@pytest.fixture
def channel(project, csm_queue):
    return SupportChannel.objects.create(
        project=project, channel_type='live_chat', display_name='Web chat',
        default_queue=csm_queue,
    )


def _payload(experience_group, queue, **overrides):
    payload = {
        'experience_group': experience_group.id,
        'name': 'Refunds',
        'target_queue': queue.id,
        'conditions': [
            {'field': 'latest_message', 'operator': 'contains_any', 'value': ['refund', ' Refund ']},
        ],
        'add_tags': ['billing'],
    }
    payload.update(overrides)
    return payload


def _create(client, project, experience_group, queue, **overrides):
    return client.post(
        _list_url(project.id), _payload(experience_group, queue, **overrides), format='json',
    )


class TestCreate:
    def test_creates_rule_with_normalised_conditions(self, csm_admin_client, project, experience_group, csm_queue, user):
        res = _create(csm_admin_client, project, experience_group, csm_queue)
        assert res.status_code == status.HTTP_201_CREATED, res.data
        assert res.data['position'] == 0
        assert res.data['match_mode'] == 'all'
        assert res.data['is_enabled'] is True
        assert res.data['target_queue_name'] == 'Frontline'
        # duplicate keyword (case/whitespace) collapsed
        assert res.data['conditions'] == [
            {'field': 'latest_message', 'operator': 'contains_any', 'value': ['refund']},
        ]
        assert RoutingRule.objects.get().created_by == user

    def test_new_rules_are_appended(self, csm_admin_client, project, experience_group, csm_queue):
        _create(csm_admin_client, project, experience_group, csm_queue, name='A')
        res = _create(csm_admin_client, project, experience_group, csm_queue, name='B')
        assert res.data['position'] == 1

    def test_duplicate_name_in_group_is_rejected(self, csm_admin_client, project, experience_group, csm_queue):
        _create(csm_admin_client, project, experience_group, csm_queue, name='Refunds')
        res = _create(csm_admin_client, project, experience_group, csm_queue, name='refunds')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'name' in res.data

    @pytest.mark.parametrize('conditions,fragment', [
        ([{'field': 'priority', 'operator': 'equals', 'value': 'x'}], "unknown field"),
        ([{'field': 'subject', 'operator': 'gte', 'value': 1}], "not valid"),
        ([{'field': 'latest_message', 'operator': 'contains_any', 'value': []}], 'at least one keyword'),
        ([{'field': 'message_count', 'operator': 'gte', 'value': 'two'}], 'whole number'),
        ([{'field': 'channel_status', 'operator': 'equals', 'value': 'busy'}], 'online, offline'),
        ([{'field': 'channel_type', 'operator': 'in', 'value': ['sms']}], 'channel types'),
    ])
    def test_invalid_conditions_are_reported_per_row(
        self, csm_admin_client, project, experience_group, csm_queue, conditions, fragment,
    ):
        res = _create(csm_admin_client, project, experience_group, csm_queue, conditions=conditions)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        message = res.data['conditions'][0]
        assert message.startswith('Condition 1:')
        assert fragment in message

    def test_too_many_conditions(self, csm_admin_client, project, experience_group, csm_queue):
        conditions = [{'field': 'message_count', 'operator': 'gte', 'value': 1}] * 11
        res = _create(csm_admin_client, project, experience_group, csm_queue, conditions=conditions)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'At most 10' in str(res.data['conditions'])

    def test_channel_from_another_project_is_rejected(
        self, csm_admin_client, project, other_project, experience_group, csm_queue,
    ):
        foreign = SupportChannel.objects.create(
            project=other_project, channel_type='email', display_name='Other',
        )
        res = _create(csm_admin_client, project, experience_group, csm_queue, conditions=[
            {'field': 'support_channel', 'operator': 'in', 'value': [foreign.id]},
        ])
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Unknown channel ids' in res.data['conditions'][0]

    def test_channel_and_organisation_conditions_accepted(
        self, csm_admin_client, project, experience_group, csm_queue, channel, customer_organisation,
    ):
        res = _create(csm_admin_client, project, experience_group, csm_queue, conditions=[
            {'field': 'support_channel', 'operator': 'in', 'value': [channel.id]},
            {'field': 'customer_organisation', 'operator': 'not_in', 'value': [customer_organisation.id]},
        ])
        assert res.status_code == status.HTTP_201_CREATED, res.data

    def test_queue_from_another_project_is_rejected(
        self, csm_admin_client, project, other_project, experience_group, customer_organisation,
    ):
        foreign_queue = Queue.objects.create(
            project=other_project, organisation=customer_organisation, name='Elsewhere',
        )
        res = _create(csm_admin_client, project, experience_group, foreign_queue)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target_queue' in res.data

    def test_inactive_queue_is_rejected(self, csm_admin_client, project, experience_group, csm_queue):
        csm_queue.is_active = False
        csm_queue.save()
        res = _create(csm_admin_client, project, experience_group, csm_queue)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target_queue' in res.data

    def test_missing_queue_is_rejected(self, csm_admin_client, project, experience_group, csm_queue):
        res = _create(csm_admin_client, project, experience_group, csm_queue, target_queue=None)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target_queue' in res.data

    def test_experience_group_from_another_project_is_rejected(
        self, csm_admin_client, project, other_project, csm_queue,
    ):
        foreign_group = ExperienceGroup.objects.create(project=other_project, name='Foreign')
        res = _create(csm_admin_client, project, foreign_group, csm_queue)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'experience_group' in res.data

    def test_tags_are_validated(self, csm_admin_client, project, experience_group, csm_queue):
        res = _create(csm_admin_client, project, experience_group, csm_queue, add_tags=['x' * 51])
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'add_tags' in res.data


class TestListUpdateDelete:
    def test_list_is_ordered_unpaginated_and_filtered_by_group(
        self, csm_admin_client, project, experience_group, csm_queue,
    ):
        other_group = ExperienceGroup.objects.create(project=project, name='Standard')
        _create(csm_admin_client, project, experience_group, csm_queue, name='A')
        _create(csm_admin_client, project, experience_group, csm_queue, name='B')
        _create(csm_admin_client, project, other_group, csm_queue, name='C')

        res = csm_admin_client.get(_list_url(project.id, experience_group=experience_group.id))
        assert res.status_code == status.HTTP_200_OK
        assert [r['name'] for r in res.data] == ['A', 'B']

    def test_partial_update(self, csm_admin_client, project, experience_group, csm_queue, tech_queue):
        rule_id = _create(csm_admin_client, project, experience_group, csm_queue).data['id']
        res = csm_admin_client.patch(_detail_url(rule_id), {
            'is_enabled': False, 'match_mode': 'any', 'target_queue': tech_queue.id,
        }, format='json')
        assert res.status_code == status.HTTP_200_OK, res.data
        assert res.data['is_enabled'] is False
        assert res.data['match_mode'] == 'any'
        assert res.data['target_queue_name'] == 'Tech'
        # untouched fields keep their values
        assert res.data['name'] == 'Refunds'
        assert res.data['add_tags'] == ['billing']

    def test_update_cannot_move_rule_between_groups(
        self, csm_admin_client, project, experience_group, csm_queue,
    ):
        other_group = ExperienceGroup.objects.create(project=project, name='Standard')
        rule_id = _create(csm_admin_client, project, experience_group, csm_queue).data['id']
        csm_admin_client.patch(_detail_url(rule_id), {'experience_group': other_group.id}, format='json')
        assert RoutingRule.objects.get(pk=rule_id).experience_group_id == experience_group.id

    def test_delete(self, csm_admin_client, project, experience_group, csm_queue):
        rule_id = _create(csm_admin_client, project, experience_group, csm_queue).data['id']
        res = csm_admin_client.delete(_detail_url(rule_id))
        assert res.status_code == status.HTTP_204_NO_CONTENT
        assert not RoutingRule.objects.exists()

    def test_deleted_queue_leaves_rule_without_target(
        self, csm_admin_client, project, experience_group, csm_queue,
    ):
        _create(csm_admin_client, project, experience_group, csm_queue)
        csm_queue.delete()
        res = csm_admin_client.get(_list_url(project.id, experience_group=experience_group.id))
        assert res.data[0]['target_queue'] is None
        assert res.data[0]['target_queue_name'] is None


class TestReorder:
    def test_reorders_group(self, csm_admin_client, project, experience_group, csm_queue):
        a = _create(csm_admin_client, project, experience_group, csm_queue, name='A').data['id']
        b = _create(csm_admin_client, project, experience_group, csm_queue, name='B').data['id']
        res = csm_admin_client.put(_reorder_url(project.id), {
            'experience_group': experience_group.id, 'ids': [b, a],
        }, format='json')
        assert res.status_code == status.HTTP_200_OK, res.data
        assert [r['name'] for r in res.data] == ['B', 'A']
        assert [r['position'] for r in res.data] == [0, 1]

    @pytest.mark.parametrize('mutate', [
        lambda ids: ids[:1],             # missing
        lambda ids: ids + [ids[0]],      # duplicate
        lambda ids: ids + [999999],      # foreign
    ])
    def test_requires_exact_id_set(self, csm_admin_client, project, experience_group, csm_queue, mutate):
        a = _create(csm_admin_client, project, experience_group, csm_queue, name='A').data['id']
        b = _create(csm_admin_client, project, experience_group, csm_queue, name='B').data['id']
        res = csm_admin_client.put(_reorder_url(project.id), {
            'experience_group': experience_group.id, 'ids': mutate([a, b]),
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'ids' in res.data


class TestVocabularyAndPermissions:
    def test_vocabulary(self, csm_admin_client, project):
        res = csm_admin_client.get(reverse('routing-rule-vocabulary') + f'?project={project.id}')
        assert res.status_code == status.HTTP_200_OK
        fields = {f['field']: f for f in res.data['fields']}
        assert 'latest_message' in fields
        assert {o['operator'] for o in fields['message_count']['operators']} == {'gte', 'lte', 'eq'}
        assert res.data['limits']['conditions'] == 10

    def test_project_member_without_csm_admin_is_forbidden(self, member_client, project):
        res = member_client.get(_list_url(project.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_csm_admin_outside_project_is_forbidden(
        self, api_client, user2, customer_organisation, project,
    ):
        CustomerUser.objects.create(
            user=user2, organisation=customer_organisation, user_type='admin', is_active=True,
        )
        api_client.force_authenticate(user=user2)
        res = api_client.get(_list_url(project.id))
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_csm_admin_outside_project_cannot_touch_detail(
        self, csm_admin_client, api_client, user2, customer_organisation, project, experience_group, csm_queue,
    ):
        rule_id = _create(csm_admin_client, project, experience_group, csm_queue).data['id']
        CustomerUser.objects.create(
            user=user2, organisation=customer_organisation, user_type='admin', is_active=True,
        )
        ProjectMember.objects.filter(user=user2).delete()
        api_client.force_authenticate(user=user2)
        res = api_client.delete(_detail_url(rule_id))
        assert res.status_code == status.HTTP_404_NOT_FOUND
        assert RoutingRule.objects.filter(pk=rule_id).exists()

    def test_project_param_required(self, csm_admin_client):
        res = csm_admin_client.get(reverse('routing-rule-list'))
        assert res.status_code == status.HTTP_400_BAD_REQUEST
