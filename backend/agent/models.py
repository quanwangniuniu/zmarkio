import uuid
from django.db import models
from django.db.models import Q
from django.conf import settings
from core.models import TimeStampedModel
from core.slug_mixins import SluggedResourceModelMixin


class AgentSession(TimeStampedModel):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('archived', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='agent_sessions',
    )
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='agent_sessions',
    )
    title = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    approval_required = models.BooleanField(
        default=False,
        help_text="When true, external side effects require user approval before commit.",
    )
    is_pinned = models.BooleanField(default=False)
    last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="User read cursor; unread when assistant messages exist after this time.",
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"AgentSession {self.id} - {self.title}"


class AgentMessage(TimeStampedModel):
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant'),
        ('system', 'System'),
    ]
    MESSAGE_TYPE_CHOICES = [
        ('text', 'Text'),
        ('analysis', 'Analysis'),
        ('decision_draft', 'Decision Draft'),
        ('task_created', 'Task Created'),
        ('confirmation_request', 'Confirmation Request'),
        ('workflow_confirm', 'Workflow Confirm'),
        ('workflow_choice', 'Workflow Choice'),
        ('approval_request', 'Approval Request'),
        ('follow_up_prompt', 'Follow-up Prompt'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        AgentSession,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    message_type = models.CharField(
        max_length=30, choices=MESSAGE_TYPE_CHOICES, default='text'
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.role}: {self.content[:50]}"


class ImportedCSVFile(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255, help_text="Stored filename on disk")
    original_filename = models.CharField(max_length=255, help_text="Original upload filename")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='imported_csv_files',
    )
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='imported_csv_files',
    )
    row_count = models.IntegerField(default=0)
    column_count = models.IntegerField(default=0)
    file_size = models.BigIntegerField(default=0)
    spreadsheet = models.ForeignKey(
        'spreadsheet.Spreadsheet',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='imported_csv_files',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.original_filename} ({self.row_count} rows)"


class FieldCategory(TimeStampedModel):
    """
    An extensible registry of semantic column categories.

    System categories (is_system=True) are seeded once from SYSTEM_CATEGORIES and
    are never modified by user actions.  When a user manually assigns a category
    name that does not yet exist, a new project-scoped (or global) FieldCategory
    record is created automatically so future AI detection can recognise it.

    The 'unknown' system category is always present and is the default for columns
    the AI could not classify — users can reassign it at any time.
    """

    SYSTEM_CATEGORIES = [
        ('identifier', 'Identifier', 'Columns that identify an entity: name, ID, campaign, ad set'),
        ('financial', 'Financial', 'Monetary values: spend, cost, budget, revenue, ROAS, CPM, CPC'),
        ('engagement', 'Engagement', 'User interaction counts: impressions, clicks, reach, video views'),
        ('conversion', 'Conversion', 'Goal-completion events: purchases, leads, add-to-cart, sign-ups'),
        ('performance_ratio', 'Performance Ratio', 'Derived rate metrics: CTR, CVR, CPA, frequency'),
        ('temporal', 'Temporal', 'Date and time columns: date, week, month, reporting period'),
        ('unknown', 'Unknown', 'Could not be automatically categorised — user can reassign'),
    ]

    name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Internal snake_case key, e.g. 'financial' or 'custom_brand_metric'",
    )
    display_name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default='')
    is_system = models.BooleanField(
        default=False,
        help_text="True for built-in categories; these cannot be renamed or deleted",
    )
    color = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text="Optional UI badge color, e.g. '#4CAF50'",
    )
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='field_categories',
        help_text="Null = global category; set = private to this project",
    )

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Field categories'
        indexes = [
            models.Index(fields=['name', 'is_system']),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.name})"

    @classmethod
    def get_or_create_by_name(cls, name, project=None):
        """
        Return a FieldCategory for the given name, creating one if it does not exist.

        System categories are matched globally.  Unknown names produce a new
        non-system record scoped to the provided project (or global if None).
        """
        obj = cls.objects.filter(name=name).first()
        if obj:
            return obj, False
        display = name.replace('_', ' ').title()
        obj = cls.objects.create(
            name=name,
            display_name=display,
            is_system=False,
            project=project,
        )
        return obj, True


