"""
Campaign Management Module - Models
============================================================================
"""

from django.db import models
from core.slug_mixins import SluggedResourceModelMixin
from django.contrib.auth import get_user_model
from django_fsm import FSMField, transition
from django.core.exceptions import ValidationError
from django.utils import timezone
from core.models import TimeStampedModel, Project
import uuid

User = get_user_model()


# ============================================================================
# Campaign Models
# ============================================================================

class Campaign(SluggedResourceModelMixin, TimeStampedModel):
    """
    Core Campaign Model - Represents advertising campaigns for strategic planning.

    Lifecycle: Planning → Testing → Scaling → Optimizing → Paused → Completed → Archived

    Section 6.1: Campaign Information
    - Identification (unique ID, name, creation timestamp)
    - Strategic details (objective, platforms, hypothesis, tags)
    - Timeline (start date, optional end date, actual completion)
    - Ownership (creator, current assignee, team visibility)
    - Status tracking (lifecycle stage, status history)
    - Relationships (tasks, decisions, calendar events, previews)
    """

    class Status(models.TextChoices):
        """Campaign lifecycle stages (Section 3.1: Campaign Status Tracking)"""
        PLANNING = 'PLANNING', 'Planning'
        TESTING = 'TESTING', 'Testing'
        SCALING = 'SCALING', 'Scaling'
        OPTIMIZING = 'OPTIMIZING', 'Optimizing'
        PAUSED = 'PAUSED', 'Paused'
        COMPLETED = 'COMPLETED', 'Completed'
        ARCHIVED = 'ARCHIVED', 'Archived'

    class Objective(models.TextChoices):
        """Campaign objectives"""
        AWARENESS = 'AWARENESS', 'Awareness'
        CONSIDERATION = 'CONSIDERATION', 'Consideration'
        CONVERSION = 'CONVERSION', 'Conversion'
        RETENTION = 'RETENTION', 'Retention'
        ENGAGEMENT = 'ENGAGEMENT', 'Engagement'
        TRAFFIC = 'TRAFFIC', 'Traffic'
        LEAD_GENERATION = 'LEAD_GENERATION', 'Lead Generation'
        APP_PROMOTION = 'APP_PROMOTION', 'App Promotion'

    class Platform(models.TextChoices):
        """Advertising platforms"""
        META = 'META', 'Meta (Facebook/Instagram)'
        GOOGLE_ADS = 'GOOGLE_ADS', 'Google Ads'
        TIKTOK = 'TIKTOK', 'TikTok'
        LINKEDIN = 'LINKEDIN', 'LinkedIn'
        SNAPCHAT = 'SNAPCHAT', 'Snapchat'
        TWITTER = 'TWITTER', 'Twitter/X'
        PINTEREST = 'PINTEREST', 'Pinterest'
        REDDIT = 'REDDIT', 'Reddit'
        PROGRAMMATIC = 'PROGRAMMATIC', 'Programmatic DSP'
        EMAIL = 'EMAIL', 'Email Marketing'

    # === Identification ===
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=255,
        help_text="Campaign name (mandatory)"
    )

    # === Strategic Details ===
    objective = models.CharField(
        max_length=50,
        choices=Objective.choices,
        help_text="Campaign objective (mandatory)"
    )
    platforms = models.JSONField(
        default=list,
        help_text="Target platforms as list of Platform choices (mandatory)"
    )
    hypothesis = models.TextField(
        blank=True,
        null=True,
        help_text="Strategic hypothesis (optional)"
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Organizational tags (optional)"
    )

    # === Timeline ===
    start_date = models.DateField(
        help_text="Campaign start date (mandatory)"
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Campaign end date (optional)"
    )
    actual_completion_date = models.DateField(
        null=True,
        blank=True,
        help_text="Actual completion date (set when completed)"
    )

    # === Ownership ===
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='campaigns',
        help_text="Project this campaign belongs to"
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,  # Prevent deletion of user who owns campaigns
        related_name='owned_campaigns',
        help_text="Campaign owner (mandatory - Section 3.1)"
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_campaigns',
        help_text="User who created the campaign"
    )
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_campaigns',
        help_text="当前执行负责人（可变更）"
    )

    # === Budget (Optional) ===
    budget_estimate = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Budget estimate (optional)"
    )

    # === Status Management ===
    status = FSMField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNING,
        protected=True,
        help_text="Current lifecycle stage"
    )
    status_note = models.TextField(
        blank=True,
        null=True,
        help_text="Note explaining current status or last status change"
    )

    # === Performance Summary (Latest) ===
    latest_performance_summary = models.JSONField(
        default=dict,
        blank=True,
        help_text="Latest performance snapshot data for quick dashboard display"
    )

    class Meta:
        db_table = 'campaigns'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', 'status', '-created_at']),
            models.Index(fields=['owner', 'status']),
            models.Index(fields=['assignee', 'status']),
            models.Index(fields=['start_date', 'end_date']),
            models.Index(fields=['project', 'start_date']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def clean(self):
        """Validation (Section 5.1: Campaign Lifecycle Rules)"""
        super().clean()

        # Section 5.1: Archived campaigns cannot be edited (except for restore transition)
        if self.pk:
            try:
                old_instance = Campaign.objects.get(pk=self.pk)
                if old_instance.status == self.Status.ARCHIVED:
                    # Only allow ARCHIVED -> COMPLETED transition (restore)
                    # All other changes are blocked
                    if self.status != self.Status.COMPLETED:
                        raise ValidationError({
                            'status': 'Archived campaigns cannot be edited. Use restore() to move back to Completed status.'
                        })
            except Campaign.DoesNotExist:
                pass

        # Validate platforms - must contain valid Platform choices
        if self.platforms:
            valid_platforms = [choice[0] for choice in self.Platform.choices]
            invalid_platforms = [p for p in self.platforms if p not in valid_platforms]
            if invalid_platforms:
                raise ValidationError({
                    'platforms': f'Invalid platforms: {invalid_platforms}. Must be one of: {valid_platforms}'
                })

        # Planning status must have future or today start dates
        if self.status == self.Status.PLANNING and self.start_date:
            if self.start_date < timezone.now().date():
                raise ValidationError({
                    'start_date': 'Planning campaigns must have start dates in the future or today'
                })

        # Completed campaigns must have end dates
        if self.status == self.Status.COMPLETED and not self.end_date:
            raise ValidationError({
                'end_date': 'Completed campaigns must have end dates'
            })

    def save(self, *args, **kwargs):
        # Only validate on update, not initial creation
        # This allows FSM default value to be set without triggering protection
        # _state.adding is True for new objects, False for existing ones
        if not self._state.adding:
            # Exclude 'status' field from field-level validation to avoid FSM protection errors.
            # Note: clean() method is still called and can access self.status for business logic
            # validation (e.g., PLANNING status date checks, COMPLETED status end_date requirement).
            # FSM transitions remain protected - status can only be changed via @transition methods.
            self.full_clean(exclude=['status'])
        super().save(*args, **kwargs)

    # === FSM Transitions ===

    @transition(field=status, source=Status.PLANNING, target=Status.TESTING)
    def start_testing(self, user=None):
        """Move to Testing phase"""
        # NOTE: AdVariation models were removed from the trimmed codebase.
        # No variation-level dependency enforced here; allow transition.
        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.TESTING,
            changed_by=user,
            note=self.status_note
        )

    @transition(field=status, source=Status.TESTING, target=Status.SCALING)
    def start_scaling(self, user=None):
        """Move to Scaling phase - requires documented performance data (Section 5.1)"""
        # Validation: Scaling requires performance data
        if not self.performance_snapshots.exists():
            raise ValidationError("Cannot start scaling: campaign must have documented performance data")

        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.SCALING,
            changed_by=user,
            note=self.status_note
        )

    @transition(field=status, source=[Status.TESTING, Status.SCALING], target=Status.OPTIMIZING)
    def start_optimizing(self, user=None):
        """Move to Optimizing phase"""
        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.OPTIMIZING,
            changed_by=user,
            note=self.status_note
        )

    @transition(
        field=status,
        source=[Status.TESTING, Status.SCALING, Status.OPTIMIZING],
        target=Status.PAUSED
    )
    def pause(self, user=None):
        """Pause the campaign"""
        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.PAUSED,
            changed_by=user,
            note=self.status_note
        )

    @transition(
        field=status,
        source=Status.PAUSED,
        target=Status.TESTING
    )
    def resume(self, user=None):
        """Resume paused campaign"""
        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.TESTING,
            changed_by=user,
            note=self.status_note
        )

    @transition(
        field=status,
        source=[Status.TESTING, Status.SCALING, Status.OPTIMIZING, Status.PAUSED],
        target=Status.COMPLETED
    )
    def complete(self, user=None):
        """Mark campaign as completed"""
        if not self.actual_completion_date:
            self.actual_completion_date = timezone.now().date()

        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.COMPLETED,
            changed_by=user,
            note=self.status_note
        )

    @transition(field=status, source=Status.COMPLETED, target=Status.ARCHIVED)
    def archive(self, user=None):
        """Archive the campaign (Section 5.1: Archived campaigns cannot be edited)"""
        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.ARCHIVED,
            changed_by=user,
            note=self.status_note
        )

    @transition(field=status, source=Status.ARCHIVED, target=Status.COMPLETED)
    def restore(self, user=None):
        """Restore archived campaign back to Completed status"""
        CampaignStatusHistory.objects.create(
            campaign=self,
            from_status=self.status,
            to_status=self.Status.COMPLETED,
            changed_by=user,
            note=self.status_note
        )

    # === Helper Properties ===

    @property
    def days_running(self):
        """Calculate days running or until launch"""
        today = timezone.now().date()
        if self.start_date > today:
            return None  # Not started yet
        return (today - self.start_date).days

    @property
    def is_editable(self):
        """Check if campaign can be edited (Section 5.1: Archived campaigns cannot be edited)"""
        return self.status != self.Status.ARCHIVED


