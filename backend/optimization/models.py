from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()

# --- Models for Optimization Experiment ---
class OptimizationExperiment(models.Model):
    #Defines the types of experiments (enum)
    class ExperimentType(models.TextChoices):
        AB_TEST = 'ab_test'
        CREATIVE_ROTATION = 'creative_rotation'
        BUDGET_SPLIT = 'budget_split'
    
    #Defines the status of experiments (enum)
    class ExperimentStatus(models.TextChoices):
        RUNNING = 'running'
        PAUSED = 'paused'
        COMPLETED = 'completed'
        ROLLED_BACK = 'rolled_back'
    
    # --- Fields ---
    name = models.CharField(
        max_length=255, 
        null=False, 
        blank=False, 
        help_text="The name of the experiment")

    experiment_type = models.CharField(
        max_length=255, 
        choices=ExperimentType.choices, 
        null=False, 
        blank=False, 
        help_text="The type of the experiment")
    
    #Linked campaigns' ids are stored as a JSON field, e.g., ["fb:123", "tt:456"]
    linked_campaign_ids = models.JSONField(
        null=False, 
        blank=False, 
        help_text="The ids of the linked campaigns")
    
    hypothesis = models.TextField(
        null=False, 
        blank=False, 
        help_text="The hypothesis of the experiment")
    
    start_date = models.DateField(
        null=False, 
        blank=False, 
        help_text="The start date of the experiment",
        )
    
    end_date = models.DateField(
        null=False, 
        blank=False, 
        help_text="The end date of the experiment")
    
    status = models.CharField(
        max_length=255, 
        choices=ExperimentStatus.choices, 
        null=False, 
        blank=False, 
        default=ExperimentStatus.RUNNING,
        help_text="The status of the experiment")
    
    description = models.TextField(
        null=False, 
        blank=False, 
        help_text="The description of the experiment")
    
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        db_column='created_by',
        related_name='owned_optimization_experiments', 
        help_text="The user who created the experiment")

    class Meta:
        db_table = 'optimization_experiment'
    
    def __str__(self):
        return f"{self.name} ({self.id})"


# --- Models for Scaling Action ---
class ScalingAction(models.Model):
    #Defines the types of scaling actions (enum)
    class ScalingActionType(models.TextChoices):
        BUDGET_INCREASE = 'budget_increase'
        BUDGET_DECREASE = 'budget_decrease'
        AUDIENCE_EXPAND = 'audience_expand'
        AUDIENCE_NARROW = 'audience_narrow'
        CREATIVE_REPLACE = 'creative_replace'
    
    # --- Fields ---
    experiment_id = models.ForeignKey(
        OptimizationExperiment,
        on_delete=models.CASCADE,
        related_name='owned_scaling_actions',
        db_column='experiment_id',
        null=True,
        help_text="The experiment that the scaling action belongs to")
    
    action_type = models.CharField(
        max_length=255, 
        choices=ScalingActionType.choices, 
        null=False, 
        blank=False, 
        help_text="The type of the scaling action")
    
    # Details of the scaling action are stored as a JSON field, e.g., {"increase_pct": 25}
    # TODO: validator needed if there is a required format for action_details
    action_details = models.JSONField(
        null=False, 
        blank=False, 
        help_text="The details of the scaling action")
    
    # Campaign id is stored as a string, e.g., "fb:123"
    campaign_id = models.CharField(
        max_length=255, 
        null=False, 
        blank=False,
        help_text="The id of the campaign that the scaling action belongs to")
    
    performed_at = models.DateTimeField(
        auto_now_add=True,
        help_text="The date and time the scaling action was performed")
    
    performed_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='performed_scaling_actions',
        db_column='performed_by',
        help_text="The user who performed the scaling action")
    
    class Meta:
        db_table = 'scaling_action'

