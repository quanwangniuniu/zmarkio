import pytest
from django.urls import reverse
from rest_framework import status

from org_customization.models import OptionConfig
from org_customization.services import CALENDAR_SURFACES

pytestmark = pytest.mark.django_db


def _url(name, project):
    return f"{reverse(name)}?project_id={project.id}"


def test_get_returns_calendar_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_url('org-customization-calendar-config', project))

    assert response.status_code == status.HTTP_200_OK
    options = response.data['options']
    for field, items in CALENDAR_SURFACES.items():
        assert [o['value'] for o in options if o['field'] == field] == [key for key, _ in items]
    assert all(o['enabled'] for o in options)
    assert [(o['field'], o['value']) for o in options if o['is_default']] == [('tab', 'week')]


def test_system_config_saves_calendar(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'calendar': {'options': [
        {'field': 'tab', 'value': 'year', 'enabled': False},
        {'field': 'tab', 'value': 'agenda', 'label': 'List', 'order': 0},
        {'field': 'section', 'value': 'scope_switch', 'enabled': False, 'label': 'ignored'},
        {'field': 'panel', 'value': 'sidebar', 'enabled': False},
        {'field': 'nav_item', 'value': 'booking_links', 'enabled': False},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = {(r.field, r.value): r for r in OptionConfig.objects.filter(module='calendar')}
    assert rows[('tab', 'year')].enabled is False
    assert (rows[('tab', 'agenda')].label, rows[('tab', 'agenda')].order) == ('List', 0)
    assert rows[('section', 'scope_switch')].enabled is False
    assert rows[('section', 'scope_switch')].label is None
    assert rows[('panel', 'sidebar')].enabled is False
    assert rows[('nav_item', 'booking_links')].enabled is False


@pytest.mark.parametrize('item', [
    {'field': 'tab', 'value': 'week', 'enabled': False},
    {'field': 'tab', 'value': 'quarter'},
    {'field': 'section', 'value': 'toolbar_print'},
    {'field': 'nav_item', 'value': 'booking_page'},
])
def test_system_config_rejects_invalid_calendar(api_client, user, project, item):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'calendar': {'options': [item]}}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not OptionConfig.objects.exists()
