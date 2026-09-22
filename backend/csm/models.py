import uuid

from django.db import models
from django.db.models import Q
from django.conf import settings
from django.utils import timezone
from core.models import TimeStampedModel, Project, Team
from core.slug_mixins import SluggedResourceModelMixin


def default_operating_hours():
    disabled = {'enabled': False}
    weekday = {'enabled': True, 'start': '09:00', 'end': '17:00'}
    return {
        'monday': weekday.copy(),
        'tuesday': weekday.copy(),
        'wednesday': weekday.copy(),
        'thursday': weekday.copy(),
        'friday': weekday.copy(),
        'saturday': disabled.copy(),
        'sunday': disabled.copy(),
    }


class Queue(SluggedResourceModelMixin, TimeStampedModel):
    # Slug-only URLs. Slug is derived from name.
    slug_source_field = 'name'

    TIER_CHOICES = [
        ('T1', 'T1 Frontline'),
        ('T2', 'T2 Technical Support'),
        ('T3', 'T3 Escalations'),
        ('T4', 'T4 VIP'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, null=True, blank=True, related_name='queues')
    organisation = models.ForeignKey(
        'customer.CustomerOrganisation',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='queues',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    tier = models.CharField(max_length=4, choices=TIER_CHOICES, default='T1')
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    sla_policy = models.ForeignKey(
        'SLAPolicy', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='queues',
    )

    class Meta:
        unique_together = ('organisation', 'name')
        ordering = ['display_order', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_tier_display()})"


class QueueAgent(TimeStampedModel):
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='agents')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='queue_assignments'
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='queue_agent_assignments'
    )

    class Meta:
        unique_together = ('queue', 'user')

    def __str__(self):
        return f"{self.user} - {self.queue.name}"


class QueueTeam(TimeStampedModel):
    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='teams')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='queue_assignments')

    class Meta:
        unique_together = ('queue', 'team')

    def __str__(self):
        return f"{self.team.name} - {self.queue.name}"


class CustomerUser(TimeStampedModel):
    USER_TYPE_CHOICES = [
        ('agent', 'Agent'),
        ('supervisor', 'Supervisor'),
        ('admin', 'Admin'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='customer_user_profiles',
    )
    team = models.ForeignKey(
        Team, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='customer_users',
    )
    queue = models.ForeignKey(
        Queue, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='customer_users',
    )
    organisation = models.ForeignKey(
        'customer.CustomerOrganisation',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='customer_users',
    )
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='agent')
    is_active = models.BooleanField(default=True)
    is_creator = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'queue')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} ({self.get_user_type_display()})"


class CsmNotification(TimeStampedModel):
    NOTIFICATION_TYPES = [
        ('org_invitation', 'Organisation Invitation'),
        ('sla_breach', 'SLA Breach'),
    ]
    ACTION_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='csm_notifications',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='csm_notifications_sent',
    )
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=300)
    message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    action_status = models.CharField(max_length=20, choices=ACTION_STATUS_CHOICES, default='pending')
    organisation = models.ForeignKey(
        'customer.CustomerOrganisation',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='notifications',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.notification_type} → {self.recipient.email} ({self.action_status})"


