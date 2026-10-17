"""
Test cases for the budget pacing forecaster.

The pacing model itself is a pure function, so every case below pins an exact
date and an exact spend series — no `timezone.now()`, no randomness, no
tolerance for "roughly right".
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework import status
from rest_framework.test import APIClient

from campaign.models import Campaign
from core.models import Organization, Project, ProjectMember
from facebook_integration.models import FacebookConnection, MetaAdAccount
from meta_ads.models import MetaAd, MetaAdSet, MetaCampaign, MetaInsightDaily
from optimization.models import CampaignPacingForecast, PacingReason, PacingStatus
from optimization.services import PacingService, compute_dow_factors, forecast_pacing

User = get_user_model()


def flat_spend(first: date, last: date, amount) -> dict:
    """Build a {date: Decimal} series with the same spend every day."""
    series = {}
    current = first
    while current <= last:
        series[current] = Decimal(amount)
        current = date.fromordinal(current.toordinal() + 1)
    return series


def weekend_heavy_spend(first: date, last: date) -> dict:
    """Spend series that doubles on Saturdays and Sundays."""
    series = {}
    current = first
    while current <= last:
        series[current] = Decimal("200") if current.weekday() >= 5 else Decimal("100")
        current = date.fromordinal(current.toordinal() + 1)
    return series


class ForecastPacingConfigTest(SimpleTestCase):
    """Cases where there is nothing to forecast yet."""

    def test_missing_budget_is_not_configured(self):
        result = forecast_pacing(
            budget=None,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NOT_CONFIGURED)
        self.assertEqual(result.reason, PacingReason.MISSING_BUDGET)
        self.assertIsNone(result.projected_total_spend)

    def test_zero_budget_counts_as_missing(self):
        result = forecast_pacing(
            budget=Decimal("0"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NOT_CONFIGURED)
        self.assertEqual(result.reason, PacingReason.MISSING_BUDGET)

    def test_missing_end_date_is_not_configured(self):
        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 1),
            end_date=None,
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NOT_CONFIGURED)
        self.assertEqual(result.reason, PacingReason.MISSING_END_DATE)

    def test_missing_both_reports_combined_reason(self):
        result = forecast_pacing(
            budget=None,
            start_date=date(2026, 1, 1),
            end_date=None,
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NOT_CONFIGURED)
        self.assertEqual(result.reason, PacingReason.MISSING_BUDGET_AND_END_DATE)

    def test_end_before_start_is_invalid_period(self):
        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 10),
            end_date=date(2026, 1, 1),
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NOT_CONFIGURED)
        self.assertEqual(result.reason, PacingReason.INVALID_PERIOD)

    def test_future_campaign_is_not_started(self):
        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 2, 1),
            end_date=date(2026, 2, 10),
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NOT_STARTED)
        self.assertEqual(result.total_days, 10)
        self.assertEqual(result.days_remaining, 10)
        self.assertEqual(result.days_elapsed, 0)

    def test_no_spend_rows_is_no_data(self):
        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend={},
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.status, PacingStatus.NO_DATA)
        self.assertEqual(result.reason, PacingReason.NO_LINKED_SPEND)
        self.assertEqual(result.days_elapsed, 5)
        self.assertEqual(result.days_remaining, 5)


class ForecastPacingVerdictTest(SimpleTestCase):
    """A 10-day, 1000-budget campaign observed on day 5."""

    START = date(2026, 1, 1)
    END = date(2026, 1, 10)
    TODAY = date(2026, 1, 5)
    BUDGET = Decimal("1000")

    def run_forecast(self, daily_amount):
        return forecast_pacing(
            budget=self.BUDGET,
            start_date=self.START,
            end_date=self.END,
            daily_spend=flat_spend(self.START, self.TODAY, daily_amount),
            today=self.TODAY,
        )

    def test_exactly_on_budget_is_on_track(self):
        result = self.run_forecast(100)

        self.assertEqual(result.status, PacingStatus.ON_TRACK)
        self.assertEqual(result.spend_to_date, Decimal("500.00"))
        self.assertEqual(result.expected_spend_to_date, Decimal("500.00"))
        self.assertEqual(result.projected_total_spend, Decimal("1000.00"))
        self.assertEqual(result.pace_ratio, Decimal("1.0000"))
        self.assertEqual(result.avg_daily_spend, Decimal("100.00"))

    def test_overspending_is_over_pacing(self):
        result = self.run_forecast(150)

        self.assertEqual(result.status, PacingStatus.OVER_PACING)
        self.assertEqual(result.spend_to_date, Decimal("750.00"))
        self.assertEqual(result.projected_total_spend, Decimal("1500.00"))
        self.assertEqual(result.pace_ratio, Decimal("1.5000"))

    def test_underspending_is_under_pacing(self):
        result = self.run_forecast(50)

        self.assertEqual(result.status, PacingStatus.UNDER_PACING)
        self.assertEqual(result.projected_total_spend, Decimal("500.00"))
        self.assertEqual(result.pace_ratio, Decimal("0.5000"))

    def test_suggested_cap_spends_exactly_the_remaining_budget(self):
        result = self.run_forecast(150)

        # 250 left over 5 remaining days.
        self.assertEqual(result.suggested_daily_cap, Decimal("50.00"))
        landed = result.spend_to_date + result.suggested_daily_cap * result.days_remaining
        self.assertEqual(landed, self.BUDGET)

    def test_suggested_cap_floors_at_zero_when_budget_is_blown(self):
        result = self.run_forecast(300)

        self.assertEqual(result.status, PacingStatus.OVER_PACING)
        self.assertEqual(result.spend_to_date, Decimal("1500.00"))
        self.assertEqual(result.suggested_daily_cap, Decimal("0.00"))


class ForecastPacingEdgeCaseTest(SimpleTestCase):

    def test_todays_partial_spend_is_excluded_from_the_average(self):
        spend = flat_spend(date(2026, 1, 1), date(2026, 1, 4), 100)
        spend[date(2026, 1, 5)] = Decimal("10")  # today, still filling up

        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend=spend,
            today=date(2026, 1, 5),
        )

        # Today counts toward spend but must not drag the pace down.
        self.assertEqual(result.spend_to_date, Decimal("410.00"))
        self.assertEqual(result.avg_daily_spend, Decimal("100.00"))
        self.assertEqual(result.projected_total_spend, Decimal("910.00"))

    def test_first_day_falls_back_to_todays_spend(self):
        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend={date(2026, 1, 1): Decimal("50")},
            today=date(2026, 1, 1),
        )

        # No completed day exists yet, so today's partial spend is all there is.
        self.assertEqual(result.avg_daily_spend, Decimal("50.00"))
        self.assertEqual(result.days_elapsed, 1)
        self.assertEqual(result.days_remaining, 9)
        self.assertEqual(result.projected_total_spend, Decimal("500.00"))

    def test_finished_campaign_projects_actual_spend_and_suggests_no_cap(self):
        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend=flat_spend(date(2026, 1, 1), date(2026, 1, 10), 100),
            today=date(2026, 1, 15),
        )

        self.assertEqual(result.days_elapsed, 10)
        self.assertEqual(result.days_remaining, 0)
        self.assertEqual(result.spend_to_date, Decimal("1000.00"))
        self.assertEqual(result.projected_total_spend, Decimal("1000.00"))
        self.assertEqual(result.status, PacingStatus.ON_TRACK)
        self.assertIsNone(result.suggested_daily_cap)

    def test_spend_outside_the_campaign_period_is_ignored(self):
        spend = flat_spend(date(2026, 1, 1), date(2026, 1, 5), 100)
        spend[date(2025, 12, 25)] = Decimal("9999")

        result = forecast_pacing(
            budget=Decimal("1000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            daily_spend=spend,
            today=date(2026, 1, 5),
        )

        self.assertEqual(result.spend_to_date, Decimal("500.00"))


class SeasonalityTest(SimpleTestCase):
    """Day-of-week factors. 2026-01-01 is a Thursday."""

    def test_short_history_falls_back_to_linear(self):
        result = forecast_pacing(
            budget=Decimal("10000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            daily_spend=flat_spend(date(2026, 1, 1), date(2026, 1, 10), 100),
            today=date(2026, 1, 10),
        )

        # Only 9 completed days — below the 14-day minimum.
        self.assertFalse(result.seasonality_applied)
        self.assertEqual(result.dow_factors, {})

    def test_weekend_heavy_spend_produces_clamped_factors(self):
        # Four whole weeks of completed history: 2026-01-04 (Sun) .. 2026-01-31.
        result = forecast_pacing(
            budget=Decimal("10000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            daily_spend=weekend_heavy_spend(date(2026, 1, 1), date(2026, 1, 31)),
            today=date(2026, 2, 1),
        )

        self.assertTrue(result.seasonality_applied)
        # Mean over the 28-day window is (5*100 + 2*200)/7 = 128.57.
        self.assertAlmostEqual(result.dow_factors["0"], 100 / (900 / 7), places=4)
        # Weekend factor would be 1.556 — clamped to the 1.5 ceiling.
        self.assertEqual(result.dow_factors["5"], 1.5)
        self.assertEqual(result.dow_factors["6"], 1.5)

    def test_flat_spend_produces_neutral_factors(self):
        result = forecast_pacing(
            budget=Decimal("10000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 1),
            daily_spend=flat_spend(date(2026, 1, 1), date(2026, 2, 1), 100),
            today=date(2026, 2, 1),
        )

        self.assertTrue(result.seasonality_applied)
        self.assertEqual(set(result.dow_factors.values()), {1.0})

    def test_all_zero_spend_disables_seasonality(self):
        factors = compute_dow_factors(
            flat_spend(date(2026, 1, 1), date(2026, 2, 1), 0),
            first_day=date(2026, 1, 1),
            last_day=date(2026, 2, 1),
        )

        self.assertEqual(factors, {})

    def test_seasonal_projection_beats_linear_over_a_weekend(self):
        # 2026-02-06 is a Friday, so the only days left are Sat and Sun — both
        # high-factor. A flat average would under-project that tail.
        result = forecast_pacing(
            budget=Decimal("10000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 8),
            daily_spend=weekend_heavy_spend(date(2026, 1, 1), date(2026, 2, 6)),
            today=date(2026, 2, 6),
        )

        self.assertTrue(result.seasonality_applied)
        self.assertEqual(result.days_remaining, 2)
        linear_projection = result.spend_to_date + result.avg_daily_spend * result.days_remaining
        self.assertGreater(result.projected_total_spend, linear_projection)

    def test_seasonal_projection_trails_linear_over_a_work_week(self):
        # 2026-02-01 is a Sunday: the remaining Mon-Sun week is dominated by
        # low-factor weekdays, so seasonality projects below a flat average.
        result = forecast_pacing(
            budget=Decimal("10000"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 2, 8),
            daily_spend=weekend_heavy_spend(date(2026, 1, 1), date(2026, 2, 1)),
            today=date(2026, 2, 1),
        )

        self.assertTrue(result.seasonality_applied)
        self.assertEqual(result.days_remaining, 7)
        linear_projection = result.spend_to_date + result.avg_daily_spend * result.days_remaining
        self.assertLess(result.projected_total_spend, linear_projection)


class PacingServiceTest(TestCase):
    """Database-facing behaviour: the Meta join and forecast persistence."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='pacinguser',
            email='pacing@example.com',
            password='testpass123',
        )
        self.organization = Organization.objects.create(name='Pacing Org')
        self.project = Project.objects.create(
            name='Pacing Project',
            organization=self.organization,
        )
        self.campaign = Campaign.objects.create(
            name='Paced Campaign',
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            budget_estimate=Decimal('1000.00'),
            project=self.project,
            owner=self.user,
        )

        connection = FacebookConnection.objects.create(
            user=self.user,
            fb_user_id='fb-user-1',
        )
        self.ad_account = MetaAdAccount.objects.create(
            connection=connection,
            meta_account_id='act-1',
            project=self.project,
        )

    def link_meta_campaign(self, campaign, meta_campaign_id):
        meta_campaign = MetaCampaign.objects.create(
            ad_account=self.ad_account,
            meta_campaign_id=meta_campaign_id,
            name=f'Meta {meta_campaign_id}',
            mediajira_campaign=campaign,
        )
        adset = MetaAdSet.objects.create(
            campaign=meta_campaign,
            meta_adset_id=f'{meta_campaign_id}-adset',
            name='Adset',
        )
        return adset

    def add_spend(self, adset, ad_id, day, amount):
        ad, _ = MetaAd.objects.get_or_create(
            adset=adset,
            meta_ad_id=ad_id,
            defaults={'name': ad_id},
        )
        MetaInsightDaily.objects.create(ad=ad, date=day, spend=Decimal(amount))

    def test_build_daily_spend_sums_every_ad_on_a_date(self):
        adset = self.link_meta_campaign(self.campaign, 'mc-1')
        self.add_spend(adset, 'ad-a', date(2026, 1, 1), 60)
        self.add_spend(adset, 'ad-b', date(2026, 1, 1), 40)
        self.add_spend(adset, 'ad-a', date(2026, 1, 2), 100)

        series = PacingService.build_daily_spend(self.campaign, through=date(2026, 1, 5))

        self.assertEqual(series[date(2026, 1, 1)], Decimal('100.00'))
        self.assertEqual(series[date(2026, 1, 2)], Decimal('100.00'))

    def test_build_daily_spend_excludes_other_campaigns(self):
        adset = self.link_meta_campaign(self.campaign, 'mc-1')
        self.add_spend(adset, 'ad-a', date(2026, 1, 1), 100)

        other_campaign = Campaign.objects.create(
            name='Other Campaign',
            objective=Campaign.Objective.TRAFFIC,
            platforms=[Campaign.Platform.META],
            start_date=date(2026, 1, 1),
            project=self.project,
            owner=self.user,
        )
        other_adset = self.link_meta_campaign(other_campaign, 'mc-2')
        self.add_spend(other_adset, 'ad-z', date(2026, 1, 1), 999)

        series = PacingService.build_daily_spend(self.campaign, through=date(2026, 1, 5))

        self.assertEqual(series[date(2026, 1, 1)], Decimal('100.00'))

    def test_build_daily_spend_stops_at_the_through_date(self):
        adset = self.link_meta_campaign(self.campaign, 'mc-1')
        self.add_spend(adset, 'ad-a', date(2026, 1, 1), 100)
        self.add_spend(adset, 'ad-a', date(2026, 1, 9), 100)

        series = PacingService.build_daily_spend(self.campaign, through=date(2026, 1, 5))

        self.assertNotIn(date(2026, 1, 9), series)

    def test_recompute_persists_an_actionable_forecast(self):
        adset = self.link_meta_campaign(self.campaign, 'mc-1')
        for day in range(1, 6):
            self.add_spend(adset, 'ad-a', date(2026, 1, day), 150)

        forecast = PacingService.recompute_for_campaign(
            self.campaign, today=date(2026, 1, 5)
        )

        self.assertEqual(forecast.status, PacingStatus.OVER_PACING)
        self.assertEqual(forecast.spend_to_date, Decimal('750.00'))
        self.assertEqual(forecast.suggested_daily_cap, Decimal('50.00'))
        self.assertEqual(forecast.computed_for_date, date(2026, 1, 5))
        self.assertEqual(self.campaign.pacing_forecast.pk, forecast.pk)

    def test_recompute_is_idempotent(self):
        adset = self.link_meta_campaign(self.campaign, 'mc-1')
        self.add_spend(adset, 'ad-a', date(2026, 1, 1), 100)

        first = PacingService.recompute_for_campaign(self.campaign, today=date(2026, 1, 5))
        second = PacingService.recompute_for_campaign(self.campaign, today=date(2026, 1, 6))

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CampaignPacingForecast.objects.filter(campaign=self.campaign).count(), 1)
        self.assertEqual(second.computed_for_date, date(2026, 1, 6))

    def test_campaign_without_meta_link_reports_no_data(self):
        forecast = PacingService.recompute_for_campaign(
            self.campaign, today=date(2026, 1, 5)
        )

        self.assertEqual(forecast.status, PacingStatus.NO_DATA)
        self.assertEqual(forecast.reason, PacingReason.NO_LINKED_SPEND)

    def test_campaign_without_budget_reports_not_configured(self):
        # Queryset update, not save(): Campaign.save() full_cleans on update and
        # would reject a PLANNING campaign whose start date is in the past.
        # Re-fetch rather than refresh_from_db(), which trips the protected
        # FSM status field.
        Campaign.objects.filter(pk=self.campaign.pk).update(budget_estimate=None)
        campaign = Campaign.objects.get(pk=self.campaign.pk)

        forecast = PacingService.recompute_for_campaign(
            campaign, today=date(2026, 1, 5)
        )

        self.assertEqual(forecast.status, PacingStatus.NOT_CONFIGURED)
        self.assertEqual(forecast.reason, PacingReason.MISSING_BUDGET)


