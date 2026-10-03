"""Routing rule CRUD API tests (CSM-S03-05)."""

import pytest
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status

from core.models import Project, ProjectMember
from csm.models import CustomerUser, Queue, RoutingRule, SupportChannel
from csm.services.routing_rules import create_rule, reorder_rules, update_rule
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



def _check_positions_now():
    """Run the deferred (group, position) check now; tests never reach COMMIT."""
    with connection.cursor() as cursor:
        cursor.execute('SET CONSTRAINTS csm_rr_unique_position_per_org_eg IMMEDIATE')


class TestPositionIntegrity:
    def test_two_rules_cannot_share_a_position(self, project, experience_group, csm_queue):
        # Inside a savepoint so the duplicate is rolled back before teardown,
        # which checks deferred constraints too.
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                for name in ('A', 'B'):
                    RoutingRule.objects.create(
                        organization=project.organization, project=project,
                        experience_group=experience_group, name=name,
                        position=0, target_queue=csm_queue,
                    )
                _check_positions_now()

    def test_reorder_swap_leaves_unique_positions(self, csm_admin_client, project, experience_group, csm_queue):
        a = _create(csm_admin_client, project, experience_group, csm_queue, name='A').data['id']
        b = _create(csm_admin_client, project, experience_group, csm_queue, name='B').data['id']
        res = csm_admin_client.put(_reorder_url(project.id), {
            'experience_group': experience_group.id, 'ids': [b, a],
        }, format='json')
        assert res.status_code == status.HTTP_200_OK, res.data
        _check_positions_now()  # raises if the swap left a duplicate

    def test_edit_does_not_write_back_a_stale_position(self, user, project, experience_group, csm_queue):
        a = create_rule(project.id, user=user, experience_group=experience_group, name='A', target_queue=csm_queue)
        b = create_rule(project.id, user=user, experience_group=experience_group, name='B', target_queue=csm_queue)
        stale_a = RoutingRule.objects.get(pk=a.pk)  # loaded before the reorder, position 0
        reorder_rules(project.id, experience_group.id, [b.pk, a.pk])

        update_rule(stale_a, is_enabled=False)

        _check_positions_now()
        assert list(
            RoutingRule.objects.filter(experience_group=experience_group).values_list('name', 'position'),
        ) == [('B', 0), ('A', 1)]
        assert RoutingRule.objects.get(pk=a.pk).is_enabled is False

    def test_create_and_reorder_take_the_same_group_lock(
        self, user, project, experience_group, csm_queue,
    ):
        rule = create_rule(
            project.id, user=user, experience_group=experience_group, name='A', target_queue=csm_queue,
        )
        table = ExperienceGroup._meta.db_table
        for run in (
            lambda: create_rule(
                project.id, user=user, experience_group=experience_group, name='B', target_queue=csm_queue,
            ),
            lambda: reorder_rules(project.id, experience_group.id, [rule.id] + list(
                RoutingRule.objects.filter(experience_group=experience_group).exclude(pk=rule.pk)
                .values_list('id', flat=True),
            )),
        ):
            with CaptureQueriesContext(connection) as queries:
                run()
            assert any(
                table in q['sql'] and 'FOR NO KEY UPDATE' in q['sql'] for q in queries.captured_queries
            )


class TestCanRoute:
    def _listed(self, client, project, experience_group):
        res = client.get(_list_url(project.id, experience_group=experience_group.id))
        return {r['name']: r['can_route'] for r in res.data}

    def test_flags_rules_whose_queue_is_inactive_or_deleted(
        self, csm_admin_client, project, experience_group, csm_queue, tech_queue,
    ):
        _create(csm_admin_client, project, experience_group, csm_queue, name='Live')
        _create(csm_admin_client, project, experience_group, tech_queue, name='Retired')
        assert self._listed(csm_admin_client, project, experience_group) == {'Live': True, 'Retired': True}

        tech_queue.is_active = False
        tech_queue.save()
        assert self._listed(csm_admin_client, project, experience_group) == {'Live': True, 'Retired': False}

        tech_queue.delete()  # target_queue is SET_NULL
        assert self._listed(csm_admin_client, project, experience_group) == {'Live': True, 'Retired': False}
        assert RoutingRule.objects.get(name='Retired').target_queue is None



