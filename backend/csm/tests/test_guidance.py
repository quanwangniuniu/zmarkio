"""Guidance entry configuration and workspace display (CSM-S03-02 / MED-222)."""
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from core.models import OrganizationMembership, Project, ProjectMember
from csm.models import (
    Conversation,
    CustomerUser,
    GuidanceEntry,
    GuidanceEntryExperienceGroup,
    QueueAgent,
)
from experience_group.models import ExperienceGroup


pytestmark = pytest.mark.django_db

User = get_user_model()


def _list_url(project, **params):
    query = '&'.join(f'{key}={value}' for key, value in {'project': project.id, **params}.items())
    return f"{reverse('csm-guidance-list')}?{query}"


def _detail_url(entry):
    return reverse('csm-guidance-detail', kwargs={'pk': entry.id})


def _reorder_url(project):
    return f"{reverse('csm-guidance-reorder')}?project={project.id}"


def _capabilities_url(project):
    return f"{reverse('csm-guidance-capabilities')}?project={project.id}"


def _conversation_guidance_url(conversation):
    return reverse('conversation-guidance', kwargs={'pk': conversation.id})


def _payload(experience_group_ids, **overrides):
    data = {
        'guidance_type': 'suggested_reply',
        'trigger_description': 'Customer asks about a refund after 30 days.',
        'recommended_response': 'Thanks for reaching out.\nRefunds after 30 days need approval.',
        'experience_group_ids': experience_group_ids,
    }
    data.update(overrides)
    return data


def _make_entry(project, groups, **fields):
    entry = GuidanceEntry.objects.create(
        project=project,
        guidance_type=fields.pop('guidance_type', 'process_note'),
        trigger_description=fields.pop('trigger_description', 'Trigger'),
        recommended_response=fields.pop('recommended_response', 'Response'),
        **fields,
    )
    for group, order in groups:
        GuidanceEntryExperienceGroup.objects.create(
            entry=entry, experience_group=group, display_order=order,
        )
    return entry


@pytest.fixture
def second_group(project):
    return ExperienceGroup.objects.create(project=project, name='Standard Support')


@pytest.fixture
def member(user2, project):
    ProjectMember.objects.create(user=user2, project=project, role='member', is_active=True)
    return user2


@pytest.fixture
def org_admin(organization):
    admin = User.objects.create_user(
        username='orgadmin', email='orgadmin@test.com', password='testpass123',
        organization=organization,
    )
    OrganizationMembership.objects.create(
        user=admin, organization=organization, role='admin', is_active=True,
    )
    return admin


def _fake_async_to_sync(recorded):
    def factory(_fn):
        def inner(group, event):
            recorded.append((group, event))
        return inner
    return factory


# ── Create ────────────────────────────────────────────────────────────────


def test_owner_creates_entry_linked_to_groups(
    member_client, project, experience_group, second_group,
):
    response = member_client.post(
        _list_url(project),
        _payload([experience_group.id, second_group.id]),
        format='json',
    )

    assert response.status_code == status.HTTP_201_CREATED, response.data
    assert response.data['guidance_type'] == 'suggested_reply'
    assert response.data['guidance_type_display'] == 'Suggested Reply'
    assert {g['id'] for g in response.data['experience_groups']} == {
        experience_group.id, second_group.id,
    }
    entry = GuidanceEntry.objects.get(pk=response.data['id'])
    assert entry.project_id == project.id
    assert entry.created_by_id is not None


def test_new_entries_are_appended_to_each_group(member_client, project, experience_group):
    first = member_client.post(_list_url(project), _payload([experience_group.id]), format='json')
    second = member_client.post(_list_url(project), _payload([experience_group.id]), format='json')

    orders = dict(
        GuidanceEntryExperienceGroup.objects.filter(
            experience_group=experience_group,
        ).values_list('entry_id', 'display_order'),
    )
    assert orders[first.data['id']] == 0
    assert orders[second.data['id']] == 1


@pytest.mark.parametrize('guidance_type', [
    'handoff', 'suggested_reply', 'escalation_procedure', 'process_note',
])
def test_all_guidance_types_are_accepted(member_client, project, experience_group, guidance_type):
    response = member_client.post(
        _list_url(project),
        _payload([experience_group.id], guidance_type=guidance_type),
        format='json',
    )
    assert response.status_code == status.HTTP_201_CREATED


