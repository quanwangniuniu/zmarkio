"""
Unit tests for commit-12: fair-use cost aggregation and alert tasks.

Mock boundary: stripe_meta.tasks._send_alert_email
setUp pattern: Plan.objects.all().delete() silences the org signal.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Organization
from stripe_meta.models import LLMCallLog, OrgMonthlyCost, Plan, Subscription
from stripe_meta.tasks import aggregate_monthly_llm_cost, check_fair_use_alerts

User = get_user_model()

ALERT_PATH = 'stripe_meta.tasks._send_alert_email'


class FairUseTestBase(TestCase):
    """
    One org, one Team plan, current-month ym.
    Plan.objects.all().delete() before org creation so the signal is a no-op.
    """

    def setUp(self):
        Plan.objects.all().delete()

        self.org = Organization.objects.create(name='Fair Use Org', slug='fair-use-org')
        self.user = User.objects.create_user(
            email='fairuse@test.com', username='fairuseuser', password='pw',
        )
        self.user.organization = self.org
        self.user.save()

        self.plan = Plan.objects.create(
            name='Team',
            stripe_price_id='price_team_fu',
            base_price_cents=4900,
            monthly_token_quota=5_000_000,
        )
        self.ym = timezone.now().strftime('%Y-%m')

    def _make_log(self, total_cost_cents, normalized_tokens, success=True):
        return LLMCallLog.objects.create(
            organization=self.org,
            provider='ollama',
            model_name='gemini-2.0-flash',
            input_tokens=100,
            output_tokens=50,
            normalized_tokens=normalized_tokens,
            input_cost_cents=total_cost_cents // 2,
            output_cost_cents=total_cost_cents - total_cost_cents // 2,
            total_cost_cents=total_cost_cents,
            success=success,
        )

    def _make_sub(self, monthly_revenue_cents=4900):
        return Subscription.objects.create(
            organization=self.org,
            plan=self.plan,
            stripe_subscription_id='sub_fu_real',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True,
            is_internal=False,
            monthly_revenue_cents=monthly_revenue_cents,
        )

    def _make_cost_row(self, llm_cost_cents, total_tokens=100_000):
        return OrgMonthlyCost.objects.create(
            organization=self.org,
            year_month=self.ym,
            llm_cost_cents=llm_cost_cents,
            total_tokens=total_tokens,
        )


# ---------------------------------------------------------------------------
# aggregate_monthly_llm_cost
# ---------------------------------------------------------------------------

class AggregationTaskTest(FairUseTestBase):

    def test_aggregation_task(self):
        """
        Two success=True logs and one success=False log.
        Aggregate must sum only the successful ones.
        """
        self._make_log(total_cost_cents=15, normalized_tokens=150, success=True)
        self._make_log(total_cost_cents=30, normalized_tokens=300, success=True)
        self._make_log(total_cost_cents=100, normalized_tokens=1_000, success=False)

        aggregate_monthly_llm_cost()

        row = OrgMonthlyCost.objects.get(organization=self.org, year_month=self.ym)
        self.assertEqual(row.llm_cost_cents, 45)   # 15 + 30 only
        self.assertEqual(row.total_tokens, 450)    # 150 + 300 only

    def test_aggregation_task_idempotent(self):
        """Running twice overwrites — result is the same as running once."""
        self._make_log(total_cost_cents=20, normalized_tokens=200, success=True)

        aggregate_monthly_llm_cost()
        aggregate_monthly_llm_cost()

        self.assertEqual(OrgMonthlyCost.objects.filter(organization=self.org, year_month=self.ym).count(), 1)
        row = OrgMonthlyCost.objects.get(organization=self.org, year_month=self.ym)
        self.assertEqual(row.llm_cost_cents, 20)


# ---------------------------------------------------------------------------
# check_fair_use_alerts — paid subscription
# ---------------------------------------------------------------------------

class FairUseAlertPaidTest(FairUseTestBase):

    def test_fair_use_alert_paid(self):
        """
        Paid sub (revenue=4900). cost=2000 → ratio ≈ 0.41 > FAIR_USE_THRESHOLD_RATIO(0.30)
        → alert fired with org as first arg and tier='paid'.
        """
        self._make_sub(monthly_revenue_cents=4900)
        self._make_cost_row(llm_cost_cents=2000)

        with patch(ALERT_PATH) as mock_alert:
            check_fair_use_alerts()

        mock_alert.assert_called_once()
        org_arg, tier_arg = mock_alert.call_args[0][:2]
        self.assertEqual(org_arg, self.org)
        self.assertEqual(tier_arg, 'paid')


# ---------------------------------------------------------------------------
# check_fair_use_alerts — free user
# ---------------------------------------------------------------------------

class FairUseAlertFreeTest(FairUseTestBase):

    def test_fair_use_alert_free(self):
        """
        No real subscription (free tier). cost=300 > FREE_USER_MAX_COST_CENTS(200) → alert
        with tier='free'.
        """
        self._make_cost_row(llm_cost_cents=300)

        with patch(ALERT_PATH) as mock_alert:
            check_fair_use_alerts()

        mock_alert.assert_called_once()
        org_arg, tier_arg = mock_alert.call_args[0][:2]
        self.assertEqual(org_arg, self.org)
        self.assertEqual(tier_arg, 'free')


# ---------------------------------------------------------------------------
# check_fair_use_alerts — under threshold (no alert)
# ---------------------------------------------------------------------------

class FairUseNoAlertTest(FairUseTestBase):

    def test_fair_use_no_alert_under_threshold(self):
        """
        Paid sub (revenue=4900). cost=100 → ratio ≈ 0.02 — well below FAIR_USE_THRESHOLD_RATIO(0.30) → no alert.
        """
        self._make_sub(monthly_revenue_cents=4900)
        self._make_cost_row(llm_cost_cents=100)

        with patch(ALERT_PATH) as mock_alert:
            check_fair_use_alerts()

        mock_alert.assert_not_called()

    def test_fair_use_no_alert_free_under_threshold(self):
        """Free user cost=100 < FREE_USER_MAX_COST_CENTS(200) → no alert."""
        self._make_cost_row(llm_cost_cents=100)

        with patch(ALERT_PATH) as mock_alert:
            check_fair_use_alerts()

        mock_alert.assert_not_called()
