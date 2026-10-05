import logging
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)


class Plan(models.Model):
    name = models.CharField(max_length=255, null=False, blank=False)
    desc = models.TextField(null=True, blank=True)
    # Legacy caps — kept nullable for backward compatibility (AlterField in 0003)
    max_team_members = models.IntegerField(null=True, blank=True)
    max_previews_per_day = models.IntegerField(null=True, blank=True)
    max_tasks_per_day = models.IntegerField(null=True, blank=True)
    # Stripe price for the base plan (legacy + new)
    stripe_price_id = models.CharField(max_length=255, null=True, blank=True)
    # Token billing fields
    base_price_cents = models.IntegerField(default=0)
    included_seats = models.IntegerField(default=1)
    extra_seat_price_cents = models.IntegerField(null=True, blank=True)
    monthly_token_quota = models.BigIntegerField(null=True, blank=True)   # null = unlimited
    overage_price_cents_per_1m = models.IntegerField(null=True, blank=True)
    max_tokens_per_call = models.BigIntegerField(null=True, blank=True)
    stripe_extra_seat_price_id = models.CharField(max_length=255, null=True, blank=True)
    stripe_overage_price_id = models.CharField(max_length=255, null=True, blank=True)
    currency = models.CharField(max_length=3, default='AUD')
    is_archived = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Subscription(models.Model):
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE, null=False, blank=False)
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, null=False, blank=False)
    stripe_subscription_id = models.CharField(max_length=255, null=False, blank=False)
    start_date = models.DateTimeField(null=False, blank=False)
    end_date = models.DateTimeField(null=False, blank=False)
    is_active = models.BooleanField(default=True)
    seat_count = models.IntegerField(default=1)
    is_internal = models.BooleanField(default=False)   # True for Free sentinel subscriptions
    monthly_revenue_cents = models.IntegerField(default=0)
    stripe_overage_item_id = models.CharField(max_length=255, null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['organization'],
                condition=models.Q(is_active=True, is_internal=False),
                name='unique_active_real_subscription_per_org',
            )
        ]


class UsageDaily(models.Model):
    user = models.ForeignKey('core.CustomUser', on_delete=models.CASCADE, null=False, blank=False)
    date = models.DateField(null=False, blank=False)
    previews_used = models.IntegerField(default=0, null=False, blank=False)
    tasks_used = models.IntegerField(default=0, null=False, blank=False)


class Payment(models.Model):
    user = models.ForeignKey('core.CustomUser', on_delete=models.CASCADE, null=False, blank=False)
    stripe_invoice_id = models.CharField(max_length=255, null=False, blank=False)
    stripe_subscription_id = models.CharField(max_length=255, null=False, blank=False)
    stripe_product_id = models.CharField(max_length=255, null=False, blank=False)
    stripe_price_id = models.CharField(max_length=255, null=False, blank=False)
    stripe_customer_id = models.CharField(max_length=255, null=False, blank=False)
    is_active = models.BooleanField(default=True)


class StripeWebhookEvent(models.Model):
    stripe_event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)
    # Atomic processing claim — set by the delivery that wins the conditional
    # UPDATE; cleared on handler failure so Stripe's retry can claim again.
    claimed_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)


class UsageMonthly(models.Model):
    """Monthly token usage per Org — reserve/commit/release tracked here."""
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE)
    year_month = models.CharField(max_length=7)          # e.g. '2026-06'
    tokens_used = models.BigIntegerField(default=0)      # committed actual usage
    tokens_reserved = models.BigIntegerField(default=0)  # in-flight pre-reservations
    overage_tokens = models.BigIntegerField(default=0)   # tokens beyond quota (reported to Stripe after month close)
    overage_reported_tokens = models.BigIntegerField(default=0)  # tokens already credited to Stripe (units x 1M)
    overage_reported_at = models.DateTimeField(null=True, blank=True)  # last successful report (informational, not a guard)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('organization', 'year_month')


class LLMCallLog(models.Model):
    """One row per LLM call — used for cost accounting and fair-use alerting."""

    class CallPurpose(models.TextChoices):
        DATA_ANALYSIS       = 'data_analysis',       'Data Analysis'
        COLUMN_DETECTION    = 'column_detection',    'Column Detection'
        CRITERIA_GENERATION = 'criteria_generation', 'Criteria Generation'
        MIRO_GENERATION     = 'miro_generation',     'Miro Generation'
        FOLLOW_UP_CHAT      = 'follow_up_chat',      'Follow-up Chat'
        CALENDAR_SUGGESTION = 'calendar_suggestion', 'Calendar Suggestion'
        OTHER               = 'other',               'Other'

    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE)
    agent_session = models.ForeignKey('agent.AgentSession', null=True, on_delete=models.SET_NULL)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    provider = models.CharField(max_length=20)    # 'ollama'; older rows: 'anthropic' or 'gemini'
    model_name = models.CharField(max_length=80)
    call_purpose = models.CharField(
        max_length=30,
        choices=CallPurpose.choices,
        default=CallPurpose.OTHER,
        db_index=True,
    )
    input_tokens = models.IntegerField()
    output_tokens = models.IntegerField()
    normalized_tokens = models.BigIntegerField()  # after per-model multiplier, used for quota
    input_cost_cents = models.IntegerField()
    output_cost_cents = models.IntegerField()
    total_cost_cents = models.IntegerField()
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['organization', 'created_at'])]


class OrgMonthlyCost(models.Model):
    """Monthly cost rollup per Org — used for fair-use alerting."""
    organization = models.ForeignKey('core.Organization', on_delete=models.CASCADE)
    year_month = models.CharField(max_length=7)
    llm_cost_cents = models.IntegerField(default=0)
    total_tokens = models.BigIntegerField(default=0)

    class Meta:
        unique_together = ('organization', 'year_month')