class CampaignStatusHistory(TimeStampedModel):
    """
    Campaign Status History - Tracks all status changes for accountability.

    Section 3.1: Status tracking with timestamps and reasons
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='status_history',
        help_text="Associated campaign"
    )
    from_status = models.CharField(
        max_length=20,
        choices=Campaign.Status.choices,
        help_text="Previous status"
    )
    to_status = models.CharField(
        max_length=20,
        choices=Campaign.Status.choices,
        help_text="New status"
    )
    changed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='campaign_status_changes',
        help_text="User who made the change"
    )
    note = models.TextField(
        blank=True,
        null=True,
        help_text="Rationale for status change"
    )

    class Meta:
        db_table = 'campaign_status_history'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
        ]

    def __str__(self):
        return f"{self.campaign.name}: {self.get_from_status_display()} → {self.get_to_status_display()}"


# ============================================================================
# Performance Tracking Models
# ============================================================================

class PerformanceCheckIn(TimeStampedModel):
    """
    Quick Check-In Model - One-click sentiment indicator for campaign health.

    Section 3.1: Performance Monitoring (Quick check-ins)
    Section 6.2: Performance Tracking Information
    """

    class Sentiment(models.TextChoices):
        """Three-level sentiment indicator"""
        POSITIVE = 'POSITIVE', 'Positive'
        NEUTRAL = 'NEUTRAL', 'Neutral'
        NEGATIVE = 'NEGATIVE', 'Negative'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='check_ins',
        help_text="Associated campaign"
    )
    sentiment = models.CharField(
        max_length=20,
        choices=Sentiment.choices,
        help_text="Campaign health indicator"
    )
    note = models.TextField(
        blank=True,
        null=True,
        help_text="Brief optional note"
    )
    checked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='campaign_check_ins',
        help_text="User who made the check-in"
    )

    class Meta:
        db_table = 'performance_check_ins'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
            models.Index(fields=['sentiment', '-created_at']),
        ]

    def __str__(self):
        return f"{self.campaign.name} - {self.get_sentiment_display()} ({self.created_at.date()})"


class PerformanceSnapshot(TimeStampedModel):
    """
    Performance Snapshot Model - Structured data capture at key milestones.

    Section 3.1: Performance Monitoring (Performance snapshots)
    Section 6.2: Performance Tracking Information
    Section 5.2: Snapshot data optional except for spend and primary metric
    """

    class MetricType(models.TextChoices):
        """Primary metric types"""
        CPA = 'CPA', 'Cost Per Acquisition'
        ROAS = 'ROAS', 'Return on Ad Spend'
        CTR = 'CTR', 'Click-Through Rate'
        CPM = 'CPM', 'Cost Per Mille'
        CPC = 'CPC', 'Cost Per Click'
        CONVERSIONS = 'CONVERSIONS', 'Conversions'
        REVENUE = 'REVENUE', 'Revenue'
        IMPRESSIONS = 'IMPRESSIONS', 'Impressions'
        CLICKS = 'CLICKS', 'Clicks'
        ENGAGEMENT_RATE = 'ENGAGEMENT_RATE', 'Engagement Rate'

    class MilestoneType(models.TextChoices):
        """Milestone types for snapshots (Section 5.2)"""
        LAUNCH = 'LAUNCH', 'Campaign Launch'
        MID_TEST = 'MID_TEST', 'Mid-Test Review'
        TEST_COMPLETE = 'TEST_COMPLETE', 'Test Completion'
        OPTIMIZATION = 'OPTIMIZATION', 'Major Optimization'
        WEEKLY_REVIEW = 'WEEKLY_REVIEW', 'Weekly Review'
        MONTHLY_REVIEW = 'MONTHLY_REVIEW', 'Monthly Review'
        CUSTOM = 'CUSTOM', 'Custom Milestone'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='performance_snapshots',
        help_text="Associated campaign"
    )

    # === Milestone Classification ===
    milestone_type = models.CharField(
        max_length=30,
        choices=MilestoneType.choices,
        default=MilestoneType.CUSTOM,
        help_text="Type of milestone this snapshot represents"
    )

    # === Mandatory Fields (Section 5.2) ===
    spend = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text="Total spend at this milestone (mandatory)"
    )
    metric_type = models.CharField(
        max_length=30,
        choices=MetricType.choices,
        help_text="Type of primary metric (mandatory)"
    )
    metric_value = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        help_text="Value of primary metric (mandatory)"
    )

    # === Optional Fields ===
    percentage_change = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Percentage change vs previous period (optional)"
    )
    notes = models.TextField(
        blank=True,
        null=True,
        help_text="Free-form observations (optional)"
    )
    screenshot = models.FileField(
        upload_to='campaign/snapshots/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text="Optional screenshot attachment"
    )

    # === Additional Metrics (Optional) ===
    additional_metrics = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metrics as key-value pairs"
    )

    # === Metadata ===
    snapshot_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='campaign_snapshots',
        help_text="User who created the snapshot"
    )

    class Meta:
        db_table = 'performance_snapshots'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
            models.Index(fields=['metric_type', '-created_at']),
        ]

    def __str__(self):
        return f"{self.campaign.name} - {self.get_metric_type_display()}: {self.metric_value} ({self.created_at.date()})"


# ============================================================================
# Ad Variation Models
# ============================================================================
# Ad Variation models removed - system trimmed to Campaign-only models.
# If variations are reintroduced later, restore classes and related relationships.


# ============================================================================
# Integration & Relationship Models (Section 3.4)
# ============================================================================

class CampaignPlatformIntegration(models.Model):
    """Sync health for a campaign's project-linked Meta ad account.

    Meta is currently the only metrics provider with a sync worker. Keep state
    per account so a successful account cannot hide another account's failure.
    """

    class SyncError(models.TextChoices):
        NONE = '', 'No error'
        AUTH = 'auth', 'Reconnect required'
        TRANSIENT = 'transient', 'Temporary failure'
        UNKNOWN = 'unknown', 'Sync failed'

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name='platform_integrations')
    ad_account = models.ForeignKey('facebook_integration.MetaAdAccount', on_delete=models.CASCADE)
    last_sync_error = models.CharField(max_length=16, choices=SyncError.choices, blank=True, default='')
    # Start of the failed attempt, used to order overlapping sync results.
    last_sync_error_at = models.DateTimeField(null=True, blank=True)
    last_sync_attempted_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'ad_account'], name='campaign_platform_account_unique'),
        ]


class CampaignTaskLink(TimeStampedModel):
    """
    Campaign-Task relationship tracking.

    Section 3.4: Task System Integration
    Links campaigns to related tasks (budget, asset, retrospective, general, etc.)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='task_links',
        help_text="Associated campaign"
    )
    task = models.ForeignKey(
        'task.Task',
        on_delete=models.CASCADE,
        related_name='campaign_links',
        help_text="Linked task"
    )
    link_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Type of relationship (e.g., 'auto_generated', 'manual', 'milestone')"
    )

    class Meta:
        db_table = 'campaign_task_links'
        unique_together = ['campaign', 'task']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
            models.Index(fields=['task']),
        ]

    def __str__(self):
        return f"{self.campaign.name} -> Task #{self.task.id}"