class Ticket(TimeStampedModel):
    # Built-in statuses. The per-project status machine
    # (TicketStatus / TicketStatusTransition) is the source of truth for
    # transitions and custom statuses; these choices give the five built-ins a
    # display label. Custom statuses are stored as slugs not listed here —
    # get_status_display() falls back to the raw slug for those.
    STATUS_CHOICES = [
        ('todo', 'To Do'),
        ('in_progress', 'In Progress'),
        ('pending_customer', 'Pending Customer Response'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    PRIORITY_CHOICES = [
        ('critical', 'Critical'),
        ('high', 'High'),
        ('medium', 'Medium'),
        ('low', 'Low'),
    ]
    PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}

    queue = models.ForeignKey(Queue, on_delete=models.CASCADE, related_name='tickets')
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='todo')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='assigned_tickets',
    )
    customer_email = models.EmailField(blank=True)
    conversation = models.ForeignKey(
        'Conversation', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tickets',
    )
    first_response_due = models.DateTimeField(null=True, blank=True)
    resolution_due = models.DateTimeField(null=True, blank=True)

    # Set when an agent first replies. Stops the first-response countdown; a
    # value after first_response_due means the target was missed.
    first_responded_at = models.DateTimeField(null=True, blank=True)

    # Guard against re-notifying every sweep once a breach has been announced.
    first_response_breach_notified = models.BooleanField(default=False)
    resolution_breach_notified = models.BooleanField(default=False)

    # Timestamp the ticket last entered "Pending Customer Response".
    # Drives the auto-resolution rule (resolve after N days with no customer reply).
    pending_since = models.DateTimeField(null=True, blank=True)

    # --- CSM-S01-07: form submission context ---
    form = models.ForeignKey(
        'TicketForm', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tickets',
    )
    experience_group = models.ForeignKey(
        'experience_group.ExperienceGroup', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tickets',
    )
    support_project = models.ForeignKey(
        'SupportProject', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tickets',
    )
    work_type = models.ForeignKey(
        'CsmWorkType', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tickets',
    )
    custom_field_values = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    def save(self, *args, **kwargs):
        # Keep the pending-clock in sync on every write path (create,
        # PATCH, task, future callers) so the auto-resolution rule always has a
        # start time. Stamp it when entering Pending Customer Response, clear it
        # on leaving.
        touched = []
        if self.status == 'pending_customer':
            if self.pending_since is None:
                self.pending_since = timezone.now()
                touched.append('pending_since')
        elif self.pending_since is not None:
            # Leaving Pending Customer Response resumes the SLA clock: credit
            # back the time spent waiting on the customer before clearing.
            from csm.services.sla import resume_sla_clock
            resume_sla_clock(self)
            self.pending_since = None
            touched += ['pending_since', 'first_response_due', 'resolution_due']
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'status' in update_fields:
            merged = list(update_fields)
            merged += [f for f in touched if f not in merged]
            kwargs['update_fields'] = merged
        super().save(*args, **kwargs)


class Conversation(TimeStampedModel):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('pending', 'Pending'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    CHANNEL_CHOICES = [
        ('web', 'Web'),
        ('email', 'Email'),
        ('whatsapp', 'WhatsApp'),
    ]

    customer = models.ForeignKey(
        'customer.Customer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='conversations',
    )
    queue = models.ForeignKey(
        Queue, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='conversations',
    )
    assigned_to = models.ForeignKey(
        CustomerUser, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='assigned_conversations',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default='web')
    support_channel = models.ForeignKey(
        'SupportChannel',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='conversations',
    )
    tags = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            # Quality inspection filters and sorts across a whole organisation.
            models.Index(fields=['queue', 'started_at'], name='csm_conv_queue_started_idx'),
            models.Index(fields=['status'], name='csm_conv_status_idx'),
        ]

    def __str__(self):
        customer_name = self.customer.full_name if self.customer else 'Unknown'
        return f"Conversation with {customer_name} [{self.get_status_display()}]"

    @property
    def elapsed_seconds(self):
        end = self.ended_at or timezone.now()
        return int((end - self.started_at).total_seconds())


