import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import ValidationError

from core.models import ProjectMember
from csm.models import Conversation, CustomerUser, QueueAgent, Ticket
from org_customization.models import OptionConfig, TaskTypeConfig

pytestmark = pytest.mark.django_db

CONFIG_PATH = reverse('org-customization-csm-config')
SYSTEM_CONFIG_PATH = reverse('org-customization-system-config')


def _config_url(project):
    return f'{CONFIG_PATH}?project_id={project.id}'


def _system_config_url(project):
    return f'{SYSTEM_CONFIG_PATH}?project_id={project.id}'


def _disable(project, field, value):
    OptionConfig.objects.create(
        organization=project.organization, project=project,
        module='csm', field=field, value=value, enabled=False,
    )


@pytest.fixture
def csm_admin_client(api_client, user, customer_organisation):
    CustomerUser.objects.create(
        user=user, organisation=customer_organisation, user_type='admin', is_active=True,
    )
    api_client.force_authenticate(user=user)
    return api_client


def _options(response, field):
    return [item for item in response.data['options'] if item['field'] == field]


def test_get_returns_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_config_url(project))

    assert response.status_code == status.HTTP_200_OK
    priorities = _options(response, 'ticket_priority')
    assert [p['value'] for p in priorities] == [c[0] for c in Ticket.PRIORITY_CHOICES]
    assert all(p['enabled'] and p['label'] is None for p in priorities)
    assert [p['value'] for p in priorities if p['is_default']] == ['medium']
    assert priorities[0]['editable'] == ['label', 'color', 'enabled', 'order']
    assert response.data['can_edit'] is True
    assert api_client.put(_config_url(project), {'options': []}, format='json').status_code == 405


def test_system_config_saves_task_and_csm(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_system_config_url(project), {
        'task': {'task_types': [{'key': 'budget', 'label': 'Spend'}]},
        'csm': {'options': [
            {'field': 'ticket_priority', 'value': 'low', 'label': 'Minor', 'color': '#123456', 'order': 0},
            {'field': 'ticket_priority', 'value': 'critical', 'enabled': False, 'order': 5},
        ]},
    }, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    assert next(t for t in response.data['task']['task_types'] if t['key'] == 'budget')['label'] == 'Spend'
    priorities = response.data['csm']['options']
    assert priorities[0]['value'] == 'low'
    assert (priorities[0]['label'], priorities[0]['color']) == ('Minor', '#123456')
    assert priorities[-1]['value'] == 'critical' and priorities[-1]['enabled'] is False


def test_system_config_replaces_stale_rows(api_client, user, project):
    api_client.force_authenticate(user=user)
    _disable(project, 'ticket_priority', 'critical')

    response = api_client.put(_system_config_url(project), {'csm': {'options': [
        {'field': 'ticket_priority', 'value': 'low', 'label': 'Minor'},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    assert list(OptionConfig.objects.values_list('value', flat=True)) == ['low']


@pytest.mark.parametrize('item', [
    {'field': 'ticket_priority', 'value': 'medium', 'enabled': False},
    {'field': 'ticket_priority', 'value': 'urgent'},
    {'field': 'queue_tier', 'value': 'T1'},
])
def test_system_config_rejects_invalid_options_without_saving_task(api_client, user, project, item):
    api_client.force_authenticate(user=user)

    response = api_client.put(_system_config_url(project), {
        'task': {'task_types': [{'key': 'budget', 'label': 'Spend'}]},
        'csm': {'options': [item]},
    }, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data['non_field_errors']
    assert not OptionConfig.objects.exists()
    assert not TaskTypeConfig.objects.exists()


def test_member_cannot_put(api_client, user2, project):
    ProjectMember.objects.create(user=user2, project=project, is_active=True)
    api_client.force_authenticate(user=user2)

    assert api_client.get(_config_url(project)).data['can_edit'] is False
    response = api_client.put(_system_config_url(project), {'csm': {'options': []}}, format='json')

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_ticket_save_rejects_disabled_priority(project, csm_queue):
    ticket = Ticket.objects.create(queue=csm_queue, title='Existing', priority='critical')
    _disable(project, 'ticket_priority', 'critical')
    _disable(project, 'ticket_priority', 'high')

    Ticket.objects.get(pk=ticket.pk).save()
    with pytest.raises(ValidationError):
        Ticket.objects.create(queue=csm_queue, title='New', priority='critical')
    loaded = Ticket.objects.get(pk=ticket.pk)
    loaded.priority = 'high'
    with pytest.raises(ValidationError):
        loaded.save()
    loaded.priority = 'medium'
    loaded.save()


def test_ticket_api_rejects_disabled_priority(csm_admin_client, project, csm_queue):
    ticket = Ticket.objects.create(queue=csm_queue, title='Existing', priority='critical')
    _disable(project, 'ticket_priority', 'critical')

    create = csm_admin_client.post(
        reverse('ticket-list'), {'queue': csm_queue.id, 'title': 'New', 'priority': 'critical'}, format='json',
    )
    keep = csm_admin_client.patch(
        reverse('ticket-detail', kwargs={'pk': ticket.id}), {'priority': 'critical', 'title': 'Renamed'}, format='json',
    )

    assert create.status_code == status.HTTP_400_BAD_REQUEST
    assert 'priority' in create.data
    assert keep.status_code == status.HTTP_200_OK, keep.data


def test_conversation_create_ticket_rejects_disabled_priority(api_client, user, project, csm_queue, customer):
    CustomerUser.objects.create(
        user=user, organisation=csm_queue.organisation, queue=csm_queue, user_type='agent', is_active=True,
    )
    QueueAgent.objects.create(queue=csm_queue, user=user)
    api_client.force_authenticate(user=user)
    _disable(project, 'ticket_priority', 'high')
    conversation = Conversation.objects.create(customer=customer, queue=csm_queue)

    response = api_client.post(
        reverse('conversation-create-ticket', kwargs={'pk': conversation.id}),
        {'title': 'Billing', 'priority': 'high', 'queue': csm_queue.id},
        format='json',
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Ticket.objects.filter(conversation=conversation).exists()

