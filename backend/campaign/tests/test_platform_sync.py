"""Campaign sync health is fed by the real Meta sync entry points."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from django.utils import timezone

from campaign.models import Campaign, CampaignPlatformIntegration
from campaign.services import CampaignPlatformIntegrationService as IntegrationService
from core.models import ProjectMember
from facebook_integration.models import FacebookConnection, MetaAdAccount
from facebook_integration.services import unpack_oauth_state
from meta_ads.meta_client import MetaApiError
from meta_ads.services import sync_ad_account
from meta_ads.tasks import sync_all_meta_connections, sync_single_ad_account
from notifications.models import Notification


@pytest.fixture
def campaign(project, user):
    return Campaign.objects.create(
        name='Campaign sync test', project=project, owner=user, creator=user,
        objective=Campaign.Objective.CONVERSION, platforms=[Campaign.Platform.META],
        start_date=timezone.now().date(),
    )


@pytest.fixture
def account(campaign, user):
    connection = FacebookConnection.objects.create(user=user, fb_user_id='fb-owner')
    return MetaAdAccount.objects.create(
        connection=connection, project=campaign.project, meta_account_id='123', name='Test ad account',
    )


@pytest.fixture(autouse=True)
def isolate_notification_channels():
    with patch('notifications.services.maybe_dispatch_external_channels'), patch(
        'notifications.services._push_notification_to_redis',
    ):
        yield


@pytest.mark.parametrize('error, expected', [
    (MetaApiError('expired', 400, {'error': {'code': 190}}), 'auth'),
    (MetaApiError('invalid session', 400, {'error': {'code': '102'}}), 'auth'),
    (MetaApiError('unauthorized', 401), 'auth'),
    (MetaApiError('revoked permission', 403), 'auth'),
    (MetaApiError('throttled', 429, {'error': {'type': 'OAuthException'}}), 'transient'),
    (MetaApiError('throttled', 400, {'error': {'code': 4, 'type': 'OAuthException'}}), 'transient'),
    (MetaApiError('unavailable', 503), 'transient'),
    (MetaApiError('temporary', 400, {'error': {'is_transient': True}}), 'transient'),
    (requests.Timeout('token=secret'), 'transient'),
    (requests.ConnectionError('offline'), 'transient'),
    (MetaApiError('bad request', 400, 'not json'), 'unknown'),
    (ValueError('unexpected'), 'unknown'),
])
def test_classify_sync_error(error, expected):
    assert IntegrationService.classify_sync_error(error) == expected


def test_http_auth_error_with_falsey_response():
    response = requests.Response()
    response.status_code = 400
    response._content = b'{"error":{"code":190}}'
    assert IntegrationService.classify_sync_error(requests.HTTPError(response=response)) == 'auth'


@pytest.mark.django_db
@pytest.mark.parametrize('error, expected, notifications', [
    (MetaApiError('access_token=secret', 400, {'error': {'code': 190}}), 'auth', 1),
    (MetaApiError('server down', 503), 'transient', 0),
    (requests.Timeout('timeout'), 'transient', 0),
    (ValueError('unexpected'), 'unknown', 0),
])
def test_worker_records_failure_and_notifies_only_for_auth(account, campaign, error, expected, notifications):
    with patch('meta_ads.services.sync_campaigns', side_effect=error):
        run = sync_ad_account(account, 'token')
    integration = CampaignPlatformIntegration.objects.get(campaign=campaign, ad_account=account)
    assert run.status == 'error'
    assert integration.last_sync_error == expected
    assert integration.last_sync_attempted_at is not None
    assert integration.last_synced_at is None
    assert Notification.objects.count() == notifications
    if notifications:
        notification = Notification.objects.get()
        assert notification.recipient_id == campaign.owner_id
        assert notification.event_type == 'campaign_platform_auth_error'
        assert notification.category == 'INTEGRATIONS'
        assert campaign.slug in notification.action_url
        assert 'secret' not in notification.body


@pytest.mark.django_db
def test_auth_episode_deduplicates_until_success_and_preserves_stale_metrics(account, campaign):
    previous_success = timezone.now() - timedelta(days=1)
    integration = CampaignPlatformIntegration.objects.create(
        campaign=campaign, ad_account=account, last_synced_at=previous_success,
    )
    for error in [MetaApiError('expired', 401), MetaApiError('expired', 401), requests.Timeout()]:
        attempt = IntegrationService.begin_sync(ad_account=account)
        IntegrationService.finish_sync(ad_account=account, attempted_at=attempt, error=error)
    integration.refresh_from_db()
    assert integration.last_sync_error == 'auth'
    assert integration.last_synced_at == previous_success
    assert Notification.objects.count() == 1

    # Exercise the actual successful orchestration, including clearing the warning.
    with patch('meta_ads.services.graph_paged', return_value=[]):
        assert sync_ad_account(account, 'replacement-token').status == 'ok'
    integration.refresh_from_db()
    assert integration.last_sync_error == ''
    assert integration.last_synced_at > previous_success
    with patch('meta_ads.services.sync_campaigns', side_effect=MetaApiError('expired again', 401)):
        sync_ad_account(account, 'token')
    assert Notification.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize('missing', [True, False])
def test_missing_or_locally_expired_token_is_recorded_without_provider_request(account, missing):
    if not missing:
        account.connection.token_expires_at = timezone.now() - timedelta(minutes=1)
        account.connection.save()
    with patch.object(FacebookConnection, 'get_access_token', return_value=None if missing else 'token'), patch(
        'meta_ads.services.sync_campaigns',
    ) as fetch:
        result = sync_single_ad_account(account.id)
    assert result['status'] == 'error'
    assert CampaignPlatformIntegration.objects.get(ad_account=account).last_sync_error == 'auth'
    assert Notification.objects.count() == 1
    fetch.assert_not_called()


@pytest.mark.django_db
def test_scheduled_sync_records_missing_token_and_does_not_advance_success(account):
    with patch.object(FacebookConnection, 'get_access_token', return_value=None):
        result = sync_all_meta_connections()
    assert result == {'connections': 1, 'ad_accounts': 1, 'errors': 1}
    account.connection.refresh_from_db()
    assert account.connection.last_synced_at is None
    assert CampaignPlatformIntegration.objects.get(ad_account=account).last_sync_error == 'auth'


@pytest.mark.django_db
def test_older_completion_cannot_clear_newer_failure(account):
    older = IntegrationService.begin_sync(ad_account=account)
    newer = IntegrationService.begin_sync(ad_account=account)
    IntegrationService.finish_sync(ad_account=account, attempted_at=newer, error=MetaApiError('expired', 401))
    IntegrationService.finish_sync(ad_account=account, attempted_at=older)
    assert CampaignPlatformIntegration.objects.get(ad_account=account).last_sync_error == 'auth'


@pytest.mark.django_db
def test_only_matching_project_and_platform_receive_sync_state(account, campaign, organization, user):
    from core.models import Project

    other_project = Project.objects.create(name='Unrelated project', organization=organization)
    Campaign.objects.create(name='Other campaign', project=other_project, owner=user,
                           objective='CONVERSION', platforms=['META'], start_date=timezone.now().date())
    Campaign.objects.create(name='Google campaign', project=campaign.project, owner=user,
                           objective='CONVERSION', platforms=['GOOGLE_ADS'], start_date=timezone.now().date())
    IntegrationService.begin_sync(ad_account=account)
    assert list(CampaignPlatformIntegration.objects.values_list('campaign_id', flat=True)) == [campaign.id]


@pytest.mark.django_db
def test_detail_exposes_read_only_health_and_reconnect_starts_existing_oauth(account, campaign, member_client, settings):
    settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    settings.FB_APP_ID = 'test-app'
    settings.FB_CONFIG_ID = 'test-config'
    settings.FB_REDIRECT_URI = 'https://app.example/api/facebook_integration/callback/'
    integration = CampaignPlatformIntegration.objects.create(campaign=campaign, ad_account=account, last_sync_error='auth')
    detail = member_client.get(f'/api/campaigns/{campaign.slug}/')
    assert detail.status_code == 200
    health = detail.data['platform_integrations'][0]
    assert health['last_sync_error'] == 'auth'
    assert health['can_reconnect'] is True
    assert health['platform'] == 'META'
    assert 'token' not in str(health)
    response = member_client.post(f'/api/campaigns/{campaign.slug}/platform-integrations/{integration.pk}/reconnect/')
    assert response.status_code == 200
    parsed = urlparse(response.data['authorize_url'])
    assert parsed.netloc == 'www.facebook.com'
    query = parse_qs(parsed.query)
    payload = unpack_oauth_state(query['state'][0])
    assert payload['user_id'] == account.connection.user_id
    assert payload['project_id'] == campaign.project_id
    assert query['redirect_uri'] == [settings.FB_REDIRECT_URI]
    integration.refresh_from_db()
    assert integration.last_sync_error == 'auth'


@pytest.mark.django_db
def test_reconnect_requires_project_access_and_original_connector(account, campaign, outsider_client, user2):
    integration = CampaignPlatformIntegration.objects.create(campaign=campaign, ad_account=account, last_sync_error='auth')
    url = f'/api/campaigns/{campaign.slug}/platform-integrations/{integration.pk}/reconnect/'
    assert outsider_client.post(url).status_code == 404
    ProjectMember.objects.create(project=campaign.project, user=user2, role='member')
    detail = outsider_client.get(f'/api/campaigns/{campaign.slug}/')
    assert detail.data['platform_integrations'][0]['can_reconnect'] is False
    assert outsider_client.post(url).status_code == 403


@pytest.mark.django_db
def test_relinked_account_no_longer_exposes_old_campaign_warning(account, campaign, member_client):
    integration = CampaignPlatformIntegration.objects.create(campaign=campaign, ad_account=account, last_sync_error='auth')
    account.project = None
    account.save()
    response = member_client.get(f'/api/campaigns/{campaign.slug}/')
    assert response.data['platform_integrations'] == []
    assert member_client.post(
        f'/api/campaigns/{campaign.slug}/platform-integrations/{integration.pk}/reconnect/',
    ).status_code == 404