class ConversationMessage(models.Model):
    SENDER_TYPE_CHOICES = [
        ('agent', 'Agent'),
        ('customer', 'Customer'),
        ('system', 'System'),
    ]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE,
        related_name='messages',
    )
    sender_type = models.CharField(max_length=20, choices=SENDER_TYPE_CHOICES)
    sender_agent = models.ForeignKey(
        CustomerUser, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sent_messages',
    )
    content = models.TextField(blank=True)
    rich_body = models.JSONField(null=True, blank=True)
    image = models.ImageField(upload_to='conversation_images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"[{self.sender_type}] {self.content[:50]}"


class ConversationQualityReview(TimeStampedModel):
    """A supervisor's quality annotation on one conversation.

    One row per (conversation, reviewer): re-rating updates the row rather
    than appending, so report counts stay truthful without having to
    de-duplicate to latest-per-reviewer in every aggregate.

    The agent, queue and organisation are SNAPSHOTS taken at review time.
    ``Conversation.assigned_to`` is mutable and nullable, so joining through it
    live would let a later reassignment retroactively move a rating onto an
    agent who never handled the conversation.
    """

    class Rating(models.TextChoices):
        GOOD = 'good', 'Good'
        NEEDS_IMPROVEMENT = 'needs_improvement', 'Needs Improvement'
        POOR = 'poor', 'Poor'

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE,
        related_name='quality_reviews',
    )
    # Keyed on the auth user, not CustomerUser: one person may hold several
    # CustomerUser rows (unique_together is ('user', 'queue')), so a
    # CustomerUser-keyed constraint would permit duplicate reviews.
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='csm_quality_reviews_given',
    )
    reviewer_name = models.CharField(max_length=200, blank=True, default='')
    rating = models.CharField(max_length=32, choices=Rating.choices)
    comment = models.TextField(blank=True, default='')
    # Not auto_now_add: with upsert semantics the "as of" date must move when a
    # rating is revised, or a stale bucket keeps counting a rewritten rating.
    reviewed_at = models.DateTimeField(default=timezone.now)

    # --- snapshots, resolved once at review time -------------------------
    agent_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='csm_quality_reviews_received',
    )
    agent_customer_user = models.ForeignKey(
        CustomerUser, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='quality_reviews_received',
    )
    agent_name = models.CharField(max_length=200, blank=True, default='')
    queue = models.ForeignKey(
        Queue, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='quality_reviews',
    )
    organisation = models.ForeignKey(
        'customer.CustomerOrganisation', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='csm_quality_reviews',
    )

    class Meta:
        ordering = ['-reviewed_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=('conversation', 'reviewer'),
                name='csm_cqr_uniq_conversation_reviewer',
            ),
        ]
        indexes = [
            models.Index(fields=['organisation', 'reviewed_at'], name='csm_cqr_org_reviewed_idx'),
            models.Index(fields=['agent_user', 'reviewed_at'], name='csm_cqr_agent_reviewed_idx'),
            models.Index(fields=['rating'], name='csm_cqr_rating_idx'),
        ]

    def __str__(self):
        return f"{self.get_rating_display()} - conversation {self.conversation_id}"


class QuickReplyTemplate(SluggedResourceModelMixin, TimeStampedModel):
    """Pre-written reply templates that agents can insert into the conversation composer."""
    slug_source_field = 'title'

    organisation = models.ForeignKey(
        'customer.CustomerOrganisation',
        on_delete=models.CASCADE,
        related_name='quick_reply_templates',
    )
    team = models.ForeignKey(
        Team, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='quick_reply_templates',
        help_text="If set, only members of this team can see the template",
    )
    title = models.CharField(max_length=200, help_text="Short label shown in the template picker")
    content = models.TextField(help_text="Plain-text content inserted into the composer")
    rich_body = models.JSONField(null=True, blank=True, help_text="Optional Tiptap JSON")
    tags = models.JSONField(default=list, blank=True, help_text="List of tag strings for filtering")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_templates',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return f"[Template] {self.title}"


class QuickReplyTemplateHistory(models.Model):
    """Snapshot of a QuickReplyTemplate captured before each edit."""

    template = models.ForeignKey(
        QuickReplyTemplate, on_delete=models.CASCADE,
        related_name='history',
    )
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='template_edits',
    )
    edited_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=200)
    content = models.TextField()
    rich_body = models.JSONField(null=True, blank=True)
    tags = models.JSONField(default=list)

    class Meta:
        ordering = ['-edited_at']

    def __str__(self):
        return f"History of template {self.template_id} at {self.edited_at}"


class TemplateTag(TimeStampedModel):
    """Admin-managed tag entity for quick-reply templates.

    Tags are scoped to an organisation so each CS org has its own tag vocabulary.
    """

    organisation = models.ForeignKey(
        'customer.CustomerOrganisation',
        on_delete=models.CASCADE,
        related_name='template_tags',
    )
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = [('organisation', 'name')]
        ordering = ['name']

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# CSM-S01-07 — Ticket form builder (Phase 1)
# ---------------------------------------------------------------------------