class DataSchemaTemplate(TimeStampedModel):
    """
    A reusable column-schema definition — the DB-driven replacement for the
    hard-coded SCHEMA_REGISTRY dict in column_registry.py.

    System templates (is_system=True) are seeded once from SCHEMA_REGISTRY.
    Learned templates (is_learned=True) are created automatically when the LLM
    successfully identifies a previously-unknown format with high confidence,
    so the same format is matched locally on the next upload without an LLM call.

    column_definitions is a JSON array of objects:
      [{
        "canonical_name": "amount_spent",
        "aliases": ["spend", "cost", "ad spend"],
        "category": "financial",
        "value_type": "number",
        "description": "Total amount spent (currency)"
      }, ...]
    """

    SOURCE_PLATFORM_CHOICES = [
        ('meta_ads', 'Meta Ads'),
        ('google_ads', 'Google Ads'),
        ('tiktok_ads', 'TikTok Ads'),
        ('klaviyo', 'Klaviyo'),
        ('mailchimp', 'Mailchimp'),
        ('custom', 'Custom / Unknown Platform'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="Human-readable name, e.g. 'Meta Ads Performance'")
    source_platform = models.CharField(
        max_length=50,
        choices=SOURCE_PLATFORM_CHOICES,
        default='custom',
    )
    description = models.TextField(blank=True, default='')
    is_system = models.BooleanField(
        default=False,
        help_text="True for templates seeded from code; protected from automatic deletion",
    )
    is_learned = models.BooleanField(
        default=False,
        help_text="True when auto-generated from a high-confidence LLM detection result",
    )
    match_threshold = models.FloatField(
        default=0.5,
        help_text="Minimum fraction of file columns that must match for this template to be applied",
    )
    usage_count = models.IntegerField(
        default=0,
        help_text="Number of uploaded files matched against this template — used for ranking",
    )
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='schema_templates',
        help_text="Null = global template; set = private to this project",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_schema_templates',
    )
    column_definitions = models.JSONField(
        default=list,
        help_text="List of column spec objects (canonical_name, aliases, category, value_type, description)",
    )

    class Meta:
        ordering = ['-usage_count', 'name']
        indexes = [
            models.Index(fields=['source_platform', 'is_system']),
            models.Index(fields=['project', 'is_system']),
        ]

    def __str__(self):
        return f"{self.name} ({self.source_platform})"


class ImportedDataField(TimeStampedModel):
    """
    One record per column in an uploaded file (the 'fields' schema table).

    Created when the user confirms the column mapping.  The category field is a
    plain CharField (not a FK) so it can hold any value — including user-defined
    categories that may not yet exist in FieldCategory.  The service layer calls
    FieldCategory.get_or_create_by_name() to keep the category registry in sync.

    Pre-computed column statistics (null_count, unique_count, min/max, sample_values)
    are populated once at import time so the analysis engine never needs to re-scan
    the source file for these figures.
    """

    VALUE_TYPE_STRING = 'string'
    VALUE_TYPE_NUMBER = 'number'
    VALUE_TYPE_BOOLEAN = 'boolean'
    VALUE_TYPE_DATE = 'date'
    VALUE_TYPE_CHOICES = [
        (VALUE_TYPE_STRING, 'String'),
        (VALUE_TYPE_NUMBER, 'Number'),
        (VALUE_TYPE_BOOLEAN, 'Boolean'),
        (VALUE_TYPE_DATE, 'Date'),
    ]

    file = models.ForeignKey(
        ImportedCSVFile,
        on_delete=models.CASCADE,
        related_name='data_fields',
    )
    matched_template = models.ForeignKey(
        DataSchemaTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='matched_fields',
        help_text="Schema template that identified this column, if any",
    )
    original_name = models.CharField(max_length=255, help_text="Column header as it appears in the source file")
    canonical_name = models.CharField(
        max_length=255,
        default='unknown',
        help_text="AI-detected snake_case name confirmed by the user, or 'unknown'",
    )
    value_type = models.CharField(max_length=20, choices=VALUE_TYPE_CHOICES, default=VALUE_TYPE_STRING)
    category = models.CharField(
        max_length=50,
        default='unknown',
        help_text=(
            "Semantic category key (references FieldCategory.name). "
            "Stored as a plain string so user-defined categories work without a FK constraint."
        ),
    )
    confidence = models.FloatField(default=0.0, help_text="AI detection confidence 0.0-1.0")
    position = models.IntegerField(default=0, help_text="0-based column index in the source file")

    # Pre-computed column statistics
    null_count = models.IntegerField(default=0)
    unique_count = models.IntegerField(default=0)
    min_value = models.TextField(null=True, blank=True, help_text="Minimum value as string (numeric columns)")
    max_value = models.TextField(null=True, blank=True, help_text="Maximum value as string (numeric columns)")
    sample_values = models.JSONField(
        default=list,
        help_text="Up to 5 representative non-null values, for UI display and LLM context",
    )

    class Meta:
        ordering = ['position']
        unique_together = [['file', 'position']]
        indexes = [
            models.Index(fields=['file', 'category']),
            models.Index(fields=['file', 'canonical_name']),
        ]

    def __str__(self):
        return f"{self.original_name} -> {self.canonical_name} ({self.category})"


