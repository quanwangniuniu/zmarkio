import pytest
from django.urls import reverse
from rest_framework import status

from org_customization.models import OptionConfig
from org_customization.services import TASK_SURFACES
from task.models import Task

pytestmark = pytest.mark.django_db


def _url(name, project):
    return f"{reverse(name)}?project_id={project.id}"


def test_get_returns_tasks_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_url('org-customization-tasks-config', project))

    assert response.status_code == status.HTTP_200_OK
    options = response.data['options']
    for field, items in TASK_SURFACES.items():
        assert [o['value'] for o in options if o['field'] == field] == [key for key, _ in items]
    assert [o['value'] for o in options if o['field'] == 'filter_status'] == list(Task.Status.values)
    assert all(o['enabled'] for o in options)
    assert not any(o['is_default'] for o in options)


def test_system_config_saves_tasks(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'tasks': {'options': [
        {'field': 'tab', 'value': 'summary', 'enabled': False},
        {'field': 'tab', 'value': 'board', 'label': 'Kanban', 'order': 0},
        {'field': 'filter', 'value': 'tags', 'enabled': False},
        {'field': 'filter_status', 'value': 'LOCKED', 'enabled': False, 'label': 'Ignored', 'order': 0},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = {(r.field, r.value): r for r in OptionConfig.objects.filter(module='tasks')}
    assert rows[('tab', 'summary')].enabled is False
    assert (rows[('tab', 'board')].label, rows[('tab', 'board')].order) == ('Kanban', 0)
    assert rows[('filter', 'tags')].enabled is False
    locked = rows[('filter_status', 'LOCKED')]
    assert (locked.enabled, locked.label, locked.order) == (False, None, 0)


@pytest.mark.parametrize('item', [
    {'field': 'tab', 'value': 'my-actions'},
    {'field': 'filter', 'value': 'approver'},
    {'field': 'filter_status', 'value': 'ARCHIVED'},
])
def test_system_config_rejects_invalid_tasks(api_client, user, project, item):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'tasks': {'options': [item]}}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not OptionConfig.objects.exists()