class CampaignDecisionLink(TimeStampedModel):
    """
    Campaign-Decision relationship tracking.

    Section 3.4: Decision System Integration
    Links campaigns to decision cards for strategic choices
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='decision_links',
        help_text="Associated campaign"
    )
    decision = models.ForeignKey(
        'decision.Decision',
        on_delete=models.CASCADE,
        related_name='campaign_links',
        help_text="Linked decision"
    )
    trigger_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="What triggered this decision (e.g., 'test_complete', 'threshold_breach', 'manual')"
    )

    class Meta:
        db_table = 'campaign_decision_links'
        unique_together = ['campaign', 'decision']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
            models.Index(fields=['decision']),
        ]

    def __str__(self):
        return f"{self.campaign.name} -> Decision #{self.decision.id}"


class CampaignCalendarLink(TimeStampedModel):
    """
    Campaign-Calendar Event relationship tracking.

    Section 3.4: Calendar System Integration
    Links campaigns to calendar events (milestones, reviews, deadlines)
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='calendar_links',
        help_text="Associated campaign"
    )
    event = models.ForeignKey(
        'calendars.Event',
        on_delete=models.CASCADE,
        related_name='campaign_links',
        help_text="Linked calendar event"
    )
    event_type = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Type of event (e.g., 'milestone', 'review', 'budget_check', 'test_deadline')"
    )

    class Meta:
        db_table = 'campaign_calendar_links'
        unique_together = ['campaign', 'event']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
            models.Index(fields=['event']),
        ]

    def __str__(self):
        return f"{self.campaign.name} -> Event: {self.event.title}"