class SupportProject(TimeStampedModel):
    """Stub for CSM-S01-08. Tables only in S01-07; no CRUD API yet."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='support_projects',
    )
    name = models.CharField(max_length=200)
    is_archived = models.BooleanField(default=False)
    default_queue = models.ForeignKey(
        Queue, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='default_for_support_projects',
    )

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'name'],
                name='csm_support_project_unique_name_per_project',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.project_id})"


class CsmWorkType(TimeStampedModel):
    """Stub for CSM-S01-08. Tables only in S01-07; no CRUD API yet."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='csm_work_types',
    )
    name = models.CharField(max_length=200)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'name'],
                name='csm_work_type_unique_name_per_project',
            ),
        ]

    def __str__(self):
        return self.name


class TicketForm(SluggedResourceModelMixin, TimeStampedModel):
    # Slug-only URLs. Slug is derived from name.
    slug_source_field = 'name'

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='ticket_forms',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_ticket_forms',
    )

    class Meta:
        ordering = ['-updated_at', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['project'],
                condition=Q(is_default=True),
                name='csm_unique_default_ticket_form_per_project',
            ),
        ]
        indexes = [
            models.Index(fields=['project', 'is_default'], name='csm_tf_proj_default_idx'),
        ]

    def __str__(self):
        suffix = ' (default)' if self.is_default else ''
        return f"{self.name}{suffix}"


class TicketFormField(models.Model):
    class FieldType(models.TextChoices):
        SYSTEM_SUMMARY = 'system_summary', 'Summary'
        SYSTEM_DESCRIPTION = 'system_description', 'Description'
        SYSTEM_PROJECT = 'system_project', 'Project'
        SYSTEM_WORK_TYPE = 'system_work_type', 'Work Type'
        SHORT_TEXT = 'short_text', 'Short text'
        PARAGRAPH = 'paragraph', 'Paragraph'
        TIMESTAMP = 'timestamp', 'Timestamp'
        DROPDOWN = 'dropdown', 'Dropdown'
        DATE = 'date', 'Date'
        NUMBER = 'number', 'Number'
        LABELS = 'labels', 'Labels'
        CHECKBOX = 'checkbox', 'Checkbox'
        PEOPLE = 'people', 'People'
        URL = 'url', 'URL'
        FILE = 'file', 'File attachment'

    SYSTEM_FIELD_KEYS = frozenset({'summary', 'description', 'project', 'work_type'})

    OPTION_FIELD_TYPES = frozenset({
        FieldType.DROPDOWN,
        FieldType.CHECKBOX,
    })

    form = models.ForeignKey(
        TicketForm, on_delete=models.CASCADE, related_name='fields',
    )
    field_key = models.SlugField(max_length=100)
    label = models.CharField(max_length=200)
    field_type = models.CharField(max_length=30, choices=FieldType.choices)
    is_required = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    options = models.JSONField(default=list, blank=True)
    field_config = models.JSONField(default=dict, blank=True)
    help_text = models.CharField(max_length=500, blank=True)
    max_files = models.PositiveSmallIntegerField(default=10)
    max_file_size_mb = models.PositiveSmallIntegerField(default=25)

    class Meta:
        ordering = ['sort_order', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['form', 'field_key'],
                name='csm_ticketformfield_unique_key_per_form',
            ),
        ]
        indexes = [
            models.Index(fields=['form', 'sort_order'], name='csm_tff_form_order_idx'),
        ]

    def __str__(self):
        return f"{self.form_id}:{self.field_key}"


class TicketFormAssignment(models.Model):
    form = models.ForeignKey(
        TicketForm, on_delete=models.CASCADE, related_name='assignments',
    )
    experience_group = models.ForeignKey(
        'experience_group.ExperienceGroup', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='ticket_form_assignments',
    )
    support_project = models.ForeignKey(
        SupportProject, on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='ticket_form_assignments',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['experience_group'],
                condition=Q(experience_group__isnull=False),
                name='csm_ticketformassignment_unique_per_eg',
            ),
            models.UniqueConstraint(
                fields=['support_project'],
                condition=Q(support_project__isnull=False),
                name='csm_ticketformassignment_unique_per_support_project',
            ),
            models.CheckConstraint(
                check=Q(experience_group__isnull=False) | Q(support_project__isnull=False),
                name='csm_ticketformassignment_requires_target',
            ),
        ]

    def __str__(self):
        if self.experience_group_id:
            return f"Form {self.form_id} → EG {self.experience_group_id}"
        return f"Form {self.form_id} → SP {self.support_project_id}"


