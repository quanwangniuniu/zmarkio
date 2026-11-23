import pytest
from django.urls import reverse
from rest_framework import status

from org_customization.models import OptionConfig
from org_customization.services import MEETING_SURFACES

pytestmark = pytest.mark.django_db


def _url(name, project):
    return f"{reverse(name)}?project_id={project.id}"


def test_get_returns_meetings_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_url('org-customization-meetings-config', project))

    assert response.status_code == status.HTTP_200_OK
    options = response.data['options']
    for field, items in MEETING_SURFACES.items():
        assert [o['value'] for o in options if o['field'] == field] == [key for key, _ in items]
    assert all(o['enabled'] for o in options)
    assert not any(o['is_default'] for o in options)


def test_system_config_saves_meetings(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'meetings': {'options': [
        {'field': 'filter', 'value': 'is_archived', 'enabled': False, 'label': 'Ignored'},
        {'field': 'filter_type', 'value': 'stand-up', 'label': 'Daily', 'order': 0},
        {'field': 'panel', 'value': 'knowledge_nav', 'enabled': False},
        {'field': 'section', 'value': 'action_items', 'label': 'Follow-ups', 'order': 5},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = {(r.field, r.value): r for r in OptionConfig.objects.filter(module='meetings')}
    assert rows[('filter', 'is_archived')].enabled is False
    assert rows[('filter', 'is_archived')].label is None
    assert (rows[('filter_type', 'stand-up')].label, rows[('filter_type', 'stand-up')].order) == ('Daily', 0)
    assert rows[('panel', 'knowledge_nav')].enabled is False
    assert (rows[('section', 'action_items')].label, rows[('section', 'action_items')].order) == (None, 0)


@pytest.mark.parametrize('item', [
    {'field': 'filter', 'value': 'tag'},
    {'field': 'filter_type', 'value': 'all-hands'},
    {'field': 'panel', 'value': 'upcoming_meetings'},
])
def test_system_config_rejects_invalid_meetings(api_client, user, project, item):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'meetings': {'options': [item]}}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not OptionConfig.objects.exists()