class PacingAPITest(TestCase):
    """Endpoint wiring, access scoping and the campaign payload passenger."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='pacing_api_user',
            email='pacing_api@example.com',
            password='testpass123',
        )
        self.outsider = User.objects.create_user(
            username='pacing_outsider',
            email='pacing_outsider@example.com',
            password='testpass123',
        )
        self.organization = Organization.objects.create(name='Pacing API Org')
        self.project = Project.objects.create(
            name='Pacing API Project',
            organization=self.organization,
            owner=self.user,
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, is_active=True
        )
        self.campaign = Campaign.objects.create(
            name='API Paced Campaign',
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 10),
            budget_estimate=Decimal('1000.00'),
            project=self.project,
            owner=self.user,
        )
        self.client.force_authenticate(user=self.user)

    def test_get_computes_a_forecast_on_first_read(self):
        self.assertFalse(
            CampaignPacingForecast.objects.filter(campaign=self.campaign).exists()
        )

        response = self.client.get(
            f'/api/optimization/campaigns/{self.campaign.slug}/pacing/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['campaign_slug'], self.campaign.slug)
        self.assertEqual(response.data['status'], PacingStatus.NO_DATA)
        self.assertTrue(
            CampaignPacingForecast.objects.filter(campaign=self.campaign).exists()
        )

    def test_recompute_returns_a_fresh_forecast(self):
        response = self.client.post(
            f'/api/optimization/campaigns/{self.campaign.slug}/pacing/recompute/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], PacingStatus.NO_DATA)
        self.assertEqual(
            CampaignPacingForecast.objects.filter(campaign=self.campaign).count(), 1
        )

    def test_unknown_campaign_is_404(self):
        response = self.client.get('/api/optimization/campaigns/no-such-campaign/pacing/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_member_cannot_read_pacing(self):
        self.client.force_authenticate(user=self.outsider)

        response = self.client.get(
            f'/api/optimization/campaigns/{self.campaign.slug}/pacing/'
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_rejected(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(
            f'/api/optimization/campaigns/{self.campaign.slug}/pacing/'
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_campaign_payload_carries_the_forecast(self):
        PacingService.recompute_for_campaign(self.campaign, today=date(2026, 1, 5))

        response = self.client.get(f'/api/campaigns/{self.campaign.slug}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['pacing'])
        self.assertEqual(response.data['pacing']['status'], PacingStatus.NO_DATA)

    def test_campaign_payload_pacing_is_null_before_first_compute(self):
        response = self.client.get(f'/api/campaigns/{self.campaign.slug}/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['pacing'])

    def editable_campaign(self, **fields):
        """A campaign the update endpoint will accept.

        Campaign.save() full_cleans on update and rejects a PLANNING campaign
        whose start date has passed, so move it on via a queryset update (the
        FSM status field is protected against direct assignment).
        """
        campaign = Campaign.objects.create(
            name='Editable Paced Campaign',
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META],
            start_date=date(2026, 1, 1),
            project=self.project,
            owner=self.user,
            # full_clean on update rejects a blank creator; API-created campaigns always have one.
            creator=self.user,
            **fields,
        )
        Campaign.objects.filter(pk=campaign.pk).update(status=Campaign.Status.TESTING)
        return Campaign.objects.get(pk=campaign.pk)

    def test_updating_budget_recomputes_pacing_immediately(self):
        campaign = self.editable_campaign(end_date=date(2026, 1, 10))
        PacingService.recompute_for_campaign(campaign)
        self.assertEqual(campaign.pacing_forecast.status, PacingStatus.NOT_CONFIGURED)

        response = self.client.patch(
            f'/api/campaigns/{campaign.slug}/',
            {'budget_estimate': '5000.00'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        forecast = CampaignPacingForecast.objects.get(campaign=campaign)
        self.assertEqual(forecast.status, PacingStatus.NO_DATA)
        self.assertEqual(forecast.budget, Decimal('5000.00'))

    def test_unrelated_update_does_not_touch_pacing(self):
        campaign = self.editable_campaign(
            end_date=date(2026, 1, 10), budget_estimate=Decimal('5000.00')
        )

        response = self.client.patch(
            f'/api/campaigns/{campaign.slug}/',
            {'name': 'Renamed Paced Campaign'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(CampaignPacingForecast.objects.filter(campaign=campaign).exists())
