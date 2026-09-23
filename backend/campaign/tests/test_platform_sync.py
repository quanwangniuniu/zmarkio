"""Campaign sync health is fed by the real Meta sync entry points."""

from datetime import timedelta
from itertools import permutations
from unittest.mock import patch

import pytest
import requests
from django.utils import timezone

from campaign.models import Campaign, CampaignPlatformIntegration
from campaign.services import CampaignPlatformIntegrationService as IntegrationService
from core.models import ProjectMember
from facebook_integration.models import FacebookConnection, MetaAdAccount
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
        assert notification.event_type == 'account_permission'
        assert notification.metadata['action'] == 'campaign_platform_auth_error'
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
@pytest.mark.parametrize('auth_finishes_first', [True, False])
def test_overlapping_auth_and_timeout_preserve_warning(account, auth_finishes_first):
    older = IntegrationService.begin_sync(ad_account=account)
    newer = IntegrationService.begin_sync(ad_account=account)
    outcomes = [(older, MetaApiError('expired', 401)), (newer, requests.Timeout())]
    for attempt, error in outcomes if auth_finishes_first else reversed(outcomes):
        IntegrationService.finish_sync(ad_account=account, attempted_at=attempt, error=error)
    integration = CampaignPlatformIntegration.objects.get(ad_account=account)
    assert integration.last_sync_error == 'auth'
    assert integration.last_sync_attempted_at == newer
    assert Notification.objects.count() == 1

    recovery = IntegrationService.begin_sync(ad_account=account)
    IntegrationService.finish_sync(ad_account=account, attempted_at=recovery)
    # A delayed/repeated failure from before recovery must not reopen the warning.
    IntegrationService.finish_sync(ad_account=account, attempted_at=older, error=outcomes[0][1])
    integration.refresh_from_db()
    assert integration.last_sync_error == ''
    assert integration.last_synced_at == recovery
    assert Notification.objects.count() == 1


@pytest.mark.django_db
def test_newer_success_supersedes_late_auth_failure(account):
    older = IntegrationService.begin_sync(ad_account=account)
    newer = IntegrationService.begin_sync(ad_account=account)
    IntegrationService.finish_sync(ad_account=account, attempted_at=newer)
    IntegrationService.finish_sync(ad_account=account, attempted_at=older, error=MetaApiError('expired', 401))
    integration = CampaignPlatformIntegration.objects.get(ad_account=account)
    assert integration.last_sync_error == ''
    assert integration.last_synced_at == newer
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_older_success_does_not_hide_newer_auth_failure(account):
    older = IntegrationService.begin_sync(ad_account=account)
    newer = IntegrationService.begin_sync(ad_account=account)
    IntegrationService.finish_sync(ad_account=account, attempted_at=older)
    IntegrationService.finish_sync(ad_account=account, attempted_at=newer, error=MetaApiError('expired', 401))
    integration = CampaignPlatformIntegration.objects.get(ad_account=account)
    assert integration.last_sync_error == 'auth'
    assert integration.last_synced_at == older
    assert Notification.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize('completion_order', list(permutations(range(3))))
@pytest.mark.parametrize('outcomes', [
    ('auth', 'success', 'transient'),
    ('success', 'auth', 'transient'),
    ('auth', 'auth', 'success'),
    ('auth', 'success', 'auth'),
])
def test_overlapping_recovery_depends_on_attempt_order(account, completion_order, outcomes):
    attempts = [IntegrationService.begin_sync(ad_account=account) for _ in outcomes]
    errors = {'auth': MetaApiError('expired', 401), 'success': None, 'transient': requests.Timeout()}
    for index in completion_order:
        IntegrationService.finish_sync(
            ad_account=account, attempted_at=attempts[index], error=errors[outcomes[index]],
        )

    integration = CampaignPlatformIntegration.objects.get(ad_account=account)
    latest_auth = max(attempt for attempt, outcome in zip(attempts, outcomes) if outcome == 'auth')
    latest_success = max(attempt for attempt, outcome in zip(attempts, outcomes) if outcome == 'success')
    assert (integration.last_sync_error == 'auth') == (latest_auth > latest_success)
    if integration.last_sync_error == 'auth':
        assert integration.last_sync_error_at == latest_auth
    elif not integration.last_sync_error:
        assert integration.last_sync_error_at is None
    assert integration.last_synced_at == latest_success
    assert integration.last_sync_attempted_at == attempts[-1]


