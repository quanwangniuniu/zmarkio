from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Mapping, Optional

from django.db.models import Sum
from django.utils import timezone

from .models import (
    CampaignPacingForecast,
    OptimizationExperiment,
    PacingReason,
    PacingStatus,
    RollbackHistory,
)


class ExperimentService:
    """Service layer for experiment-related operations"""
    
    @staticmethod
    def check_experiment_exists(experiment_id):
        """
        Check if experiment exists by ID
        
        Args:
            experiment_id (int): The experiment ID
            
        Returns:
            experiment: OptimizationExperiment instance or None if not found
        """
        try:
            experiment = OptimizationExperiment.objects.get(id=experiment_id)
            return experiment
        except OptimizationExperiment.DoesNotExist:
            return None
    
    @staticmethod
    def validate_experiment_status_transition(current_status, new_status):
        """
        Validate experiment status transition business rules
        
        Args:
            current_status (str): Current experiment status
            new_status (str): New experiment status
            
        Returns:
            tuple: (is_valid, error_message) where is_valid is bool and error_message is str or None
        """
        # Define valid status transitions
        valid_transitions = {
            OptimizationExperiment.ExperimentStatus.RUNNING: [
                OptimizationExperiment.ExperimentStatus.PAUSED,
                OptimizationExperiment.ExperimentStatus.COMPLETED,
                OptimizationExperiment.ExperimentStatus.ROLLED_BACK
            ],
            OptimizationExperiment.ExperimentStatus.PAUSED: [
                OptimizationExperiment.ExperimentStatus.RUNNING,
                OptimizationExperiment.ExperimentStatus.COMPLETED,
                OptimizationExperiment.ExperimentStatus.ROLLED_BACK
            ],
            OptimizationExperiment.ExperimentStatus.COMPLETED: [
                # No valid transitions from completed
            ],
            OptimizationExperiment.ExperimentStatus.ROLLED_BACK: [
                # No valid transitions from rolled_back
            ]
        }
        
        # If status is the same, it's valid (no change)
        if current_status == new_status:
            return True, None
        
        # Check if transition is valid
        allowed_transitions = valid_transitions.get(current_status, [])
        if new_status not in allowed_transitions:
            return False, f"Cannot change status from '{current_status}' to '{new_status}'. Valid transitions from '{current_status}' are: {', '.join(allowed_transitions) if allowed_transitions else 'none'}"
        
        return True, None

class RollbackHistoryService:
    """Service layer for rollback history-related operations"""
    
    @staticmethod
    def check_rollback_history_exists_by_scaling_action_id(scaling_action_id):
        """
        Check if rollback history exists by scaling action ID
        """
        try:
            rollback_history = RollbackHistory.objects.get(scaling_action_id=scaling_action_id)
            return rollback_history
        except RollbackHistory.DoesNotExist:
            return None


# ==================== BUDGET PACING FORECAST ====================

# Tuning knobs for the pacing model. Module-level so tests can monkeypatch them
# and so the thresholds are reviewable in one place.
UNDER_PACING_THRESHOLD = Decimal("0.90")
OVER_PACING_THRESHOLD = Decimal("1.10")

# Trailing window used for the "current pace" average daily spend.
TRAILING_AVERAGE_DAYS = 7

# Day-of-week seasonality: needs at least MIN_DAYS of completed history before
# factors are trusted, and is fitted over at most WINDOW_DAYS.
SEASONALITY_WINDOW_DAYS = 28
SEASONALITY_MIN_DAYS = 14

# Factors are clamped so a single freak day cannot dominate the projection.
DOW_FACTOR_MIN = Decimal("0.5")
DOW_FACTOR_MAX = Decimal("1.5")

_MONEY = Decimal("0.01")
_RATIO = Decimal("0.0001")
_NEUTRAL_FACTOR = Decimal("1")


def _money(value) -> Decimal:
    """Quantize to 2dp, the precision of every money column in this app."""
    return Decimal(value).quantize(_MONEY, rounding=ROUND_HALF_UP)


def _ratio(value) -> Decimal:
    return Decimal(value).quantize(_RATIO, rounding=ROUND_HALF_UP)


def _date_range(first: date, last: date):
    """Inclusive date iterator; yields nothing when last < first."""
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