# --- Models for Rollback History ---
class RollbackHistory(models.Model):
    # --- Fields ---
    scaling_action_id = models.ForeignKey(
        ScalingAction,
        on_delete=models.CASCADE,
        related_name='owned_rollback_histories',
        null=False,
        help_text="The id of the scaling action that was rolled back")

    reason = models.TextField(
        null=False, 
        blank=False,
        help_text="The reason for the rollback")
    
    performed_at = models.DateTimeField(
        auto_now_add=True, 
        help_text="The date and time the rollback action was performed")
    
    performed_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        db_column='performed_by',
        related_name='performed_rollback_actions', 
        help_text="The user who performed the rollback action")
    
    class Meta:
        db_table = 'rollback_history'

# --- Models for Experiment Metric ---
class ExperimentMetric(models.Model):
    # --- Fields ---
    experiment_id = models.ForeignKey(
        OptimizationExperiment,
        on_delete=models.CASCADE,
        related_name='owned_experiment_metrics',
        db_column='experiment_id',
        null=False,
        help_text="The id of the experiment that the metric belongs to")
    
    metric_name = models.CharField(
        max_length=255, 
        null=False, 
        blank=False, 
        help_text="The name of the metric")
    
    metric_value = models.FloatField(
        null=False, 
        blank=False, 
        help_text="The value of the metric")
    
    recorded_at = models.DateTimeField(
        auto_now_add=True,
        help_text="The date and time the metric was recorded")
    
    class Meta:
        indexes = [
            models.Index(fields=['experiment_id', 'recorded_at'], name='exp_metric_time_idx'),
        ]
        db_table = 'experiment_metric'


