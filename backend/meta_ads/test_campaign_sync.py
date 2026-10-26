"""MED-242: Meta sync failures surface on the linked project's campaigns."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from campaign.models import Campaign
from campaign.serializers import CampaignCreateSerializer
from core.models import Organization, Project, ProjectMember
from core.services.auth_tokens import build_user_refresh_token
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from facebook_integration.models import FacebookConnection, MetaAdAccount
from meta_ads.meta_client import MetaApiError
from meta_ads.services import _classify_sync_error, sync_ad_account
from meta_ads.tasks import sync_single_ad_account
from notifications.models import Notification

pytestmark = pytest.mark.django_db

EXPIRED = MetaApiError("access_token=secret expired", 400, {"error": {"code": 190, "type": "OAuthException"}})


def make_tenant():
    org = Organization.objects.create(name=f"MED-242 {uuid4().hex[:8]}")
    # TenantSchemaMiddleware caches org id -> slug in Redis, which xdist workers share.
    cache.set(f"tenant:slug:{org.pk}", org.slug, 300)
    user = get_user_model().objects.create_user(
        username=f"med242-{uuid4().hex[:8]}", email=f"{uuid4().hex}@example.com",
        password="x", organization=org, current_organization=org,
    )
    schema = slug_to_schema_name(org.slug)
    with tenant_schema_context(schema):
        project = Project.objects.create(name="Sync project", organization=org, owner=user)
        ProjectMember.objects.create(project=project, user=user, role="owner")
        campaign = Campaign.objects.create(
            name="Sync campaign", project=project, owner=user, creator=user,
            objective=Campaign.Objective.CONVERSION, platforms=[Campaign.Platform.META],
            start_date=timezone.now().date(),
        )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {build_user_refresh_token(user).access_token}")
    return SimpleNamespace(org=org, user=user, schema=schema, project=project, campaign=campaign, client=client)


@pytest.fixture
def tenant():
    return make_tenant()


@pytest.fixture
def account(tenant):
    fb = FacebookConnection.objects.create(user=tenant.user, fb_user_id=f"fb-{uuid4().hex[:8]}")
    return MetaAdAccount.objects.create(
        connection=fb, meta_account_id=uuid4().hex[:10], name="Test ad account", project_id=tenant.project.pk,
    )


def campaign_in(schema, campaign):
    with tenant_schema_context(schema):
        return Campaign.objects.get(pk=campaign.pk)


def sync_failing_with(account, error, tenant_schema="public"):
    with patch("meta_ads.services.sync_campaigns", side_effect=error):
        return sync_ad_account(account, "token", tenant_schema=tenant_schema)


def sync_successfully(account, tenant_schema="public"):
    with patch("meta_ads.services.graph_paged", return_value=[]):
        return sync_ad_account(account, "token", tenant_schema=tenant_schema)


@pytest.mark.parametrize("error, expected", [
    (EXPIRED, "auth"),
    (MetaApiError("invalid session", 400, {"error": {"code": "102"}}), "auth"),
    (MetaApiError("unauthorized", 401), "auth"),
    (MetaApiError("throttled", 400, {"error": {"code": 4, "type": "OAuthException"}}), "transient"),
    (MetaApiError("throttled", 429), "transient"),
    (MetaApiError("unavailable", 503), "transient"),
    (MetaApiError("bad request", 400, {"error": "boom"}), "transient"),
    (MetaApiError("bad request", 400, "not json"), "transient"),
    (requests.Timeout("timeout"), "transient"),
    (ValueError("unexpected"), "transient"),
])
def test_classify_sync_error(error, expected):
    assert _classify_sync_error(error) == expected


def test_auth_error_sets_last_sync_error_and_notifies_owner_once(tenant, account):
    for _ in range(2):
        assert sync_failing_with(account, EXPIRED, tenant.schema).status == "error"

    campaign = campaign_in(tenant.schema, tenant.campaign)
    assert campaign.last_sync_error == "auth"
    assert campaign.last_sync_attempted_at is not None
    assert campaign.updated_at == tenant.campaign.updated_at
    notification = Notification.objects.get()
    assert notification.recipient_id == tenant.user.pk
    assert notification.category == "INTEGRATIONS"
    assert notification.metadata["change_type"] == "campaign_sync_auth_error"
    assert notification.action_url == f"/campaigns/{tenant.campaign.slug}"
    assert "secret" not in notification.body


def test_transient_error_is_recorded_without_notification(tenant, account):
    sync_failing_with(account, requests.Timeout(), tenant.schema)
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_error == "transient"
    assert not Notification.objects.exists()


def test_transient_error_keeps_an_open_reconnect_warning(tenant, account):
    sync_failing_with(account, EXPIRED, tenant.schema)
    sync_failing_with(account, requests.Timeout(), tenant.schema)
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_error == "auth"


def test_success_clears_the_error_and_a_new_expiry_notifies_again(tenant, account):
    sync_failing_with(account, EXPIRED, tenant.schema)
    assert sync_successfully(account, tenant.schema).status == "ok"
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_error == ""

    sync_failing_with(account, EXPIRED, tenant.schema)
    assert Notification.objects.count() == 2


def test_success_clears_the_error_after_meta_is_removed_from_platforms(tenant, account):
    sync_failing_with(account, EXPIRED, tenant.schema)
    with tenant_schema_context(tenant.schema):
        Campaign.objects.filter(pk=tenant.campaign.pk).update(platforms=[Campaign.Platform.GOOGLE_ADS])
    sync_successfully(account, tenant.schema)
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_error == ""


def test_only_meta_campaigns_in_the_linked_project_get_sync_errors(tenant, account):
    with tenant_schema_context(tenant.schema):
        other_project = Project.objects.create(name="Other", organization=tenant.org, owner=tenant.user)
        ignored = [
            Campaign.objects.create(
                name="Ignored", project=project, owner=tenant.user, creator=tenant.user, is_deleted=deleted,
                objective="CONVERSION", platforms=platforms, start_date=timezone.now().date(),
            )
            for project, platforms, deleted in [
                (other_project, ["META"], False), (tenant.project, ["GOOGLE_ADS"], False), (tenant.project, ["META"], True),
            ]
        ]
    sync_failing_with(account, EXPIRED, tenant.schema)
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_error == "auth"
    for campaign in ignored:
        assert campaign_in(tenant.schema, campaign).last_sync_attempted_at is None
    assert Notification.objects.count() == 1


def test_sync_without_a_requesting_organization_leaves_org_campaigns_untouched(tenant, account):
    sync_failing_with(account, EXPIRED)
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_attempted_at is None
    assert not Notification.objects.exists()


def test_refresh_passes_the_requesting_organization_to_the_sync(tenant, account):
    with patch("meta_ads.views.sync_single_ad_account.delay") as delay:
        delay.return_value.id = "task-123"
        response = tenant.client.post(f"/api/meta_ads/ad_accounts/{account.pk}/sync/")
    assert response.status_code == 202, response.data
    delay.assert_called_once_with(account.pk, tenant_schema=tenant.schema)

    with patch.object(FacebookConnection, "get_access_token", return_value="token"), patch(
        "meta_ads.services.sync_campaigns", side_effect=EXPIRED,
    ):
        sync_single_ad_account(account.pk, tenant_schema=tenant.schema)
    assert campaign_in(tenant.schema, tenant.campaign).last_sync_error == "auth"


def test_campaign_api_exposes_the_sync_state_read_only(tenant, account):
    sync_failing_with(account, EXPIRED, tenant.schema)
    response = tenant.client.get(f"/api/campaigns/{tenant.campaign.slug}/")
    assert response.status_code == 200, response.data
    assert response.data["last_sync_error"] == "auth"
    assert response.data["last_sync_attempted_at"] is not None
    for field in ("last_sync_error", "last_sync_attempted_at"):
        assert CampaignCreateSerializer().fields[field].read_only


def test_linking_an_organization_project_passes_database_constraints(tenant, account):
    MetaAdAccount.objects.filter(pk=account.pk).update(project=None)
    # Django defers FK checks to commit, which tests never reach; check now instead.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    response = tenant.client.post(
        f"/api/facebook_integration/ad_accounts/{account.pk}/link_project/",
        {"project_id": tenant.project.pk}, format="json",
    )
    assert response.status_code == 200, response.data
    account.refresh_from_db()
    assert account.project_id == tenant.project.pk
