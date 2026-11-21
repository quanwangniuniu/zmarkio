import pytest
from django.urls import reverse
from rest_framework import status

from decision.models import Decision, Review, Signal
from org_customization.models import OptionConfig

pytestmark = pytest.mark.django_db

FIELDS = {
    'signal_metric': Signal.Metric,
    'signal_movement': Signal.Movement,
    'signal_period': Signal.Period,
    'review_quality': Review.DecisionQuality,
    'risk_level': Decision.RiskLevel,
}


def _url(name, project):
    return f"{reverse(name)}?project_id={project.id}"


def test_get_returns_decision_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_url('org-customization-decisions-config', project))

    assert response.status_code == status.HTTP_200_OK
    options = response.data['options']
    for field, choices in FIELDS.items():
        assert [o['value'] for o in options if o['field'] == field] == list(choices.values)
    editable = {o['field']: o['editable'] for o in options}
    assert editable['signal_metric'] == ['label', 'enabled', 'order']
    assert editable['risk_level'] == ['label', 'color']


def test_system_config_saves_decisions_and_ignores_locked_attributes(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'decisions': {'options': [
        {'field': 'signal_metric', 'value': 'PURCHASES', 'label': 'Orders', 'order': 0},
        {'field': 'signal_metric', 'value': 'AOV', 'enabled': False},
        {'field': 'risk_level', 'value': 'HIGH', 'label': 'Critical', 'color': '#DC2626', 'enabled': False},
        {'field': 'review_quality', 'value': 'POOR', 'label': 'Missed', 'order': 9},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = {(r.field, r.value): r for r in OptionConfig.objects.filter(module='decisions')}
    assert rows[('signal_metric', 'PURCHASES')].label == 'Orders'
    assert rows[('signal_metric', 'AOV')].enabled is False
    assert (rows[('risk_level', 'HIGH')].label, rows[('risk_level', 'HIGH')].enabled) == ('Critical', True)
    assert rows[('review_quality', 'POOR')].order == 0
    metrics = {o['value']: o for o in response.data['decisions']['options'] if o['field'] == 'signal_metric'}
    assert (metrics['PURCHASES']['label'], metrics['AOV']['enabled']) == ('Orders', False)


def test_system_config_rejects_unknown_decision_option(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'decisions': {'options': [
        {'field': 'signal_metric', 'value': 'NOT_A_METRIC'},
    ]}}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not OptionConfig.objects.exists()
