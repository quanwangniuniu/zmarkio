"""Exercise real JWT middleware, tenant tables and the shared-account worker."""

from datetime import timedelta
from importlib import import_module
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from django.contrib.auth import get_user_model
from django.apps import apps
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.test import APIClient

from campaign.models import Campaign, CampaignPlatformIntegration
from core.models import Organization, OrganizationMembership, Project, ProjectMember
from core.services.auth_tokens import build_user_refresh_token
from core.services.tenant import rename_tenant_schema, slug_to_schema_name
from core.tenant_context import current_tenant_schema, tenant_schema_context
from facebook_integration.models import FacebookConnection, MetaAdAccount
from meta_ads.tasks import sync_single_ad_account
from notifications.models import Notification


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_demo():
    tenants = []
    for index in range(2):
        org = Organization.objects.create(name=f'MED-242-{uuid4().hex}')
        user = get_user_model().objects.create_user(
            username=f'med242-{index}', email=f'med242-{index}@example.com',
            password='demo-password', organization=org, current_organization=org,
            is_verified=True,
        )
        OrganizationMembership.objects.create(user=user, organization=org)
        schema = slug_to_schema_name(org.slug)
        with tenant_schema_context(schema):
            # Same numeric ID in two tenants; no matching public project.
            project = Project.objects.create(id=924242, name='Sync demo', organization=org, owner=user)
            ProjectMember.objects.create(project=project, user=user, role='owner')
            campaign = Campaign.objects.create(
                project=project, owner=user, creator=user, name='Sync demo',
                objective='CONVERSION', platforms=['META'], start_date=timezone.now().date(),
            )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_user_refresh_token(user).access_token}')
        tenants.append((org, user, schema, project, campaign, client))

    connection = FacebookConnection.objects.create(
        user=tenants[0][1], fb_user_id='tenant-demo',
        token_expires_at=timezone.now() - timedelta(hours=1),
    )
    account = MetaAdAccount.objects.create(connection=connection, meta_account_id='tenant-demo')
    with patch.object(FacebookConnection, 'get_access_token', return_value='demo-token'), patch(
        'notifications.services.maybe_dispatch_external_channels',
    ), patch('notifications.services._push_notification_to_redis'):
        yield tenants, account


def link_account(tenant, account):
    response = tenant[5].post(
        f'/api/facebook_integration/ad_accounts/{account.pk}/link_project/',
        {'project_id': tenant[3].pk}, format='json',
    )
    assert response.status_code == 200, response.data
    account.refresh_from_db()
    assert account.project_schema == tenant[2]


def test_real_tenant_link_expiry_notification_and_recovery(tenant_demo):
    tenants, account = tenant_demo
    org, owner, schema, project, campaign, client = tenants[0]
    link_account(tenants[0], account)
    # The worker starts in public, as it does in Celery.
    assert current_tenant_schema() == 'public'
    for _ in range(2):
        assert sync_single_ad_account(account.pk)['status'] == 'error'
    assert current_tenant_schema() == 'public'
    assert CampaignPlatformIntegration.objects.count() == 0
    with tenant_schema_context(tenants[1][2]):
        assert CampaignPlatformIntegration.objects.count() == 0
    notification = Notification.objects.get(metadata__action='campaign_platform_auth_error')
    assert notification.recipient_id == owner.pk
    assert notification.action_url == f'/{org.slug}/{project.slug}/campaigns/{campaign.slug}'
    detail = client.get(f'/api/campaigns/{campaign.slug}/')
    assert detail.status_code == 200, detail.data
    health = detail.data['platform_integrations'][0]
    assert health['last_sync_error'] == 'auth'
    assert health['can_reconnect'] is True
    assert health['last_sync_attempted_at'] is not None

    account.connection.token_expires_at = timezone.now() + timedelta(days=1)
    account.connection.save(update_fields=['token_expires_at'])
    with patch('meta_ads.services.graph_paged', return_value=[]):
        assert sync_single_ad_account(account.pk)['status'] == 'ok'
    health = client.get(f'/api/campaigns/{campaign.slug}/').data['platform_integrations'][0]
    assert health['last_sync_error'] == ''
    assert health['last_synced_at'] is not None