class TicketFormSubmission(models.Model):
    form = models.ForeignKey(
        TicketForm, on_delete=models.CASCADE, related_name='submissions',
    )
    experience_group = models.ForeignKey(
        'experience_group.ExperienceGroup', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='ticket_form_submissions',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='ticket_form_submissions',
    )
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Submission {self.id} (form {self.form_id})"


def ticket_attachment_upload_to(instance, filename):
    now = timezone.now()
    return f"csm/ticket_attachments/{now:%Y/%m/%d}/{filename}"


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='attachments',
    )
    submission = models.ForeignKey(
        TicketFormSubmission, on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='attachments',
    )
    file = models.FileField(upload_to=ticket_attachment_upload_to, max_length=500)
    original_name = models.CharField(max_length=255)
    size_bytes = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=Q(ticket__isnull=False) | Q(submission__isnull=False),
                name='csm_ticketattachment_requires_parent',
            ),
        ]

    def __str__(self):
        return self.original_name or str(self.file)


class BusinessHoursCalendar(TimeStampedModel):
    """Weekly working hours and timezone for an SLA countdown.

    When a policy references a calendar, its countdowns advance only during
    the open hours defined here, so nights and weekends do not consume the
    target. A policy with no calendar counts elapsed wall-clock time instead.
    ``schedule`` uses the same shape as SupportChannel.operating_hours.
    """

    project = models.ForeignKey(
        'core.Project', on_delete=models.CASCADE,
        related_name='business_hours_calendars',
    )
    name = models.CharField(max_length=200)
    timezone = models.CharField(max_length=64, default='UTC')
    schedule = models.JSONField(default=default_operating_hours)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SLAPolicy(TimeStampedModel):
    """An SLA policy defining per-priority time targets.

    A project can hold several policies; a queue picks one (``Queue.sla_policy``).
    A queue with no policy falls back to its project's default policy
    (``is_default``), so every ticket resolves to some SLA rather than none.
    """

    project = models.ForeignKey(
        'core.Project', on_delete=models.CASCADE,
        related_name='sla_policies',
    )
    name = models.CharField(max_length=200, default='Default SLA Policy')
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        default=False,
        help_text='Fallback policy for queues in this project with no policy assigned.',
    )
    calendar = models.ForeignKey(
        BusinessHoursCalendar, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sla_policies',
    )
    pause_on_pending = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'SLA Policy'
        verbose_name_plural = 'SLA Policies'
        # A stable order so that picking a fallback policy with .first() (when a
        # project has several and none is marked default) is deterministic.
        ordering = ['id']

    def __str__(self):
        return f"SLA Policy — {self.project_id}"


class SLAPriorityTarget(models.Model):
    """Per-priority SLA time targets within an SLAPolicy."""

    PRIORITY_CHOICES = Ticket.PRIORITY_CHOICES

    policy = models.ForeignKey(
        SLAPolicy, on_delete=models.CASCADE,
        related_name='priority_targets',
    )
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES)
    first_response_minutes = models.PositiveIntegerField(
        default=480,
        help_text='Minutes until first response is due (e.g. 60 = 1 hour)',
    )
    resolution_minutes = models.PositiveIntegerField(
        default=1440,
        help_text='Minutes until resolution is due (e.g. 480 = 8 hours)',
    )

    class Meta:
        unique_together = ('policy', 'priority')
        ordering = ['policy', 'priority']

    def __str__(self):
        return (
            f"{self.policy_id} | {self.priority}: "
            f"{self.first_response_minutes}m / {self.resolution_minutes}m"
        )


