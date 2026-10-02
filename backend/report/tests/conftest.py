"""Shared fixtures for custom KPI tests.

`kpi_warehouse` builds the full chain a Meta insight row travels to reach a
project: insight -> ad -> adset -> meta campaign -> mediajira campaign ->
project. It also seeds the two negative cases the aggregation must exclude --
another project, and a Meta campaign that was never linked to a MediaJira one.
"""

import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django
from django.conf import settings

if not settings.configured:
    django.setup()

from django.contrib.auth import get_user_model

from campaign.models import Campaign
from core.models import Organization, Project, ProjectMember
from facebook_integration.models import FacebookConnection, MetaAdAccount
from meta_ads.models import MetaAd, MetaAdSet, MetaCampaign, MetaInsightDaily

User = get_user_model()


def _make_ad(ad_account, mediajira_campaign, suffix):
    """One ad at the end of a fresh Meta campaign/adset chain."""
    meta_campaign = MetaCampaign.objects.create(
        ad_account=ad_account,
        meta_campaign_id=f"camp-{suffix}",
        name=f"Meta Campaign {suffix}",
        mediajira_campaign=mediajira_campaign,
    )
    adset = MetaAdSet.objects.create(
        campaign=meta_campaign,
        meta_adset_id=f"adset-{suffix}",
        name=f"Ad Set {suffix}",
    )
    return MetaAd.objects.create(
        adset=adset,
        meta_ad_id=f"ad-{suffix}",
        name=f"Ad {suffix}",
    )


@pytest.fixture
def kpi_user(db):
    return User.objects.create_user(
        username="kpi_owner",
        email="kpi_owner@example.com",
        password="testpass123",
    )


@pytest.fixture
def kpi_warehouse(db, kpi_user):
    organization = Organization.objects.create(name="KPI Org")
    project = Project.objects.create(
        name="KPI Project", organization=organization, owner=kpi_user
    )
    other_project = Project.objects.create(
        name="Other Project", organization=organization, owner=kpi_user
    )
    ProjectMember.objects.create(user=kpi_user, project=project, is_active=True)
    ProjectMember.objects.create(user=kpi_user, project=other_project, is_active=True)

    campaign = Campaign.objects.create(
        name="Q4 Push",
        objective=Campaign.Objective.CONVERSION,
        platforms=[Campaign.Platform.META],
        start_date=date.today() - timedelta(days=30),
        project=project,
        owner=kpi_user,
    )

    connection = FacebookConnection.objects.create(user=kpi_user, fb_user_id="fb-1")
    ad_account = MetaAdAccount.objects.create(
        connection=connection, meta_account_id="act-1", name="Ad Account"
    )

    linked_ad = _make_ad(ad_account, campaign, "linked")
    # Same ad account, but never linked to a MediaJira campaign -> no project.
    unlinked_ad = _make_ad(ad_account, None, "unlinked")

    for offset in range(3):
        MetaInsightDaily.objects.create(
            ad=linked_ad,
            date=date.today() - timedelta(days=offset),
            spend=Decimal("100.00"),
            revenue=Decimal("400.00"),
            impressions=2000,
            reach=1500,
            clicks=10,
            purchases=2,
            lpv_count=6,
        )
    MetaInsightDaily.objects.create(
        ad=unlinked_ad,
        date=date.today(),
        spend=Decimal("999.00"),
        revenue=Decimal("999.00"),
        clicks=999,
    )

    return {
        "user": kpi_user,
        "organization": organization,
        "project": project,
        "other_project": other_project,
        "campaign": campaign,
        "linked_ad": linked_ad,
        "unlinked_ad": unlinked_ad,
    }