@pytest.mark.parametrize('overrides, field', [
    ({'guidance_type': 'bogus'}, 'guidance_type'),
    ({'trigger_description': '   '}, 'trigger_description'),
    ({'recommended_response': ''}, 'recommended_response'),
    ({'experience_group_ids': []}, 'experience_group_ids'),
])
def test_create_rejects_invalid_fields(member_client, project, experience_group, overrides, field):
    data = {**_payload([experience_group.id]), **overrides}
    response = member_client.post(_list_url(project), data, format='json')
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert field in response.data
    assert not GuidanceEntry.objects.exists()


def test_create_rejects_group_from_another_project(member_client, project, user, organization):
    other_project = Project.objects.create(name='Other', organization=organization, owner=user)
    foreign_group = ExperienceGroup.objects.create(project=other_project, name='Foreign')

    response = member_client.post(
        _list_url(project), _payload([foreign_group.id]), format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'experience_group_ids' in response.data
    assert not GuidanceEntry.objects.exists()


def test_create_rejects_group_without_project(member_client, project):
    orphan_group = ExperienceGroup.objects.create(project=None, name='Orphan')

    response = member_client.post(
        _list_url(project), _payload([orphan_group.id]), format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not GuidanceEntry.objects.exists()


# ── Permissions ───────────────────────────────────────────────────────────


def test_plain_member_can_read_but_not_write(
    api_client, member, project, experience_group, second_group,
):
    entry = _make_entry(project, [(experience_group, 0)])
    api_client.force_authenticate(user=member)

    assert api_client.get(_list_url(project)).status_code == status.HTTP_200_OK
    assert api_client.get(_detail_url(entry)).status_code == status.HTTP_200_OK
    assert api_client.get(_capabilities_url(project)).data == {'can_manage': False}

    writes = [
        api_client.post(_list_url(project), _payload([experience_group.id]), format='json'),
        api_client.patch(_detail_url(entry), {'trigger_description': 'x'}, format='json'),
        api_client.put(
            _reorder_url(project),
            {'experience_group': experience_group.id, 'ids': [entry.id]},
            format='json',
        ),
        api_client.delete(_detail_url(entry)),
    ]
    assert [r.status_code for r in writes] == [status.HTTP_403_FORBIDDEN] * 4
    assert GuidanceEntry.objects.count() == 1


def test_outsider_is_denied(outsider_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])

    assert outsider_client.get(_list_url(project)).status_code == status.HTTP_403_FORBIDDEN
    assert outsider_client.get(_detail_url(entry)).status_code == status.HTTP_404_NOT_FOUND
    assert outsider_client.post(
        _list_url(project), _payload([experience_group.id]), format='json',
    ).status_code == status.HTTP_403_FORBIDDEN


def test_org_admin_who_is_not_a_member_can_manage(api_client, org_admin, project, experience_group):
    api_client.force_authenticate(user=org_admin)

    assert api_client.get(_capabilities_url(project)).data == {'can_manage': True}
    response = api_client.post(_list_url(project), _payload([experience_group.id]), format='json')
    assert response.status_code == status.HTTP_201_CREATED


def test_owner_capabilities(member_client, project):
    assert member_client.get(_capabilities_url(project)).data == {'can_manage': True}


def test_put_on_detail_is_not_allowed(member_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])
    response = member_client.put(
        _detail_url(entry), _payload([experience_group.id]), format='json',
    )
    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


# ── List / update / delete ────────────────────────────────────────────────


def test_list_by_group_returns_that_groups_entries_in_order(
    member_client, project, experience_group, second_group,
):
    later = _make_entry(project, [(experience_group, 5)], trigger_description='later')
    earlier = _make_entry(project, [(experience_group, 1), (second_group, 0)], trigger_description='earlier')
    other = _make_entry(project, [(second_group, 1)], trigger_description='other group')

    response = member_client.get(_list_url(project, experience_group=experience_group.id))

    assert response.status_code == status.HTTP_200_OK
    assert [row['id'] for row in response.data] == [earlier.id, later.id]

    everything = member_client.get(_list_url(project))
    assert {row['id'] for row in everything.data} == {earlier.id, later.id, other.id}


def test_list_rejects_group_from_another_project(member_client, project, user, organization):
    other_project = Project.objects.create(name='Other', organization=organization, owner=user)
    foreign_group = ExperienceGroup.objects.create(project=other_project, name='Foreign')

    response = member_client.get(_list_url(project, experience_group=foreign_group.id))

    assert response.status_code == status.HTTP_400_BAD_REQUEST