@dataclass
class PacingForecast:
    """Result of the pacing model. Pure data — no DB access."""

    status: str
    reason: str = ""
    budget: Optional[Decimal] = None
    spend_to_date: Decimal = Decimal("0.00")
    expected_spend_to_date: Optional[Decimal] = None
    projected_total_spend: Optional[Decimal] = None
    suggested_daily_cap: Optional[Decimal] = None
    avg_daily_spend: Decimal = Decimal("0.00")
    pace_ratio: Optional[Decimal] = None
    total_days: int = 0
    days_elapsed: int = 0
    days_remaining: int = 0
    seasonality_applied: bool = False
    dow_factors: Dict[str, float] = field(default_factory=dict)
    computed_for_date: Optional[date] = None

    def as_model_fields(self) -> dict:
        """Field mapping for CampaignPacingForecast create/update."""
        return {
            "status": self.status,
            "reason": self.reason,
            "budget": self.budget,
            "spend_to_date": self.spend_to_date,
            "expected_spend_to_date": self.expected_spend_to_date,
            "projected_total_spend": self.projected_total_spend,
            "suggested_daily_cap": self.suggested_daily_cap,
            "avg_daily_spend": self.avg_daily_spend,
            "pace_ratio": self.pace_ratio,
            "total_days": self.total_days,
            "days_elapsed": self.days_elapsed,
            "days_remaining": self.days_remaining,
            "seasonality_applied": self.seasonality_applied,
            "dow_factors": self.dow_factors,
            "computed_for_date": self.computed_for_date,
        }


def compute_dow_factors(
    daily_spend: Mapping[date, Decimal],
    *,
    first_day: date,
    last_day: date,
) -> Dict[int, Decimal]:
    """Fit day-of-week spend factors over completed days in [first_day, last_day].

    A factor is that weekday's mean spend divided by the overall mean, clamped to
    [DOW_FACTOR_MIN, DOW_FACTOR_MAX]. Returns {} when there is not enough history
    or no spend at all, which callers treat as "fall back to pure linear".
    """
    if last_day < first_day:
        return {}

    window_start = max(first_day, last_day - timedelta(days=SEASONALITY_WINDOW_DAYS - 1))
    days = list(_date_range(window_start, last_day))
    if len(days) < SEASONALITY_MIN_DAYS:
        return {}

    # Missing rows are genuine zero-spend days, not gaps.
    spends = [Decimal(daily_spend.get(day, 0)) for day in days]
    overall_mean = sum(spends) / Decimal(len(days))
    if overall_mean <= 0:
        return {}

    by_weekday: Dict[int, list] = {}
    for day, spend in zip(days, spends):
        by_weekday.setdefault(day.weekday(), []).append(spend)

    factors: Dict[int, Decimal] = {}
    for weekday in range(7):
        samples = by_weekday.get(weekday)
        if not samples:
            factors[weekday] = _NEUTRAL_FACTOR
            continue
        weekday_mean = sum(samples) / Decimal(len(samples))
        factor = weekday_mean / overall_mean
        factors[weekday] = min(max(factor, DOW_FACTOR_MIN), DOW_FACTOR_MAX)

    return factors