class ImportedDataRecord(models.Model):
    """
    One record per data row in an uploaded file — hybrid storage design.

    Core fields (file, row_index, created_at, quality_score) are proper DB columns
    for fast filtering and sorting.

    All cell values live in the JSON 'data' field keyed by canonical_name:
      {"campaign_name": "FB Ads", "amount_spent": 500, "ctr": 0.032}
    Columns with an 'unknown' canonical name are stored under their original header.

    quality_score (0.0-1.0) is the fraction of non-unknown, non-null fields in
    this row — useful for filtering incomplete rows before sending data to the LLM.

    Using a plain Model (not TimeStampedModel) to minimise per-row overhead.
    """

    file = models.ForeignKey(
        ImportedCSVFile,
        on_delete=models.CASCADE,
        related_name='data_records',
    )
    row_index = models.IntegerField(help_text="0-based row index in the source file")
    created_at = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(
        default=dict,
        help_text="Cell values keyed by canonical_name (or original header for unknown columns)",
    )
    quality_score = models.FloatField(
        default=1.0,
        help_text="Fraction of confirmed (non-unknown) fields with non-null values; 0.0-1.0",
    )

    class Meta:
        ordering = ['row_index']
        unique_together = [['file', 'row_index']]
        indexes = [
            models.Index(fields=['file', 'row_index']),
            models.Index(fields=['file', 'quality_score']),
        ]

    def __str__(self):
        return f"Record row={self.row_index} file={self.file_id}"


class AgentWorkflowDefinition(SluggedResourceModelMixin, TimeStampedModel):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('draft', 'Draft'),
        ('archived', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='agent_workflow_definitions',
        null=True,
        blank=True,
    )
    is_default = models.BooleanField(default=False)
    is_system = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_workflow_definitions',
    )

    # Trigger configuration fields
    trigger_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="""
        Trigger configuration JSON structure:
        {
            "trigger_type": "polling" | "instant" | "scheduled" | "manual",
            "polling": {
                "interval_minutes": 5 | 15 | 30 | 60,
                "data_sources": ["spreadsheet", "task", "decision"],
                "conditions": [{"type": "spreadsheet_upload", "project_id": 123}, ...]
            },
            "instant": {
                "event_types": ["task.created", "task.status_changed", ...],
                "webhook_enabled": true,
                "webhook_secret": "auto_generated_uuid",
                "filters": {"project_id": 123, "task_priority": ["HIGH", "CRITICAL"]}
            },
            "scheduled": {
                "cron_expression": "0 9 * * 1",
                "timezone": "UTC",
                "enabled": true
            },
            "manual": {
                "require_confirmation": true,
                "allowed_users": [user_id_list] or "all"
            }
        }
        """
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name