def test_update_keeps_order_of_retained_groups_and_appends_new_ones(
    member_client, project, experience_group, second_group,
):
    _make_entry(project, [(second_group, 0)])
    entry = _make_entry(project, [(experience_group, 3)])

    response = member_client.patch(
        _detail_url(entry),
        {
            'trigger_description': 'Updated trigger',
            'experience_group_ids': [experience_group.id, second_group.id],
        },
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data['trigger_description'] == 'Updated trigger'
    orders = {g['id']: g['display_order'] for g in response.data['experience_groups']}
    assert orders == {experience_group.id: 3, second_group.id: 1}


def test_update_removes_unselected_group(member_client, project, experience_group, second_group):
    entry = _make_entry(project, [(experience_group, 0), (second_group, 0)])

    response = member_client.patch(
        _detail_url(entry), {'experience_group_ids': [second_group.id]}, format='json',
    )

    assert response.status_code == status.HTTP_200_OK
    assert list(entry.experience_group_links.values_list('experience_group_id', flat=True)) == [
        second_group.id,
    ]


def test_delete_removes_entry_and_links(member_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])

    response = member_client.delete(_detail_url(entry))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not GuidanceEntry.objects.filter(pk=entry.pk).exists()
    assert not GuidanceEntryExperienceGroup.objects.exists()


# ── Reorder ───────────────────────────────────────────────────────────────