class TestOrganizationIsolation:
    """
    Project ids are numbered per organisation schema, so another organisation
    can have a project with our id. Its rules share this public table and must
    never surface or change through our project.
    """

    @pytest.fixture
    def foreign(self, project, csm_queue):
        from core.models import Organization
        other_org = Organization.objects.create(name='Elsewhere', email_domain='elsewhere.test')
        group = ExperienceGroup.objects.create(project=project, name='Their group')
        rule = RoutingRule.objects.create(
            organization=other_org, project=project, experience_group=group, name='Theirs',
            position=0, target_queue=csm_queue,
        )
        return {'group': group, 'rule': rule, 'org': other_org}

    def test_new_rules_record_the_projects_organization(
        self, csm_admin_client, project, experience_group, csm_queue,
    ):
        rule_id = _create(csm_admin_client, project, experience_group, csm_queue).data['id']
        assert RoutingRule.objects.get(pk=rule_id).organization_id == project.organization_id

    def test_lists_never_include_another_organisations_rules(
        self, csm_admin_client, project, experience_group, csm_queue, foreign,
    ):
        _create(csm_admin_client, project, experience_group, csm_queue, name='Ours')
        everything = csm_admin_client.get(_list_url(project.id))
        assert [r['name'] for r in everything.data] == ['Ours']
        theirs = csm_admin_client.get(_list_url(project.id, experience_group=foreign['group'].id))
        assert theirs.data == []

    def test_another_organisations_rule_cannot_be_changed(self, csm_admin_client, project, foreign):
        rule = foreign['rule']
        assert csm_admin_client.patch(_detail_url(rule.pk), {'name': 'Hacked'}, format='json').status_code == 404
        assert csm_admin_client.delete(_detail_url(rule.pk)).status_code == 404
        res = csm_admin_client.put(_reorder_url(project.id), {
            'experience_group': foreign['group'].id, 'ids': [rule.pk],
        }, format='json')
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        rule.refresh_from_db()
        assert (rule.name, rule.position) == ('Theirs', 0)

    def test_another_organisations_rows_in_the_same_group_never_collide(
        self, csm_admin_client, project, experience_group, csm_queue, foreign,
    ):
        # Their rule sits in *our* group with the name and position ours will take.
        RoutingRule.objects.create(
            organization=foreign['org'], project=project, experience_group=experience_group,
            name='Refunds', position=0, target_queue=csm_queue,
        )
        a = _create(csm_admin_client, project, experience_group, csm_queue, name='Refunds')
        assert a.status_code == status.HTTP_201_CREATED, a.data  # no "already exists" oracle
        assert a.data['position'] == 0
        b = _create(csm_admin_client, project, experience_group, csm_queue, name='Billing').data['id']
        res = csm_admin_client.put(_reorder_url(project.id), {
            'experience_group': experience_group.id, 'ids': [b, a.data['id']],
        }, format='json')
        assert res.status_code == status.HTTP_200_OK, res.data
        _check_positions_now()

    def test_queue_of_another_organisation_is_rejected(
        self, csm_admin_client, project, experience_group, foreign,
    ):
        from customer.models import CustomerOrganisation
        their_customer = CustomerOrganisation.objects.create(name='Their customer', organization=foreign['org'])
        their_queue = Queue.objects.create(project=project, organisation=their_customer, name='Theirs', tier='T1')
        res = _create(csm_admin_client, project, experience_group, their_queue)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert 'target_queue' in res.data

    def test_another_organisations_rule_cannot_be_read(self, csm_admin_client, foreign):
        assert csm_admin_client.get(_detail_url(foreign['rule'].pk)).status_code == 404

    def test_project_without_an_organisation_is_a_400_not_a_500(
        self, csm_admin_client, user, experience_group, csm_queue,
    ):
        orphan = Project.objects.create(
            name='Orphan', organization=None, owner=user, objectives=['awareness'], kpis={},
        )
        ProjectMember.objects.create(user=user, project=orphan, role='owner', is_active=True)
        listed = csm_admin_client.get(_list_url(orphan.id))
        assert listed.status_code == status.HTTP_400_BAD_REQUEST
        assert 'project' in listed.data
        created = _create(csm_admin_client, orphan, experience_group, csm_queue)
        assert created.status_code == status.HTTP_400_BAD_REQUEST

    def test_sandbox_ignores_another_organisations_rules(self, csm_admin_client, project, foreign):
        res = csm_admin_client.post(
            reverse('routing-sandbox-evaluate') + f'?project={project.id}',
            {'experience_group': foreign['group'].id, 'messages': ['Hi']}, format='json',
        )
        assert res.status_code == status.HTTP_200_OK, res.data
        assert res.data['rule_count'] == 0
        assert res.data['traces'][0]['steps'] == []


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