# --- Models for Scaling Plan & Steps ---
class ScalingPlan(models.Model):
    """
    High-level scaling plan linked to a Task(type='scaling').
    Captures strategy, targets, risks, limits, affected entities,
    expected outcomes and post-scaling review.
    """

    class Strategy(models.TextChoices):
        HORIZONTAL = "horizontal", "Horizontal"
        VERTICAL = "vertical", "Vertical"
        HYBRID = "hybrid", "Hybrid"

    class PlanStatus(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    task = models.OneToOneField(
        "task.Task",
        on_delete=models.CASCADE,
        related_name="scaling_plan",
        help_text="Parent task that owns this scaling plan",
    )
    strategy = models.CharField(
        max_length=20,
        choices=Strategy.choices,
        help_text="Scaling strategy (horizontal / vertical / hybrid)",
    )
    scaling_target = models.TextField(
        blank=True,
        help_text="Description of scaling targets (budget, KPIs, etc.)",
    )
    risk_considerations = models.TextField(
        blank=True,
        help_text="Key risks and mitigation considerations for this scaling plan",
    )
    max_scaling_limit = models.CharField(
        max_length=255,
        blank=True,
        help_text="Maximum scaling limits (e.g. max budget or % increase)",
    )
    stop_conditions = models.TextField(
        blank=True,
        help_text="Conditions under which scaling should be stopped or rolled back",
    )
    affected_entities = models.JSONField(
        null=True,
        blank=True,
        help_text="List of affected campaigns/ad sets and relevant identifiers",
    )
    expected_outcomes = models.TextField(
        blank=True,
        help_text="Expected outcomes / KPI ranges for the overall scaling plan",
    )

    status = models.CharField(
        max_length=20,
        choices=PlanStatus.choices,
        default=PlanStatus.PLANNED,
        help_text="Lifecycle status of the scaling plan",
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When execution of the scaling plan started",
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When execution of the scaling plan completed",
    )

    # Post-scaling review fields
    review_summary = models.TextField(
        blank=True,
        help_text="High-level summary of scaling impact after completion",
    )
    review_lessons_learned = models.TextField(
        blank=True,
        help_text="Key lessons learned from this scaling activity",
    )
    review_future_actions = models.TextField(
        blank=True,
        help_text="Recommended changes or improvements for future scaling tasks",
    )
    review_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the post-scaling review was completed",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scaling_plan"

    def __str__(self) -> str:
        return f"ScalingPlan(task={self.task_id}, strategy={self.strategy})"


class ScalingStep(models.Model):
    """
    Individual scaling execution step belonging to a ScalingPlan.
    Tracks planned vs actual changes and performance deviations.
    """

    class StepStatus(models.TextChoices):
        PLANNED = "planned", "Planned"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    plan = models.ForeignKey(
        ScalingPlan,
        on_delete=models.CASCADE,
        related_name="steps",
        help_text="Parent scaling plan",
    )
    step_order = models.PositiveIntegerField(
        default=1,
        help_text="Order of this step within the scaling plan",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional name/label for this scaling step",
    )
    planned_change = models.TextField(
        blank=True,
        help_text="Description of planned change (e.g. budget from 500→800)",
    )
    expected_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Expected metrics for this step (e.g. ROAS, CPA, CTR)",
    )
    actual_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Actual observed metrics after this scaling step",
    )
    status = models.CharField(
        max_length=20,
        choices=StepStatus.choices,
        default=StepStatus.PLANNED,
        help_text="Execution status for this step",
    )
    scheduled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this step is planned to be executed",
    )
    executed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this step was actually executed",
    )
    notes = models.TextField(
        blank=True,
        help_text="Additional notes, adjustments, or comments for this step",
    )
    stop_triggered = models.BooleanField(
        default=False,
        help_text="Whether this step triggered stop/rollback conditions",
    )

    # Optional link to low-level scaling action record if available
    related_scaling_action = models.ForeignKey(
        ScalingAction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="plan_steps",
        help_text="Optional link to underlying scaling action, if applicable",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "scaling_step"
        ordering = ["plan_id", "step_order", "id"]

    def __str__(self) -> str:
        return f"ScalingStep(plan={self.plan_id}, order={self.step_order})"


# --- Models for Optimization Task ---
class Optimization(models.Model):
    """
    Optimization task model linked to a Task(type='optimization').
    Represents optimization tasks that can be created and managed through the task system.
    """

    class ActionType(models.TextChoices):
        PAUSE = "pause", "Pause"
        SCALE = "scale", "Scale"
        DUPLICATE = "duplicate", "Duplicate"
        EDIT = "edit", "Edit"

    class ExecutionStatus(models.TextChoices):
        DETECTED = "detected", "Detected"
        PLANNED = "planned", "Planned"
        EXECUTED = "executed", "Executed"
        MONITORING = "monitoring", "Monitoring"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
    
    task = models.OneToOneField(
        "task.Task",
        on_delete=models.CASCADE,
        related_name="optimization",
        null=True,
        blank=True,
        help_text="The task that owns this optimization (1:1 relationship)"
    )

    # IDs only (aligned with Experiment JSON patterns)
    affected_entity_ids = models.JSONField(
        null=True,
        blank=True,
        help_text="Affected campaign/ad set ids, e.g. {'campaign_ids': ['fb:123'], 'ad_set_ids': ['fb:456']}",
    )

    # Trigger/baseline/observed metrics
    triggered_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Metrics that triggered optimization, e.g. {'CPA': {'delta_pct': 35, 'window': '24h'}}",
    )
    baseline_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Baseline metrics before adjustments, e.g. {'CPA': 12.3, 'CTR': 0.9}",
    )
    observed_metrics = models.JSONField(
        null=True,
        blank=True,
        help_text="Observed metrics after action, for monitoring/outcome tracking",
    )

    action_type = models.CharField(
        max_length=20,
        choices=ActionType.choices,
        default=ActionType.PAUSE,
        help_text="Planned action type (pause/scale/duplicate/edit)",
    )
    planned_action = models.TextField(
        blank=True,
        help_text="Planned action details (what will be changed and how)",
    )
    execution_status = models.CharField(
        max_length=20,
        choices=ExecutionStatus.choices,
        default=ExecutionStatus.DETECTED,
        help_text="Execution status from detection to monitoring/outcome",
    )

    executed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the optimization action was executed",
    )
    monitored_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When post-adjustment monitoring/outcome was last updated",
    )
    outcome_notes = models.TextField(
        blank=True,
        help_text="Notes about outcome and performance changes after optimization",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "optimization"

    def __str__(self) -> str:
        return f"Optimization(task={self.task_id}, status={self.execution_status})"


# --- Models for Budget Pacing Forecast ---
class PacingStatus(models.TextChoices):
    """Pacing verdict for a campaign's budget burn.

    The three "actionable" states (under/on_track/over) are only reachable once
    the campaign has a budget, an end date and a linked daily spend series.
    The rest tell the UI why there is no forecast to show.
    """

    NOT_CONFIGURED = "not_configured", "Not Configured"
    NOT_STARTED = "not_started", "Not Started"
    NO_DATA = "no_data", "No Data"
    UNDER_PACING = "under_pacing", "Under Pacing"
    ON_TRACK = "on_track", "On Track"
    OVER_PACING = "over_pacing", "Over Pacing"


class PacingReason(models.TextChoices):
    """Machine-readable explanation for a non-actionable pacing status."""

    MISSING_BUDGET = "missing_budget", "Missing Budget"
    MISSING_END_DATE = "missing_end_date", "Missing End Date"
    MISSING_BUDGET_AND_END_DATE = "missing_budget_and_end_date", "Missing Budget And End Date"
    INVALID_PERIOD = "invalid_period", "Invalid Period"
    NO_LINKED_SPEND = "no_linked_spend", "No Linked Spend"


class CampaignPacingForecast(models.Model):
    """Latest budget pacing forecast for a campaign.

    Recomputed nightly by `optimization.tasks.recompute_all_pacing_forecasts`
    and on demand via the pacing recompute endpoint. Kept in its own table
    rather than as columns on `campaigns` so the forecast can be recomputed
    and reasoned about independently of the campaign record itself.
    """

    campaign = models.OneToOneField(
        "campaign.Campaign",
        on_delete=models.CASCADE,
        related_name="pacing_forecast",
        help_text="The campaign this forecast describes",
    )

    status = models.CharField(
        max_length=20,
        choices=PacingStatus.choices,
        default=PacingStatus.NO_DATA,
        help_text="Pacing verdict for the campaign",
    )
    reason = models.CharField(
        max_length=32,
        choices=PacingReason.choices,
        blank=True,
        default="",
        help_text="Why no actionable forecast is available (blank when one is)",
    )

    # --- Money (campaign currency; all nullable when not computable) ---
    budget = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Budget the forecast was computed against",
    )
    spend_to_date = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Actual spend from campaign start through the computed date",
    )
    expected_spend_to_date = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Spend a perfectly linear burn would have reached by now",
    )
    projected_total_spend = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Forecast spend at period end if current pace continues",
    )
    suggested_daily_cap = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Baseline daily cap that would land exactly on budget",
    )
    avg_daily_spend = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=0,
        help_text="Trailing average daily spend used to project forward",
    )

    pace_ratio = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="projected_total_spend / budget (1.0 lands exactly on budget)",
    )

    # --- Period ---
    total_days = models.IntegerField(
        default=0, help_text="Length of the campaign period in days (inclusive)"
    )
    days_elapsed = models.IntegerField(
        default=0, help_text="Days of the period elapsed through the computed date"
    )
    days_remaining = models.IntegerField(
        default=0, help_text="Days of the period left after the computed date"
    )

    # --- Seasonality ---
    seasonality_applied = models.BooleanField(
        default=False,
        help_text="Whether day-of-week factors were applied (false = pure linear)",
    )
    dow_factors = models.JSONField(
        default=dict,
        blank=True,
        help_text="Day-of-week spend factors keyed by weekday '0'-'6' (Monday=0)",
    )

    computed_for_date = models.DateField(
        help_text="The 'today' the forecast was computed for"
    )
    computed_at = models.DateTimeField(
        auto_now=True, help_text="When this forecast was last recomputed"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "campaign_pacing_forecast"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["computed_for_date"]),
        ]

    def __str__(self) -> str:
        return f"CampaignPacingForecast(campaign={self.campaign_id}, status={self.status})"