def forecast_pacing(
    *,
    budget: Optional[Decimal],
    start_date: Optional[date],
    end_date: Optional[date],
    daily_spend: Mapping[date, Decimal],
    today: date,
) -> PacingForecast:
    """Project period-end spend and suggest a daily cap that lands on budget.

    Pure function: every input is passed in (including `today`), so tests are
    deterministic and the model can be exercised without touching the database.

    The projection is `spend_to_date + avg_daily_spend * seasonal_weight`, where
    `seasonal_weight` is the sum of day-of-week factors over the remaining days
    (and simply the day count when there is not enough history for seasonality).
    `suggested_daily_cap` inverts that: the baseline daily spend which, scaled by
    each remaining day's factor, exhausts exactly the remaining budget.
    """
    has_budget = budget is not None and Decimal(budget) > 0
    has_end_date = end_date is not None

    if not has_budget and not has_end_date:
        reason = PacingReason.MISSING_BUDGET_AND_END_DATE
    elif not has_budget:
        reason = PacingReason.MISSING_BUDGET
    elif not has_end_date:
        reason = PacingReason.MISSING_END_DATE
    else:
        reason = None

    # start_date is non-null on Campaign, so this only guards direct callers —
    # but reporting "missing end date" for it would be a lie.
    if start_date is None:
        reason = PacingReason.INVALID_PERIOD

    if reason is not None:
        return PacingForecast(
            status=PacingStatus.NOT_CONFIGURED,
            reason=reason,
            budget=_money(budget) if has_budget else None,
            computed_for_date=today,
        )

    if end_date < start_date:
        return PacingForecast(
            status=PacingStatus.NOT_CONFIGURED,
            reason=PacingReason.INVALID_PERIOD,
            budget=_money(budget),
            computed_for_date=today,
        )

    budget = _money(budget)
    total_days = (end_date - start_date).days + 1

    if today < start_date:
        return PacingForecast(
            status=PacingStatus.NOT_STARTED,
            budget=budget,
            total_days=total_days,
            days_remaining=total_days,
            computed_for_date=today,
        )

    # Today counts as elapsed (it is in progress), capped at the period length.
    days_elapsed = min((today - start_date).days + 1, total_days)
    days_remaining = total_days - days_elapsed

    # Today's row is partial, so it counts toward spend but not toward the pace.
    spend_through = min(today, end_date)
    last_complete_day = min(today - timedelta(days=1), end_date)

    if not daily_spend:
        return PacingForecast(
            status=PacingStatus.NO_DATA,
            reason=PacingReason.NO_LINKED_SPEND,
            budget=budget,
            total_days=total_days,
            days_elapsed=days_elapsed,
            days_remaining=days_remaining,
            computed_for_date=today,
        )

    spend_to_date = _money(
        sum(
            (Decimal(amount) for day, amount in daily_spend.items()
             if start_date <= day <= spend_through),
            Decimal("0"),
        )
    )

    # Trailing average over completed days only. On day one there is no completed
    # day yet, so today's partial spend is the only signal available.
    trailing_start = max(start_date, last_complete_day - timedelta(days=TRAILING_AVERAGE_DAYS - 1))
    trailing_days = list(_date_range(trailing_start, last_complete_day))
    if trailing_days:
        trailing_total = sum(
            (Decimal(daily_spend.get(day, 0)) for day in trailing_days), Decimal("0")
        )
        avg_daily_spend = _money(trailing_total / Decimal(len(trailing_days)))
    else:
        avg_daily_spend = spend_to_date

    factors = compute_dow_factors(
        daily_spend, first_day=start_date, last_day=last_complete_day
    )
    seasonality_applied = bool(factors)

    remaining_days = list(_date_range(today + timedelta(days=1), end_date))
    if seasonality_applied:
        seasonal_weight = sum(
            (factors[day.weekday()] for day in remaining_days), Decimal("0")
        )
    else:
        seasonal_weight = Decimal(len(remaining_days))

    projected_total_spend = _money(spend_to_date + avg_daily_spend * seasonal_weight)
    expected_spend_to_date = _money(budget * Decimal(days_elapsed) / Decimal(total_days))
    pace_ratio = _ratio(projected_total_spend / budget)

    if pace_ratio < UNDER_PACING_THRESHOLD:
        status = PacingStatus.UNDER_PACING
    elif pace_ratio > OVER_PACING_THRESHOLD:
        status = PacingStatus.OVER_PACING
    else:
        status = PacingStatus.ON_TRACK

    # No days left to cap, and nothing to re-pace with.
    if seasonal_weight > 0:
        remaining_budget = max(budget - spend_to_date, Decimal("0"))
        suggested_daily_cap = _money(remaining_budget / seasonal_weight)
    else:
        suggested_daily_cap = None

    return PacingForecast(
        status=status,
        budget=budget,
        spend_to_date=spend_to_date,
        expected_spend_to_date=expected_spend_to_date,
        projected_total_spend=projected_total_spend,
        suggested_daily_cap=suggested_daily_cap,
        avg_daily_spend=avg_daily_spend,
        pace_ratio=pace_ratio,
        total_days=total_days,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        seasonality_applied=seasonality_applied,
        dow_factors={str(k): float(v) for k, v in factors.items()},
        computed_for_date=today,
    )


class PacingService:
    """Database-facing wrapper around the pure pacing model.

    v1 sources spend from Meta only, via
    Campaign <- MetaCampaign.mediajira_campaign -> MetaAdSet -> MetaAd -> MetaInsightDaily.
    Campaigns with no linked MetaCampaign resolve to PacingStatus.NO_DATA.
    """

    @staticmethod
    def build_daily_spend(campaign, *, through: date) -> Dict[date, Decimal]:
        """Daily spend for a campaign from campaign start through `through`."""
        # Imported lazily so the optimization app does not hard-depend on
        # meta_ads at import time.
        from meta_ads.models import MetaInsightDaily

        if campaign.start_date is None:
            return {}

        rows = (
            MetaInsightDaily.objects.filter(
                ad__adset__campaign__mediajira_campaign=campaign,
                date__gte=campaign.start_date,
                date__lte=through,
            )
            .values("date")
            .annotate(total_spend=Sum("spend"))
        )
        return {row["date"]: Decimal(row["total_spend"] or 0) for row in rows}

    @staticmethod
    def forecast_for_campaign(campaign, *, today: Optional[date] = None) -> PacingForecast:
        """Compute (without saving) the forecast for one campaign."""
        if today is None:
            today = timezone.now().date()

        # Skip the insights query when the model would short-circuit on config
        # anyway — the nightly task walks every campaign, most of which are
        # unconfigured or not yet started.
        configured = (
            campaign.budget_estimate is not None
            and campaign.end_date is not None
            and campaign.start_date is not None
            and campaign.start_date <= today
        )
        daily_spend = (
            PacingService.build_daily_spend(campaign, through=today) if configured else {}
        )

        return forecast_pacing(
            budget=campaign.budget_estimate,
            start_date=campaign.start_date,
            end_date=campaign.end_date,
            daily_spend=daily_spend,
            today=today,
        )

    @staticmethod
    def recompute_for_campaign(campaign, *, today: Optional[date] = None):
        """Compute and persist the forecast for one campaign."""
        forecast = PacingService.forecast_for_campaign(campaign, today=today)
        obj, _ = CampaignPacingForecast.objects.update_or_create(
            campaign=campaign,
            defaults=forecast.as_model_fields(),
        )
        return obj