# ============================================================================
# Workflow Automation Models (Section 3.3)
# ============================================================================

class AutomationTrigger(TimeStampedModel):
    """
    Automation Trigger Configuration - Defines when to auto-generate tasks/decisions.

    Section 3.3: Workflow Automation
    Section 5.4: Automation Trigger Rules
    """

    class TriggerType(models.TextChoices):
        """Types of automation triggers"""
        STATUS_CHANGE = 'STATUS_CHANGE', 'Campaign Status Change'
        MILESTONE_DATE = 'MILESTONE_DATE', 'Milestone Date Reached'
        PERFORMANCE_THRESHOLD = 'PERFORMANCE_THRESHOLD', 'Performance Threshold'
        TIME_BASED = 'TIME_BASED', 'Time-Based (e.g., X days since)'
        CONSECUTIVE_CHECKINS = 'CONSECUTIVE_CHECKINS', 'Consecutive Check-ins Pattern'

    class ActionType(models.TextChoices):
        """Actions to take when triggered"""
        CREATE_TASK = 'CREATE_TASK', 'Create Task'
        CREATE_DECISION = 'CREATE_DECISION', 'Create Decision Card'
        CREATE_CALENDAR_EVENT = 'CREATE_CALENDAR_EVENT', 'Create Calendar Event'
        SEND_NOTIFICATION = 'SEND_NOTIFICATION', 'Send Notification'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Scope
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='automation_triggers',
        help_text="Project this trigger applies to"
    )

    # Trigger Configuration
    trigger_type = models.CharField(
        max_length=50,
        choices=TriggerType.choices,
        help_text="What triggers this automation"
    )
    trigger_config = models.JSONField(
        default=dict,
        help_text="Trigger-specific configuration (e.g., {'from_status': 'TESTING', 'to_status': 'SCALING'})"
    )

    # Action Configuration
    action_type = models.CharField(
        max_length=50,
        choices=ActionType.choices,
        help_text="What action to take"
    )
    action_config = models.JSONField(
        default=dict,
        help_text="Action-specific configuration (e.g., task template, decision template)"
    )

    # Control
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this trigger is active (Section 5.4: can be toggled off)"
    )

    # De-duplication (Section 5.4)
    prevent_duplicates = models.BooleanField(
        default=True,
        help_text="Prevent creating duplicate tasks/decisions for same issue"
    )
    duplicate_check_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="How to check for duplicates (e.g., time window, key fields)"
    )

    class Meta:
        db_table = 'automation_triggers'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', 'is_active']),
            models.Index(fields=['trigger_type', 'is_active']),
        ]

    def __str__(self):
        return f"{self.get_trigger_type_display()} -> {self.get_action_type_display()}"