class SupportChannel(TimeStampedModel):
    class ChannelType(models.TextChoices):
        LIVE_CHAT = 'live_chat', 'Live chat'
        CONTACT_FORM = 'contact_form', 'Contact form'
        EMAIL = 'email', 'Email'

    class OfflineAlternative(models.TextChoices):
        MESSAGE_ONLY = 'message_only', 'Message only'
        CONTACT_FORM = 'contact_form', 'Contact form'
        KNOWLEDGE_BASE = 'knowledge_base', 'Knowledge base'

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='support_channels',
    )
    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    display_name = models.CharField(max_length=200)
    welcome_message = models.TextField(blank=True)
    # Sent to the customer when a ticket is created from a conversation on this
    # channel (AC5). Blank = no confirmation message is sent.
    ticket_confirmation_message = models.TextField(blank=True)
    operating_hours = models.JSONField(default=default_operating_hours)
    timezone = models.CharField(max_length=64, default='UTC')
    offline_fallback_message = models.TextField(blank=True)
    offline_alternative = models.CharField(
        max_length=20,
        choices=OfflineAlternative.choices,
        default=OfflineAlternative.MESSAGE_ONLY,
    )
    offline_alternative_target_id = models.PositiveIntegerField(null=True, blank=True)
    default_queue = models.ForeignKey(
        Queue, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='support_channels',
    )
    ticket_form = models.ForeignKey(
        TicketForm, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='support_channels',
    )
    email_address = models.EmailField(blank=True)
    embed_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'display_name']
        indexes = [
            models.Index(fields=['project', 'is_active'], name='csm_sc_proj_active_idx'),
            models.Index(fields=['embed_key'], name='csm_sc_embed_key_idx'),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.get_channel_type_display()})"


class SupportChannelExperienceGroup(models.Model):
    channel = models.ForeignKey(
        SupportChannel, on_delete=models.CASCADE, related_name='experience_group_links',
    )
    experience_group = models.ForeignKey(
        'experience_group.ExperienceGroup', on_delete=models.CASCADE,
        related_name='support_channel_links',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['channel', 'experience_group'],
                name='csm_sceg_unique_channel_eg',
            ),
        ]

    def __str__(self):
        return f"Channel {self.channel_id} → EG {self.experience_group_id}"


class CSMInvitation(TimeStampedModel):
    email = models.EmailField()
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='csm_invitations')
    team = models.ForeignKey(
        Team, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='csm_invitations'
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='csm_invitations_sent'
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField()
    accepted = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"Invitation to {self.email} ({'accepted' if self.accepted else 'pending'})"


# --- Ticket status machine & lifecycle ------------------------------------

class TicketStatus(TimeStampedModel):
    """A ticket workflow status, project-scoped (one machine per project).

    The five built-ins (is_builtin=True) are seeded per project; admins add
    custom statuses and place them anywhere in the sequence via `order`.
    `slug` is what Ticket.status stores.
    """
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='ticket_statuses',
    )
    slug = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20, default='#94a3b8')
    order = models.PositiveIntegerField(default=0)
    is_builtin = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name_plural = 'ticket statuses'
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'slug'],
                name='csm_ticketstatus_unique_slug_per_project',
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"


class TicketStatusTransition(TimeStampedModel):
    """A permitted edge (from_status -> to_status) in a project's status machine.

    Presence of a row = the transition is allowed. Absence = blocked.
    Statuses are referenced by slug so custom statuses need no schema change.
    """
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name='ticket_status_transitions',
    )
    from_status = models.CharField(max_length=50)
    to_status = models.CharField(max_length=50)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'from_status', 'to_status'],
                name='csm_ticketstatustransition_unique',
            )
        ]

    def __str__(self):
        return f"{self.from_status} -> {self.to_status}"


class TicketAutoResolveConfig(TimeStampedModel):
    """Per-project rule: auto-move tickets left in Pending Customer Response for
    `days_until_resolve` days with no customer reply to Resolved, notifying them."""
    project = models.OneToOneField(
        Project, on_delete=models.CASCADE, related_name='ticket_auto_resolve_config',
    )
    enabled = models.BooleanField(default=False)
    days_until_resolve = models.PositiveIntegerField(default=2)
    notification_message = models.TextField(
        default=(
            'This ticket has been automatically resolved as we did not hear '
            'back from you. Please reply if you still need help and we will '
            'reopen it.'
        ),
    )

    def __str__(self):
        return f"AutoResolveConfig(project={self.project_id}, enabled={self.enabled})"