def test_reorder_sets_display_order_within_group(
    member_client, project, experience_group, second_group,
):
    a = _make_entry(project, [(experience_group, 0), (second_group, 0)])
    b = _make_entry(project, [(experience_group, 1)])
    c = _make_entry(project, [(experience_group, 2)])

    response = member_client.put(
        _reorder_url(project),
        {'experience_group': experience_group.id, 'ids': [c.id, a.id, b.id]},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert [row['id'] for row in response.data] == [c.id, a.id, b.id]
    # Other groups keep their own order.
    assert GuidanceEntryExperienceGroup.objects.get(
        entry=a, experience_group=second_group,
    ).display_order == 0


@pytest.mark.parametrize('ids_builder', [
    lambda a, b: [a.id],              # missing
    lambda a, b: [a.id, b.id, 999999],  # extra
    lambda a, b: [a.id, a.id, b.id],  # duplicate
])
def test_reorder_requires_exact_set(member_client, project, experience_group, ids_builder):
    a = _make_entry(project, [(experience_group, 0)])
    b = _make_entry(project, [(experience_group, 1)])

    response = member_client.put(
        _reorder_url(project),
        {'experience_group': experience_group.id, 'ids': ids_builder(a, b)},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert list(
        GuidanceEntryExperienceGroup.objects.order_by('display_order').values_list('entry_id', flat=True),
    ) == [a.id, b.id]


def test_reorder_is_reflected_in_later_reads_despite_group_name_order(
    api_client, member_client, user2, project, csm_queue, customer, experience_group,
):
    # Group chips on an entry are sorted by name; entry order within a group
    # must still follow display_order, including in the agent workspace.
    zulu = ExperienceGroup.objects.create(project=project, name='Zulu')
    a = _make_entry(project, [(experience_group, 0), (zulu, 0)])
    b = _make_entry(project, [(experience_group, 1), (zulu, 1)])

    response = member_client.put(
        _reorder_url(project),
        {'experience_group': experience_group.id, 'ids': [b.id, a.id]},
        format='json',
    )
    assert response.status_code == status.HTTP_200_OK, response.data

    listed = member_client.get(_list_url(project, experience_group=experience_group.id))
    assert [row['id'] for row in listed.data] == [b.id, a.id]

    customer.experience_group = experience_group
    customer.save()
    conversation = Conversation.objects.create(customer=customer, queue=csm_queue)
    _authenticate_queue_agent(api_client, user2, csm_queue)
    workspace = api_client.get(_conversation_guidance_url(conversation))
    assert [row['id'] for row in workspace.data['entries']] == [b.id, a.id]


def test_reorder_with_current_expected_order_succeeds(member_client, project, experience_group):
    a = _make_entry(project, [(experience_group, 0)])
    b = _make_entry(project, [(experience_group, 1)])

    response = member_client.put(
        _reorder_url(project),
        {'experience_group': experience_group.id, 'ids': [b.id, a.id], 'expected_ids': [a.id, b.id]},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert [row['id'] for row in response.data] == [b.id, a.id]


def test_reorder_with_stale_expected_order_conflicts(member_client, project, experience_group):
    a = _make_entry(project, [(experience_group, 0)])
    b = _make_entry(project, [(experience_group, 1)])
    c = _make_entry(project, [(experience_group, 2)])
    # Another admin already moved c to the top.
    GuidanceEntryExperienceGroup.objects.filter(entry=c).update(display_order=0)
    GuidanceEntryExperienceGroup.objects.filter(entry=a).update(display_order=1)
    GuidanceEntryExperienceGroup.objects.filter(entry=b).update(display_order=2)

    with patch('csm.services.guidance.async_to_sync') as sender:
        response = member_client.put(
            _reorder_url(project),
            {
                'experience_group': experience_group.id,
                'ids': [b.id, a.id, c.id],
                'expected_ids': [a.id, b.id, c.id],
            },
            format='json',
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert list(
        GuidanceEntryExperienceGroup.objects.order_by('display_order').values_list('entry_id', flat=True),
    ) == [c.id, a.id, b.id]
    sender.assert_not_called()


# ── Optimistic concurrency on edit / delete ───────────────────────────────


def test_update_with_current_version_succeeds(member_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])
    seen = member_client.get(_list_url(project)).data[0]['updated_at']

    response = member_client.patch(
        _detail_url(entry),
        {'trigger_description': 'Edited', 'expected_updated_at': seen},
        format='json',
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data['trigger_description'] == 'Edited'


def test_update_with_stale_version_conflicts(member_client, project, experience_group, second_group):
    entry = _make_entry(project, [(experience_group, 0)])
    seen = member_client.get(_list_url(project)).data[0]['updated_at']
    # Another admin moves the entry to a different group first.
    first = member_client.patch(
        _detail_url(entry), {'experience_group_ids': [second_group.id]}, format='json',
    )
    assert first.status_code == status.HTTP_200_OK

    response = member_client.patch(
        _detail_url(entry),
        {'trigger_description': 'Edited', 'expected_updated_at': seen},
        format='json',
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    entry.refresh_from_db()
    assert entry.trigger_description == 'Trigger'
    assert list(entry.experience_group_links.values_list('experience_group_id', flat=True)) == [
        second_group.id,
    ]


def test_delete_with_stale_version_conflicts(member_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])
    seen = member_client.get(_list_url(project)).data[0]['updated_at']
    member_client.patch(_detail_url(entry), {'trigger_description': 'Edited'}, format='json')

    response = member_client.delete(
        f"{_detail_url(entry)}?{urlencode({'expected_updated_at': seen})}",
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert GuidanceEntry.objects.filter(pk=entry.pk).exists()


def test_delete_with_current_version_succeeds(member_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])
    seen = member_client.get(_list_url(project)).data[0]['updated_at']

    response = member_client.delete(
        f"{_detail_url(entry)}?{urlencode({'expected_updated_at': seen})}",
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not GuidanceEntry.objects.filter(pk=entry.pk).exists()


def test_delete_rejects_malformed_version(member_client, project, experience_group):
    entry = _make_entry(project, [(experience_group, 0)])

    response = member_client.delete(f'{_detail_url(entry)}?expected_updated_at=yesterday')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert GuidanceEntry.objects.filter(pk=entry.pk).exists()


# ── Broadcasts ────────────────────────────────────────────────────────────


def test_writes_broadcast_to_affected_group_channels(
    member_client, project, experience_group, second_group,
    django_capture_on_commit_callbacks,
):
    sent = []
    with patch('csm.services.guidance.async_to_sync', _fake_async_to_sync(sent)):
        with django_capture_on_commit_callbacks(execute=True):
            created = member_client.post(
                _list_url(project), _payload([experience_group.id]), format='json',
            )
        assert [group for group, _ in sent] == [f'csm_guidance_eg_{experience_group.id}']
        assert sent[0][1] == {
            'type': 'guidance.updated',
            'experience_group_ids': [experience_group.id],
        }

        sent.clear()
        with django_capture_on_commit_callbacks(execute=True):
            member_client.patch(
                _detail_url(GuidanceEntry.objects.get(pk=created.data['id'])),
                {'experience_group_ids': [second_group.id]},
                format='json',
            )
        # Moving groups notifies both the old and the new group.
        assert {group for group, _ in sent} == {
            f'csm_guidance_eg_{experience_group.id}',
            f'csm_guidance_eg_{second_group.id}',
        }

        sent.clear()
        with django_capture_on_commit_callbacks(execute=True):
            member_client.put(
                _reorder_url(project),
                {'experience_group': second_group.id, 'ids': [created.data['id']]},
                format='json',
            )
        assert [group for group, _ in sent] == [f'csm_guidance_eg_{second_group.id}']

        sent.clear()
        with django_capture_on_commit_callbacks(execute=True):
            member_client.delete(_detail_url(GuidanceEntry.objects.get(pk=created.data['id'])))
        assert [group for group, _ in sent] == [f'csm_guidance_eg_{second_group.id}']


def test_rejected_write_does_not_broadcast(
    member_client, project, experience_group, django_capture_on_commit_callbacks,
):
    sent = []
    with patch('csm.services.guidance.async_to_sync', _fake_async_to_sync(sent)):
        with django_capture_on_commit_callbacks(execute=True):
            response = member_client.post(
                _list_url(project), _payload([experience_group.id], trigger_description=''),
                format='json',
            )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert sent == []


# ── Agent workspace endpoint ──────────────────────────────────────────────


def _authenticate_queue_agent(api_client, agent, queue):
    CustomerUser.objects.create(
        user=agent, organisation=queue.organisation, queue=queue,
        user_type='agent', is_active=True,
    )
    QueueAgent.objects.create(queue=queue, user=agent)
    api_client.force_authenticate(user=agent)


def test_conversation_guidance_shows_only_matched_group_in_order(
    api_client, user2, project, csm_queue, customer, experience_group, second_group,
):
    customer.experience_group = experience_group
    customer.save()
    conversation = Conversation.objects.create(customer=customer, queue=csm_queue)
    second = _make_entry(project, [(experience_group, 1)], guidance_type='handoff')
    first = _make_entry(project, [(experience_group, 0), (second_group, 5)])
    _make_entry(project, [(second_group, 0)], trigger_description='other group only')
    # An agent needs queue access, not project membership.
    _authenticate_queue_agent(api_client, user2, csm_queue)

    response = api_client.get(_conversation_guidance_url(conversation))

    assert response.status_code == status.HTTP_200_OK
    assert response.data['experience_group'] == {
        'id': experience_group.id, 'name': experience_group.name,
    }
    assert [row['id'] for row in response.data['entries']] == [first.id, second.id]
    assert response.data['entries'][1]['guidance_type_display'] == 'Handoff'
    assert response.data['entries'][0]['recommended_response'] == 'Response'


def test_conversation_guidance_without_group_is_empty(
    api_client, user2, project, csm_queue, customer, experience_group,
):
    _make_entry(project, [(experience_group, 0)])
    no_group = Conversation.objects.create(customer=customer, queue=csm_queue)
    no_customer = Conversation.objects.create(customer=None, queue=csm_queue)
    _authenticate_queue_agent(api_client, user2, csm_queue)

    for conversation in (no_group, no_customer):
        response = api_client.get(_conversation_guidance_url(conversation))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'experience_group': None, 'entries': []}


def test_conversation_guidance_requires_queue_access(
    api_client, user2, csm_queue, customer, experience_group,
):
    customer.experience_group = experience_group
    customer.save()
    conversation = Conversation.objects.create(customer=customer, queue=csm_queue)
    api_client.force_authenticate(user=user2)

    response = api_client.get(_conversation_guidance_url(conversation))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_deleting_experience_group_keeps_entry_reachable_in_full_list(
    member_client, project, experience_group,
):
    entry = _make_entry(project, [(experience_group, 0)])
    experience_group.delete()

    response = member_client.get(_list_url(project))

    assert [row['id'] for row in response.data] == [entry.id]
    assert response.data[0]['experience_groups'] == []


def test_list_unassigned_returns_only_entries_without_groups(
    member_client, project, experience_group, second_group,
):
    orphan = _make_entry(project, [(experience_group, 0)])
    kept = _make_entry(project, [(second_group, 0)])
    experience_group.delete()

    response = member_client.get(_list_url(project, unassigned='true'))

    assert response.status_code == status.HTTP_200_OK
    assert [row['id'] for row in response.data] == [orphan.id]
    assert kept.id not in {row['id'] for row in response.data}