def test_transient_failure_in_tenant_does_not_notify(tenant_demo):
    tenants, account = tenant_demo
    link_account(tenants[0], account)
    account.connection.token_expires_at = None
    account.connection.save(update_fields=['token_expires_at'])
    with patch('meta_ads.services.sync_campaigns', side_effect=requests.Timeout()):
        sync_single_ad_account(account.pk)
    detail = tenants[0][5].get(f'/api/campaigns/{tenants[0][4].slug}/')
    assert detail.data['platform_integrations'][0]['last_sync_error'] == 'transient'
    assert Notification.objects.filter(metadata__action='campaign_platform_auth_error').count() == 0


def test_same_project_id_in_another_tenant_does_not_grant_account_access(tenant_demo):
    tenants, account = tenant_demo
    link_account(tenants[0], account)
    response = tenants[1][5].get('/api/facebook_integration/status/', {'project_id': tenants[1][3].pk})
    assert response.status_code == 200
    assert response.data.get('ad_accounts', []) == []
    response = tenants[1][5].post(
        f'/api/facebook_integration/ad_accounts/{account.pk}/link_project/',
        {'project_id': None}, format='json',
    )
    assert response.status_code == 404


def test_worker_uses_account_schema_after_connector_switches_organization(tenant_demo):
    tenants, account = tenant_demo
    link_account(tenants[0], account)
    owner = tenants[0][1]
    owner.current_organization = tenants[1][0]
    owner.save(update_fields=['current_organization'])
    with tenant_schema_context(tenants[1][2]):
        sync_single_ad_account(account.pk)
        assert current_tenant_schema() == tenants[1][2]
        assert CampaignPlatformIntegration.objects.count() == 0
    with tenant_schema_context(tenants[0][2]):
        assert CampaignPlatformIntegration.objects.get(ad_account=account).last_sync_error == 'auth'


def test_schema_rename_keeps_account_sync_in_its_organization(tenant_demo):
    tenants, account = tenant_demo
    link_account(tenants[0], account)
    org = tenants[0][0]
    new_slug = f'renamed-{uuid4().hex}'
    with transaction.atomic():
        rename_tenant_schema(org.slug, new_slug)
        org.slug = new_slug
        org.save(update_fields=['slug'])
    sync_single_ad_account(account.pk)
    with tenant_schema_context(slug_to_schema_name(new_slug)):
        assert CampaignPlatformIntegration.objects.get(ad_account=account).last_sync_error == 'auth'


def test_legacy_account_link_migrates_by_project_slug_not_numeric_id(tenant_demo):
    tenants, account = tenant_demo
    org, owner, schema, tenant_project, *_ = tenants[0]
    legacy_project = Project.objects.create(
        name='Legacy project', slug=tenant_project.slug, organization=org, owner=owner,
    )
    account.project = legacy_project
    account.save(update_fields=['project'])
    assert account.project_id != tenant_project.pk

    migration = import_module('facebook_integration.migrations.0003_account_project_schema')
    with connection.schema_editor() as editor:
        migration.backfill_project_schemas(apps, editor)
    account.refresh_from_db()
    assert account.project_schema == schema
    assert account.project_id == tenant_project.pk
    sync_single_ad_account(account.pk)
    with tenant_schema_context(schema):
        assert CampaignPlatformIntegration.objects.get(ad_account=account).last_sync_error == 'auth'


@pytest.mark.parametrize('delete_via_api', [False, True])
def test_deleting_same_id_project_only_unlinks_accounts_in_that_schema(tenant_demo, delete_via_api):
    tenants, account = tenant_demo
    link_account(tenants[0], account)
    other_connection = FacebookConnection.objects.create(user=tenants[1][1], fb_user_id='other')
    other_account = MetaAdAccount.objects.create(
        connection=other_connection, meta_account_id='other',
        project_id=tenants[1][3].pk, project_schema=tenants[1][2],
    )
    with tenant_schema_context(tenants[1][2]):
        tenants[1][4].delete()
        if not delete_via_api:
            tenants[1][3].delete()
    if delete_via_api:
        response = tenants[1][5].delete(f'/api/core/projects/{tenants[1][3].slug}/')
        assert response.status_code == 204, response.data
    account.refresh_from_db()
    other_account.refresh_from_db()
    assert account.project_id == tenants[0][3].pk
    assert other_account.project_id is None