class AutomationExecution(TimeStampedModel):
    """
    Automation Execution Log - Tracks when automations were executed.

    Used for preventing duplicates and auditing automation behavior.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trigger = models.ForeignKey(
        AutomationTrigger,
        on_delete=models.CASCADE,
        related_name='executions',
        help_text="Which trigger was executed"
    )
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='automation_executions',
        help_text="Campaign this execution was for"
    )

    # Execution details
    triggered_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the trigger was fired"
    )
    executed_successfully = models.BooleanField(
        default=True,
        help_text="Whether the action completed successfully"
    )
    error_message = models.TextField(
        blank=True,
        null=True,
        help_text="Error message if execution failed"
    )

    # What was created
    created_task = models.ForeignKey(
        'task.Task',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='automation_source',
        help_text="Task created by this automation"
    )
    created_decision = models.ForeignKey(
        'decision.Decision',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='automation_source',
        help_text="Decision created by this automation"
    )
    created_event = models.ForeignKey(
        'calendars.Event',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='automation_source',
        help_text="Calendar event created by this automation"
    )

    # Context
    trigger_context = models.JSONField(
        default=dict,
        blank=True,
        help_text="Context data at trigger time (for debugging/auditing)"
    )

    class Meta:
        db_table = 'automation_executions'
        ordering = ['-triggered_at']
        indexes = [
            models.Index(fields=['campaign', '-triggered_at']),
            models.Index(fields=['trigger', '-triggered_at']),
            models.Index(fields=['executed_successfully']),
        ]

    def __str__(self):
        status = "✓" if self.executed_successfully else "✗"
        return f"{status} {self.trigger} for {self.campaign.name} ({self.triggered_at.date()})"


# ============================================================================
# Notification Preferences (Section 5.4 / 7.4)
# ============================================================================

class CampaignNotificationPreference(TimeStampedModel):
    """
    User notification preferences for campaign module.

    Section 5.4: Notification Requirements
    Section 7.4: All notifications configurable by type and delivery channel
    """

    class NotificationType(models.TextChoices):
        """Types of campaign notifications"""
        OVERDUE_REVIEW = 'OVERDUE_REVIEW', 'Overdue Review Points'
        PENDING_DECISION = 'PENDING_DECISION', 'Pending Decisions'
        TASK_DUE = 'TASK_DUE', 'Tasks Approaching Due Date'
        NO_UPDATE_THRESHOLD = 'NO_UPDATE_THRESHOLD', 'Campaign Without Updates'
        MILESTONE_ACHIEVEMENT = 'MILESTONE_ACHIEVEMENT', 'Achievement Milestones'
        PERFORMANCE_ALERT = 'PERFORMANCE_ALERT', 'Performance Alerts'

    class DeliveryChannel(models.TextChoices):
        """Notification delivery channels"""
        IN_APP = 'IN_APP', 'In-App Notification'
        EMAIL = 'EMAIL', 'Email'
        PUSH = 'PUSH', 'Push Notification'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='campaign_notification_preferences',
        help_text="User these preferences apply to"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notification_preferences',
        help_text="Project-specific preferences (null = global default)"
    )

    # Preferences
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
        help_text="Type of notification"
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Whether this notification type is enabled"
    )
    delivery_channels = models.JSONField(
        default=list,
        help_text="List of delivery channels (e.g., ['IN_APP', 'EMAIL'])"
    )

    # Thresholds (optional, type-specific)
    threshold_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Type-specific thresholds (e.g., {'days_without_update': 7})"
    )

    class Meta:
        db_table = 'campaign_notification_preferences'
        unique_together = ['user', 'project', 'notification_type']
        indexes = [
            models.Index(fields=['user', 'enabled']),
            models.Index(fields=['project', 'enabled']),
        ]

    def __str__(self):
        scope = f"[{self.project.name}]" if self.project else "[Global]"
        return f"{self.user.email} {scope} - {self.get_notification_type_display()}: {self.enabled}"


# ============================================================================
# Campaign Template Models
# ============================================================================

class CampaignTemplate(SluggedResourceModelMixin, TimeStampedModel):
    """
    Campaign Template Model - Reusable campaign structures.

    Section 3.1: Campaign Templates
    Section 6.4: Template Information
    """

    class SharingScope(models.TextChoices):
        """Template sharing scope"""
        PERSONAL = 'PERSONAL', 'Personal (Private)'
        TEAM = 'TEAM', 'Team-wide'
        ORGANIZATION = 'ORGANIZATION', 'Organization-wide'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # === Template Metadata ===
    name = models.CharField(
        max_length=255,
        help_text="Template name (must be unique within scope)"
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Template description"
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_campaign_templates',
        help_text="User who created the template"
    )
    version_number = models.PositiveIntegerField(
        default=1,
        help_text="Template version number"
    )
    sharing_scope = models.CharField(
        max_length=20,
        choices=SharingScope.choices,
        default=SharingScope.PERSONAL,
        help_text="Who can use this template"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='campaign_templates',
        help_text="Project scope (for team/org templates)"
    )

    # === Campaign Structure (Section 6.4) ===
    objective = models.CharField(
        max_length=50,
        choices=Campaign.Objective.choices,
        blank=True,
        null=True,
        help_text="Default objective"
    )
    platforms = models.JSONField(
        default=list,
        blank=True,
        help_text="Suggested platform list"
    )
    hypothesis_framework = models.TextField(
        blank=True,
        null=True,
        help_text="Hypothesis template or framework"
    )
    tag_suggestions = models.JSONField(
        default=list,
        blank=True,
        help_text="Suggested tags"
    )

    # === Workflow Configuration (Section 6.4) ===
    task_checklist = models.JSONField(
        default=list,
        blank=True,
        help_text="Task checklist with due date formulas"
    )
    review_schedule_pattern = models.JSONField(
        default=dict,
        blank=True,
        help_text="Review schedule pattern (e.g., {'frequency': 'weekly', 'day': 'Monday'})"
    )
    decision_point_triggers = models.JSONField(
        default=list,
        blank=True,
        help_text="Automated decision point triggers"
    )

    # === Variation Structure (Section 6.4) ===
    recommended_variation_count = models.PositiveIntegerField(
        default=3,
        help_text="Recommended number of variations"
    )
    variation_templates = models.JSONField(
        default=list,
        blank=True,
        help_text="Copy element templates for variations"
    )

    # === Usage Statistics ===
    usage_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this template has been used"
    )

    # === Archive Management (Section 5.5) ===
    is_archived = models.BooleanField(
        default=False,
        help_text="Whether this template is archived (soft delete - preserves campaign history)"
    )

    class Meta:
        db_table = 'campaign_templates'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['creator', 'sharing_scope', 'is_archived']),
            models.Index(fields=['project', 'sharing_scope', 'is_archived']),
            models.Index(fields=['name']),
        ]
        constraints = [
            # Section 5.5: Template names must be unique within user/team scope (excluding archived)
            models.UniqueConstraint(
                fields=['creator', 'name', 'sharing_scope'],
                condition=models.Q(is_archived=False, sharing_scope='PERSONAL'),
                name='unique_template_name_per_creator'
            ),
            models.UniqueConstraint(
                fields=['project', 'name', 'sharing_scope'],
                condition=models.Q(is_archived=False, sharing_scope__in=['TEAM', 'ORGANIZATION']),
                name='unique_template_name_per_project'
            ),
        ]

    def __str__(self):
        archived_flag = " [ARCHIVED]" if self.is_archived else ""
        return f"{self.name} (v{self.version_number}){archived_flag}"

    def increment_usage(self):
        """Increment usage counter"""
        self.usage_count += 1
        self.save(update_fields=['usage_count', 'updated_at'])

    def archive_template(self):
        """Archive this template (Section 5.5: soft delete to preserve history)"""
        self.is_archived = True
        self.save(update_fields=['is_archived', 'updated_at'])

    def restore_template(self):
        """Restore archived template"""
        self.is_archived = False
        self.save(update_fields=['is_archived', 'updated_at'])


# ============================================================================
# Campaign Attachment Models
# ============================================================================

class CampaignAttachment(TimeStampedModel):
    """
    Campaign Attachment Model - Stores files and URLs associated with campaigns.
    
    Supports both file uploads and external URLs for campaign-related assets.
    """

    class AssetType(models.TextChoices):
        """Asset type classifications"""
        IMAGE = 'IMAGE', 'Image'
        DOCUMENT = 'DOCUMENT', 'Document'
        VIDEO = 'VIDEO', 'Video'
        AUDIO = 'AUDIO', 'Audio'
        LINK = 'LINK', 'Link'
        OTHER = 'OTHER', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    campaign = models.ForeignKey(
        Campaign,
        on_delete=models.CASCADE,
        related_name='attachments',
        help_text="Associated campaign"
    )
    file = models.FileField(
        upload_to='campaign/attachments/%Y/%m/%d/',
        null=True,
        blank=True,
        help_text="Uploaded file (optional if URL is provided)"
    )
    url = models.URLField(
        max_length=2048,
        null=True,
        blank=True,
        help_text="External URL (optional if file is provided)"
    )
    asset_type = models.CharField(
        max_length=20,
        choices=AssetType.choices,
        help_text="Type of asset"
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='campaign_attachments',
        help_text="User who uploaded/added the attachment"
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional metadata as key-value pairs"
    )

    class Meta:
        db_table = 'campaign_attachments'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['campaign', '-created_at']),
            models.Index(fields=['asset_type', '-created_at']),
        ]

    def __str__(self):
        asset_name = self.file.name if self.file else self.url
        return f"{self.campaign.name} - {self.get_asset_type_display()}: {asset_name}"

    def clean(self):
        """Validation: ensure at least one of file or url is provided"""
        super().clean()
        if not self.file and not self.url:
            raise ValidationError({
                'file': 'Either file or URL must be provided',
                'url': 'Either file or URL must be provided'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