@pytest.mark.django_db
def test_delayed_start_cannot_replace_newer_attempt(account):
    original_filter = Campaign.objects.filter
    newer = None

    def start_newer_sync_before_loading_campaigns(*args, **kwargs):
        nonlocal newer
        with patch.object(Campaign.objects, 'filter', original_filter):
            newer = IntegrationService.begin_sync(ad_account=account)
        return original_filter(*args, **kwargs)

    with patch.object(Campaign.objects, 'filter', side_effect=start_newer_sync_before_loading_campaigns):
        older = IntegrationService.begin_sync(ad_account=account)

    integration = CampaignPlatformIntegration.objects.get(ad_account=account)
    assert older < newer
    assert integration.last_sync_attempted_at == newer
    IntegrationService.finish_sync(ad_account=account, attempted_at=newer, error=MetaApiError('expired', 401))
    IntegrationService.finish_sync(ad_account=account, attempted_at=older)
    integration.refresh_from_db()
    assert integration.last_sync_error == 'auth'
    assert Notification.objects.count() == 1


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
@pytest.mark.parametrize('removed_before_start', [True, False])
def test_removed_meta_platform_does_not_receive_sync_failures(account, campaign, member_client, removed_before_start):
    previous = IntegrationService.begin_sync(ad_account=account)
    IntegrationService.finish_sync(ad_account=account, attempted_at=previous)
    if not removed_before_start:
        attempt = IntegrationService.begin_sync(ad_account=account)
    campaign.platforms = [Campaign.Platform.GOOGLE_ADS]
    campaign.save(update_fields=['platforms'])
    if removed_before_start:
        attempt = IntegrationService.begin_sync(ad_account=account)
    IntegrationService.finish_sync(ad_account=account, attempted_at=attempt, error=MetaApiError('expired', 401))

    integration = CampaignPlatformIntegration.objects.get(ad_account=account)
    assert integration.last_sync_error == ''
    assert integration.last_synced_at == previous
    assert Notification.objects.count() == 0
    assert member_client.get(f'/api/campaigns/{campaign.slug}/').data['platform_integrations'] == []


@pytest.mark.django_db
def test_detail_exposes_read_only_health(account, campaign, member_client):
    integration = CampaignPlatformIntegration.objects.create(campaign=campaign, ad_account=account, last_sync_error='auth')
    url = f'/api/campaigns/{campaign.slug}/'
    detail = member_client.get(url)
    assert detail.status_code == 200
    health = detail.data['platform_integrations'][0]
    assert health['last_sync_error'] == 'auth'
    assert health['can_reconnect'] is True
    assert 'token' not in str(health)
    assert member_client.patch(url, {'platform_integrations': []}, format='json').status_code == 200
    integration.refresh_from_db()
    assert integration.last_sync_error == 'auth'


@pytest.mark.django_db
def test_health_requires_project_access_and_cta_requires_original_connector(account, campaign, outsider_client, user2):
    CampaignPlatformIntegration.objects.create(campaign=campaign, ad_account=account, last_sync_error='auth')
    assert outsider_client.get(f'/api/campaigns/{campaign.slug}/').status_code == 404
    ProjectMember.objects.create(project=campaign.project, user=user2, role='member')
    detail = outsider_client.get(f'/api/campaigns/{campaign.slug}/')
    assert detail.data['platform_integrations'][0]['can_reconnect'] is False


@pytest.mark.django_db
def test_relinked_account_no_longer_exposes_old_campaign_warning(account, campaign, member_client):
    CampaignPlatformIntegration.objects.create(campaign=campaign, ad_account=account, last_sync_error='auth')
    account.project = None
    account.save()
    response = member_client.get(f'/api/campaigns/{campaign.slug}/')
    assert response.data['platform_integrations'] == []
