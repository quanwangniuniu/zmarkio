import datetime as dt
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember
from facebook_integration.models import FacebookConnection, MetaAdAccount

from .models import MetaAd, MetaAdCreative, MetaAdSet, MetaCampaign, MetaInsightDaily


class MetaAdsProjectSharedAccessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client = APIClient()
        self.org = Organization.objects.create(name="Meta Org")
        self.connector = User.objects.create_user(
            email="connector@example.com",
            username="connector",
            password="pw",
            organization=self.org,
        )
        self.member = User.objects.create_user(
            email="member@example.com",
            username="member",
            password="pw",
            organization=self.org,
        )
        self.manager = User.objects.create_user(
            email="manager@example.com",
            username="manager",
            password="pw",
            organization=self.org,
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.com",
            username="outsider",
            password="pw",
            organization=self.org,
        )
        self.project = Project.objects.create(
            name="Meta Project",
            organization=self.org,
            owner=self.connector,
        )
        ProjectMember.objects.create(
            user=self.connector,
            project=self.project,
            role="owner",
            is_active=True,
        )
        ProjectMember.objects.create(
            user=self.member,
            project=self.project,
            role="member",
            is_active=True,
        )
        ProjectMember.objects.create(
            user=self.manager,
            project=self.project,
            role="Campaign Manager",
            is_active=True,
        )
        self.connection = FacebookConnection.objects.create(
            user=self.connector,
            fb_user_id="fb-1",
            fb_user_name="Connector User",
            is_active=True,
        )
        self.account = MetaAdAccount.objects.create(
            connection=self.connection,
            meta_account_id="123",
            name="Shared Account",
            currency="USD",
            business_id="biz-shared",
            project=self.project,
        )
        self.unlinked_account = MetaAdAccount.objects.create(
            connection=self.connection,
            meta_account_id="456",
            name="Unlinked Account",
            currency="USD",
        )
        self.campaign = MetaCampaign.objects.create(
            ad_account=self.account,
            meta_campaign_id="cmp-1",
            name="Campaign",
        )
        self.adset = MetaAdSet.objects.create(
            campaign=self.campaign,
            meta_adset_id="set-1",
            name="Ad Set",
        )
        self.creative = MetaAdCreative.objects.create(
            ad_account=self.account,
            meta_creative_id="crt-1",
            name="Creative",
        )
        self.ad = MetaAd.objects.create(
            adset=self.adset,
            creative=self.creative,
            meta_ad_id="ad-1",
            name="Ad",
        )
        MetaInsightDaily.objects.create(
            ad=self.ad,
            date=dt.date.today(),
            spend="10.00",
            impressions=100,
            clicks=5,
            leads=1,
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def test_project_member_status_includes_shared_account(self):
        self.authenticate(self.member)
        response = self.client.get(
            "/api/facebook_integration/status/",
            {"project_id": self.project.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["connected"])
        accounts = response.data["ad_accounts"]
        self.assertEqual([account["id"] for account in accounts], [self.account.id])
        self.assertFalse(accounts[0]["connected_by_current_user"])
        self.assertFalse(accounts[0]["can_sync"])

    def test_project_member_can_list_shared_accounts_for_project(self):
        self.authenticate(self.member)
        response = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["page"], 1)
        self.assertEqual(response.data["page_size"], 5)
        self.assertEqual(
            [account["id"] for account in response.data["results"]],
            [self.account.id],
        )
        self.assertFalse(response.data["results"][0]["connected_by_current_user"])
        self.assertFalse(response.data["results"][0]["can_sync"])
        self.assertEqual(
            response.data["results"][0]["connector_name"],
            self.connector.username,
        )

    def test_non_member_cannot_list_project_accounts(self):
        self.authenticate(self.outsider)
        response = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_ad_account_list_search_filters_by_name_account_id_and_business_id(self):
        MetaAdAccount.objects.create(
            connection=self.connection,
            meta_account_id="987650",
            name="Retail Growth",
            currency="USD",
            business_id="biz-retail",
            project=self.project,
        )

        self.authenticate(self.member)

        by_name = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id, "search": "retail"},
        )
        by_account_id = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id, "search": "123"},
        )
        by_business_id = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id, "search": "biz-shared"},
        )

        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(
            [account["meta_account_id"] for account in by_name.data["results"]],
            ["987650"],
        )
        self.assertEqual(by_account_id.data["count"], 1)
        self.assertEqual(by_account_id.data["results"][0]["id"], self.account.id)
        self.assertEqual(by_business_id.data["count"], 1)
        self.assertEqual(by_business_id.data["results"][0]["id"], self.account.id)

    def test_ad_account_list_paginates_with_spreadsheets_shape(self):
        names = ["Alpha Account", "Bravo Account", "Charlie Account"]
        for index, name in enumerate(names, start=1):
            MetaAdAccount.objects.create(
                connection=self.connection,
                meta_account_id=f"page-{index}",
                name=name,
                currency="USD",
                project=self.project,
            )

        self.authenticate(self.member)
        response = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {
                "project_id": self.project.id,
                "page": 2,
                "page_size": 2,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 4)
        self.assertEqual(response.data["page"], 2)
        self.assertEqual(response.data["page_size"], 2)
        self.assertEqual(
            [account["name"] for account in response.data["results"]],
            ["Charlie Account", "Shared Account"],
        )

    def test_ad_account_list_unlinked_accounts_visible_only_to_connector(self):
        self.authenticate(self.member)
        member_response = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id},
        )
        member_ids = [account["id"] for account in member_response.data["results"]]
        self.assertNotIn(self.unlinked_account.id, member_ids)

        self.authenticate(self.connector)
        connector_response = self.client.get(
            "/api/facebook_integration/ad_accounts/",
            {"project_id": self.project.id},
        )
        connector_ids = [
            account["id"] for account in connector_response.data["results"]
        ]
        self.assertIn(self.unlinked_account.id, connector_ids)

    def test_project_member_can_read_shared_account_data(self):
        self.authenticate(self.member)
        response = self.client.get(
            "/api/meta_ads/summary/",
            {"ad_account": self.account.id, "days": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["ad_account_id"], self.account.id)
        self.assertEqual(response.data["aggregates"]["impressions"], 100)

    def test_outsider_cannot_read_shared_account_data(self):
        self.authenticate(self.outsider)
        response = self.client.get(
            "/api/meta_ads/summary/",
            {"ad_account": self.account.id, "days": 1},
        )

        self.assertEqual(response.status_code, 404)

    def test_unlinked_account_visible_only_to_connector(self):
        self.authenticate(self.member)
        member_response = self.client.get(
            "/api/facebook_integration/status/",
            {"project_id": self.project.id},
        )
        member_ids = [account["id"] for account in member_response.data["ad_accounts"]]
        self.assertNotIn(self.unlinked_account.id, member_ids)

        self.authenticate(self.connector)
        connector_response = self.client.get(
            "/api/facebook_integration/status/",
            {"project_id": self.project.id},
        )
        connector_ids = [
            account["id"] for account in connector_response.data["ad_accounts"]
        ]
        self.assertIn(self.unlinked_account.id, connector_ids)

    def test_ordinary_project_member_cannot_link_or_sync(self):
        self.authenticate(self.member)

        link_response = self.client.post(
            f"/api/facebook_integration/ad_accounts/{self.account.id}/link_project/",
            {"project_id": None},
            format="json",
        )
        sync_response = self.client.post(
            f"/api/meta_ads/ad_accounts/{self.account.id}/sync/",
        )

        self.assertEqual(link_response.status_code, 403)
        self.assertEqual(sync_response.status_code, 403)

    def test_project_manager_can_link_shared_account_to_project(self):
        other_project = Project.objects.create(
            name="Target Project",
            organization=self.org,
            owner=self.connector,
        )
        ProjectMember.objects.create(
            user=self.connector,
            project=other_project,
            role="owner",
            is_active=True,
        )
        ProjectMember.objects.create(
            user=self.manager,
            project=other_project,
            role="Campaign Manager",
            is_active=True,
        )

        self.authenticate(self.manager)
        response = self.client.post(
            f"/api/facebook_integration/ad_accounts/{self.account.id}/link_project/",
            {"project_id": other_project.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.project_id, other_project.id)
        self.assertTrue(response.data["can_manage"])
        self.assertTrue(response.data["can_sync"])

    @patch("meta_ads.views.sync_single_ad_account.delay")
    def test_project_manager_can_trigger_sync(self, delay_mock):
        delay_mock.return_value = Mock(id="task-123")
        self.authenticate(self.manager)

        response = self.client.post(
            f"/api/meta_ads/ad_accounts/{self.account.id}/sync/",
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "queued")
        delay_mock.assert_called_once_with(self.account.id, tenant_schema="public")

    def test_non_connector_cannot_disconnect_connector_facebook_connection(self):
        self.authenticate(self.manager)

        response = self.client.post("/api/facebook_integration/disconnect/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"connected": False})
        self.connection.refresh_from_db()
        self.assertTrue(self.connection.is_active)