class AgentWorkflowStep(TimeStampedModel):
    STEP_TYPE_CHOICES = [
        ('analyze_data', 'Analyze Data'),
        ('call_dify', 'Call LLM (Legacy)'),
        ('call_llm', 'Call LLM'),
        ('create_decision', 'Create Decision'),
        ('create_tasks', 'Create Tasks'),
        ('generate_miro_snapshot', 'Generate Miro Snapshot'),
        ('create_miro_board', 'Create Miro Board'),
        ('custom_api', 'Custom API'),
        ('await_confirmation', 'Await Confirmation'),
        ('detect_columns', 'Detect Columns'),
        ('normalize_data', 'Normalize Data'),
        ('generate_criteria', 'Generate Criteria'),
        # Flow control (UI-driven; executor is a no-op pass-through)
        ('if_else', 'If - Else'),
        ('merge', 'Merge'),
        ('loop', 'Loop'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        AgentWorkflowDefinition,
        on_delete=models.CASCADE,
        related_name='steps',
    )
    name = models.CharField(max_length=255)
    step_type = models.CharField(max_length=30, choices=STEP_TYPE_CHOICES)
    order = models.PositiveIntegerField()
    config = models.JSONField(default=dict, blank=True)
    description = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['order']
        # Partial unique index: only active (non-deleted) steps must have unique orders.
        # Soft-deleted steps retain their order value without blocking future assignments.
        constraints = [
            models.UniqueConstraint(
                fields=['workflow', 'order'],
                condition=Q(is_deleted=False),
                name='unique_active_workflow_step_order',
            )
        ]

    def __str__(self):
        return f"{self.workflow.name} - Step {self.order}: {self.name}"


class AgentWorkflowRun(TimeStampedModel):
    STATUS_CHOICES = [
        ('analyzing', 'Analyzing'),
        ('awaiting_confirmation', 'Awaiting Confirmation'),
        ('awaiting_external_approval', 'Awaiting External Approval'),
        ('creating_decision', 'Creating Decision'),
        ('creating_tasks', 'Creating Tasks'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        AgentSession,
        on_delete=models.CASCADE,
        related_name='workflow_runs',
    )
    spreadsheet = models.ForeignKey(
        'spreadsheet.Spreadsheet',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agent_workflow_runs',
    )
    decision = models.ForeignKey(
        'decision.Decision',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agent_workflow_runs',
    )
    workflow_definition = models.ForeignKey(
        AgentWorkflowDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='workflow_runs',
    )
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default='analyzing')
    current_step_order = models.PositiveIntegerField(null=True, blank=True)
    analysis_result = models.JSONField(null=True, blank=True)
    created_tasks = models.JSONField(default=list, blank=True)
    created_decisions = models.JSONField(default=list, blank=True)
    miro_snapshot = models.JSONField(null=True, blank=True)
    miro_board = models.ForeignKey(
        'miro.Board',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='agent_workflow_runs',
    )
    success_criteria = models.JSONField(null=True, blank=True)
    generation_outputs_requested = models.JSONField(
        null=True,
        blank=True,
        help_text='User-selected generation outputs for this run (upload-analyze).',
    )
    error_message = models.TextField(null=True, blank=True)
    chat_follow_up_started = models.BooleanField(default=False)
    chat_followed_up = models.BooleanField(default=False)
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"WorkflowRun {self.id} - {self.status}"


class AgentStepExecution(TimeStampedModel):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('awaiting', 'Awaiting Confirmation'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow_run = models.ForeignKey(
        AgentWorkflowRun,
        on_delete=models.CASCADE,
        related_name='step_executions',
    )
    step = models.ForeignKey(
        AgentWorkflowStep,
        on_delete=models.SET_NULL,
        null=True,
        related_name='executions',
    )
    step_order = models.PositiveIntegerField()
    step_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    input_data = models.JSONField(null=True, blank=True)
    output_data = models.JSONField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['step_order']

    def __str__(self):
        return f"Run {self.workflow_run_id} - Step {self.step_order}: {self.status}"


class AgentPendingExternalApproval(TimeStampedModel):
    """Human-in-the-loop gate before agent commits an external side effect."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        AgentSession,
        on_delete=models.CASCADE,
        related_name='pending_external_approvals',
    )
    workflow_run = models.ForeignKey(
        AgentWorkflowRun,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='pending_external_approvals',
    )
    step_execution = models.ForeignKey(
        AgentStepExecution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pending_external_approvals',
    )
    kind = models.CharField(
        max_length=64,
        help_text='Registry key, e.g. task, miro_board, calendar_event, forward_message.',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    draft = models.JSONField(default=dict)
    commit_context = models.JSONField(default=dict)
    destination_options = models.JSONField(default=list)
    default_destination = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"PendingExternalApproval {self.id} ({self.kind}) {self.status}"


class AgentWorkflowTemplate(SluggedResourceModelMixin, TimeStampedModel):
    """
    Reusable workflow template that stores workflow steps configuration.

    Templates provide a named, categorized, shareable abstraction for workflow blueprints.
    The same template can be applied to multiple projects, enabling consistent
    "Agent takes care of the project" behavior.

    Templates are completely independent - they store their own steps configuration.
    Creating a template from a workflow copies the steps (no dependency).
    Applying a template creates a new workflow with copied steps (no dependency).

    Visibility is determined by:
      - organization: if set, all members of that org can see this template (org section)
      - projects:     M2M — visible to members of any listed project (project section)
      - both empty:   only the creator can see it (private section)
    A template may appear in both org and project sections simultaneously.
    """

    CATEGORY_CHOICES = [
        ('review', 'Review'),
        ('optimization', 'Optimization'),
        ('analysis', 'Analysis'),
        ('reporting', 'Reporting'),
        ('other', 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text='Template name, e.g. "Q4 Campaign Review"')
    description = models.TextField(blank=True, default='')
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='other',
        help_text='Predefined category for organizing templates',
    )

    # Template stores its own steps configuration (fully independent)
    steps_config = models.JSONField(
        default=list,
        blank=True,
        help_text='Array of step configurations: [{"step_type": "...", "name": "...", "order": 0, "config": {...}}, ...]',
    )

    # DEPRECATED: Legacy field, will be removed after data migration
    workflow_definition = models.ForeignKey(
        AgentWorkflowDefinition,
        on_delete=models.PROTECT,
        related_name='templates',
        help_text='DEPRECATED: Legacy cloned workflow, being replaced by steps_config',
        null=True,
        blank=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_workflow_templates',
    )

    # Sharing: organization level — visible to all members of this org
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='workflow_templates',
        help_text='If set, all members of this organization can see the template',
    )

    # Sharing: project level — M2M, visible to members of any listed project
    projects = models.ManyToManyField(
        'core.Project',
        blank=True,
        related_name='workflow_templates',
        help_text='Members of any listed project can see this template',
    )

    use_cases = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Example scenarios / phrases describing when to use this template '
            '(documentation for users; not used for automatic routing).'
        ),
    )

    class Meta:
        db_table = 'agent_workflow_template'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['category']),
            models.Index(fields=['organization', 'is_deleted']),
            models.Index(fields=['created_by', 'is_deleted']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class WorkflowTriggerLog(TimeStampedModel):
    """
    Records every trigger attempt (successful or failed) for audit and debugging.
    Used for the trigger history timeline and monitoring dashboard.
    """

    TRIGGER_TYPE_CHOICES = [
        ('polling', 'Polling'),
        ('instant', 'Instant'),
        ('scheduled', 'Scheduled'),
        ('manual', 'Manual'),
    ]

    STATUS_CHOICES = [
        ('triggered', 'Triggered'),
        ('skipped', 'Skipped'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    workflow = models.ForeignKey(
        AgentWorkflowDefinition,
        on_delete=models.CASCADE,
        related_name='trigger_logs',
    )

    trigger_type = models.CharField(max_length=20, choices=TRIGGER_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    # Context about what caused the trigger
    trigger_context = models.JSONField(
        default=dict,
        help_text="""
        Context about what caused the trigger:
        {
            "event_type": "task.status_changed",
            "task_id": 456,
            "old_status": "TODO",
            "new_status": "DONE",
            "spreadsheet_id": 789,
            "webhook_source": "zapier",
            "manual_user_id": 123
        }
        """
    )

    # Generated workflow run (if successfully triggered)
    workflow_run = models.ForeignKey(
        'AgentWorkflowRun',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='trigger_log',
    )

    # Error message (if failed)
    error_message = models.TextField(null=True, blank=True)

    # Execution time in milliseconds
    execution_time_ms = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['workflow', '-created_at']),
            models.Index(fields=['trigger_type', 'status']),
            models.Index(fields=['created_at']),  # For cleanup
        ]

    def __str__(self):
        return f"{self.trigger_type} trigger for {self.workflow.name} - {self.status}"


class WorkflowTriggerState(TimeStampedModel):
    """
    Maintains state for each workflow's trigger mechanism to enable:
    - Idempotency: prevent duplicate triggers for the same event
    - Deduplication: track last checked data hash for polling
    - Scheduling: track next scheduled run time
    """

    workflow = models.OneToOneField(
        AgentWorkflowDefinition,
        on_delete=models.CASCADE,
        related_name='trigger_state',
        primary_key=True,
    )

    # Polling state
    last_polling_check = models.DateTimeField(null=True, blank=True)
    last_checked_data_hash = models.TextField(
        blank=True,
        default='',
        help_text="Hash of the data checked in last polling cycle (for deduplication)"
    )

    # Scheduling state
    next_scheduled_run = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Next scheduled execution time (computed from cron expression)"
    )

    # Last successful trigger
    last_successful_trigger = models.DateTimeField(null=True, blank=True)
    last_trigger_type = models.CharField(max_length=20, blank=True, default='')

    # Rate limiting (prevent trigger spam)
    trigger_count_last_hour = models.IntegerField(default=0)
    trigger_count_reset_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'agent_workflow_trigger_state'

    def __str__(self):
        return f"TriggerState for {self.workflow.name}"
