import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers, status

from campaign.models import Campaign, PerformanceCheckIn, PerformanceSnapshot
from org_customization.models import OptionConfig

pytestmark = pytest.mark.django_db

FIELDS = {
    'objective': Campaign.Objective,
    'platform': Campaign.Platform,
    'snapshot_milestone': PerformanceSnapshot.MilestoneType,
    'snapshot_metric': PerformanceSnapshot.MetricType,
    'status': Campaign.Status,
    'checkin_sentiment': PerformanceCheckIn.Sentiment,
}


def _url(name, project):
    return f"{reverse(name)}?project_id={project.id}"


def _hide(project, field, value):
    OptionConfig.objects.create(
        organization_id=project.organization_id, project_id=project.id,
        module='campaigns', field=field, value=value, enabled=False,
    )


def _campaign(project, user, **fields):
    return Campaign.objects.create(**{
        'name': 'Spring', 'project': project, 'owner': user, 'creator': user, 'objective': Campaign.Objective.CONVERSION,
        'platforms': [Campaign.Platform.META], 'start_date': timezone.now().date(), **fields,
    })


def test_get_returns_campaign_defaults(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.get(_url('org-customization-campaigns-config', project))

    assert response.status_code == status.HTTP_200_OK
    options = response.data['options']
    for field, choices in FIELDS.items():
        assert [o['value'] for o in options if o['field'] == field] == list(choices.values)
    editable = {o['field']: o['editable'] for o in options}
    assert editable['platform'] == ['label', 'enabled', 'order']
    assert editable['status'] == ['label', 'color']


def test_system_config_saves_campaigns_and_ignores_locked_attributes(api_client, user, project):
    api_client.force_authenticate(user=user)

    response = api_client.put(_url('org-customization-system-config', project), {'campaigns': {'options': [
        {'field': 'platform', 'value': 'PINTEREST', 'enabled': False},
        {'field': 'objective', 'value': 'LEAD_GENERATION', 'label': 'Leads'},
        {'field': 'status', 'value': 'SCALING', 'label': 'Growing', 'color': '#16A34A', 'enabled': False},
    ]}}, format='json')

    assert response.status_code == status.HTTP_200_OK, response.data
    rows = {(r.field, r.value): r for r in OptionConfig.objects.filter(module='campaigns')}
    assert rows[('platform', 'PINTEREST')].enabled is False
    assert rows[('objective', 'LEAD_GENERATION')].label == 'Leads'
    assert (rows[('status', 'SCALING')].label, rows[('status', 'SCALING')].enabled) == ('Growing', True)


def test_campaign_save_rejects_hidden_platform_but_keeps_existing(project, user):
    campaign = _campaign(project, user, platforms=[Campaign.Platform.META, Campaign.Platform.TIKTOK])
    _hide(project, 'platform', Campaign.Platform.TIKTOK)
    _hide(project, 'platform', Campaign.Platform.PINTEREST)

    campaign.platforms = [Campaign.Platform.TIKTOK, Campaign.Platform.GOOGLE_ADS]
    campaign.save()

    campaign.platforms = [Campaign.Platform.TIKTOK, Campaign.Platform.PINTEREST]
    with pytest.raises(serializers.ValidationError):
        campaign.save()
    with pytest.raises(serializers.ValidationError):
        _campaign(project, user, platforms=[Campaign.Platform.TIKTOK])


def test_snapshot_and_check_in_reject_hidden_options(project, user):
    campaign = _campaign(project, user)
    _hide(project, 'snapshot_metric', PerformanceSnapshot.MetricType.CPC)

    with pytest.raises(serializers.ValidationError):
        PerformanceSnapshot.objects.create(campaign=campaign, spend=10, metric_type='CPC', metric_value=1)
    PerformanceSnapshot.objects.create(campaign=campaign, spend=10, metric_type='CTR', metric_value=1)
    PerformanceCheckIn.objects.create(campaign=campaign, sentiment=PerformanceCheckIn.Sentiment.NEGATIVE)


def test_create_campaign_api_rejects_hidden_objective(api_client, user, project, monkeypatch):
    monkeypatch.setattr('campaign.views.CampaignTaskIntegrationService.on_campaign_created', lambda *args, **kwargs: None)
    api_client.force_authenticate(user=user)
    _hide(project, 'objective', Campaign.Objective.TRAFFIC)

    response = api_client.post('/api/campaigns/', {
        'name': 'Spring', 'objective': 'TRAFFIC', 'platforms': ['META'],
        'start_date': timezone.now().date().isoformat(), 'project_id': project.id, 'owner_id': user.id,
    }, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'objective' in response.data
    assert not Campaign.objects.exists()
