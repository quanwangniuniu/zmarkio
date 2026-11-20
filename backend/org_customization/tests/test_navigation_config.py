import pytest
from django.urls import reverse
from rest_framework import status

from org_customization.models import OptionConfig
from org_customization.services import NAVIGATION_GROUPS

pytestmark = pytest.mark.django_db


def _url(name, project):
    return f"{reverse(name)}?project_id={project.id}"


def test_get_returns_navigation_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_url('org-customization-navigation-config', project))

    assert response.status_code == status.HTTP_200_OK
    options = response.data['options']
    for group, items in NAVIGATION_GROUPS.items():
        assert [o['value'] for o in options if o['field'] == group] == [key for key, _ in items]
    assert [o['value'] for o in options if o['is_default']] == ['overview']


def test_system_config_saves_navigation(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'navigation': {'options': [
        {'field': 'manage', 'value': 'meta_ads', 'enabled': False},
        {'field': 'manage', 'value': 'tasks', 'label': 'Jobs', 'order': 0, 'color': '#000000'},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = {r.value: r for r in OptionConfig.objects.filter(module='navigation')}
    assert rows['meta_ads'].enabled is False
    assert (rows['tasks'].label, rows['tasks'].order, rows['tasks'].color) == ('Jobs', 0, None)


@pytest.mark.parametrize('item', [
    {'field': 'overview', 'value': 'overview', 'enabled': False},
    {'field': 'manage', 'value': 'agent'},
])
def test_system_config_rejects_invalid_navigation(api_client, user, project, item):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'navigation': {'options': [item]}}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not OptionConfig.objects.exists()
