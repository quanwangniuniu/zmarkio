import uuid

from django.db import models, transaction
from core.slug_mixins import SluggedResourceModelMixin
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.base_user import BaseUserManager
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True

class Organization(TimeStampedModel):
    name = models.CharField(max_length=200, unique=True)
    email_domain = models.CharField(max_length=100, blank=True, null=True, help_text="Email domain for SSO organization matching (e.g., 'agencyX.com')")
    parent_org = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='child_organizations'
    )
    desc = models.TextField(blank=True, null=True)
    is_parent = models.BooleanField(default=False)
    slug = models.SlugField(max_length=200, unique=True)
    is_active = models.BooleanField(default=True)
    stripe_customer_id = models.CharField(max_length=255, null=True, blank=True)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if not self.slug:
            self.slug = self._generate_unique_slug()
        # transaction.atomic() creates a savepoint when an outer transaction
        # already exists (e.g. called from a view wrapped in atomic()), so
        # nesting is safe.  If provision_tenant_schema() raises, the savepoint
        # is released and both the INSERT and the CREATE SCHEMA are rolled back
        # — satisfying the AC "no org record without a corresponding schema".
        with transaction.atomic():
            super().save(*args, **kwargs)
            if is_new:
                # Synchronous by design: must share this transaction so that
                # schema-creation failure rolls back the org row atomically.
                # Org creation is low-frequency; the extra latency is acceptable.
                from core.services.tenant import provision_tenant_schema
                provision_tenant_schema(self.slug)
    
    def _generate_unique_slug(self):
        """Generate a unique slug from the organization name"""
        import uuid

        base_slug = slugify(self.name)

        # Fallback: if slugify returns empty (e.g. for Chinese/Korean names),
        # use allow_unicode=True to preserve Unicode characters
        if not base_slug:
            base_slug = slugify(self.name, allow_unicode=True)

        # Last resort: if still empty, use a UUID-based slug
        if not base_slug:
            base_slug = f"org-{uuid.uuid4().hex[:8]}"

        slug = base_slug
        counter = 1

        # Keep checking until we find a unique slug
        while Organization.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        return slug

    def __str__(self):
        return self.name

class Team(TimeStampedModel):
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name="teams"
    )
    name = models.CharField(max_length=200)
    parent = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='child_teams'
    )
    desc = models.TextField(blank=True, null=True)
    is_parent = models.BooleanField(default=False)

    class Meta:
        unique_together = ("organization", "name")

    def __str__(self):
        return f"{self.organization.name} / {self.name}"

# Team Role Constants
class TeamRole:
    """Team-level role constants"""
    LEADER = 2
    MEMBER = 3
    
    CHOICES = [
        (LEADER, 'Team Leader'),
        (MEMBER, 'Member'),
    ]
    
    @classmethod
    def get_role_name(cls, role_id):
        """Get role name by ID"""
        role_map = dict(cls.CHOICES)
        return role_map.get(role_id, 'Unknown')
    
    @classmethod
    def is_valid_role(cls, role_id):
        """Check if role ID is valid"""
        return role_id in [cls.LEADER, cls.MEMBER]
    
    @classmethod
    def can_manage_team(cls, role_id):
        """Check if role can manage team members"""
        return role_id == cls.LEADER

class TeamMember(TimeStampedModel):
    """Team membership model for managing user-team relationships"""
    user = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.CASCADE,
        related_name='team_memberships'
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='members'
    )
    role_id = models.IntegerField(
        choices=TeamRole.CHOICES,
        default=TeamRole.MEMBER,
        help_text="Role of the user in this team"
    )

    class Meta:
        unique_together = ['user', 'team']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.team.name} ({TeamRole.get_role_name(self.role_id)})"

    @property
    def role_name(self):
        """Get the role name for this membership"""
        return TeamRole.get_role_name(self.role_id)

    @property
    def is_leader(self):
        """Check if this member is a team leader"""
        return self.role_id == TeamRole.LEADER

class Role(TimeStampedModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="roles",
        null=True,
        blank=True,
        help_text="Organization this role belongs to. Leave empty for super admin roles."
    )
    name = models.CharField(max_length=100)
    level = models.PositiveIntegerField(
        default=10,
        help_text="Lower number = higher privilege"
    )

    class Meta:
        unique_together = ("organization", "name")
        ordering = ["level"]

    def __str__(self):
        return f"{self.name} (Level {self.level})"

class Permission(TimeStampedModel):
    MODULE_CHOICES = [
        ("ASSET", "Asset"),
        ("CAMPAIGN", "Campaign"),
        ("BUDGET_REQUEST", "Budget Request"),
        ("BUDGET_POOL", "Budget Pool"),
        ("BUDGET_ESCALATION", "Budget Escalation"),
        ("QUEUE", "Queue"),                                                                                                                      
        ("SUPPORT_TEAM", "Support Team"),                                                                                                      
        ("TICKET", "Ticket"),                                                                                                                  
        ("INVITATION", "Invitation"),   
    ]
    ACTION_CHOICES = [
        ("VIEW", "View"),
        ("EDIT", "Edit"),
        ("APPROVE", "Approve"),
        ("DELETE", "Delete"),
        ("EXPORT", "Export"),
        
    ]

    module = models.CharField(max_length=20, choices=MODULE_CHOICES)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)

    class Meta:
        unique_together = ("module", "action")

    def __str__(self):
        return f"{self.module}:{self.action}"

class CustomUserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)

class CustomUser(AbstractUser):
    is_verified = models.BooleanField(default=False)
    email = models.EmailField(unique=True)
    verification_token = models.CharField(max_length=100, blank=True, null=True)
    #For password reset
    password_reset_token = models.CharField(max_length=100, blank=True, null=True)
    password_reset_token_expires_at = models.DateTimeField(blank=True, null=True)

    # For google auth
    google_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    google_registered = models.BooleanField(default=False)
    password_set = models.BooleanField(default=True)
    password_last_changed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp used by elevated-role password rotation policy.",
    )
    auth_token_version = models.PositiveIntegerField(
        default=0,
        help_text="Incremented to invalidate previously issued JWTs after security-sensitive changes.",
    )

    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)

    # Profile fields
    job = models.CharField(max_length=150, blank=True, default='')
    department = models.CharField(max_length=150, blank=True, default='')
    location = models.CharField(max_length=150, blank=True, default='')

    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users',
        help_text="DEPRECATED: Use current_organization instead. Kept for backward compatibility."
    )
    current_organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='current_users',
        help_text="The organization context the user is currently viewing"
    )
    active_project = models.ForeignKey(
        'core.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_users',
        help_text="The currently active project for this user",
        db_constraint=False  # CRITICAL: Project is in tenant schema, User in public schema
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    objects = CustomUserManager()

    def set_password(self, raw_password):
        super().set_password(raw_password)
        if raw_password is not None:
            self.password_last_changed_at = timezone.now()

    def __str__(self):
        return self.email 

class Project(SluggedResourceModelMixin, TimeStampedModel):
    """
    Project model - Top-level container for all workspace activities.
    Stores media buyer configuration collected during onboarding wizard.
    """
    # SECTION 1: Project Basics
    name = models.CharField(max_length=200)
    description = models.TextField(
        blank=True,
        null=True,
        help_text="Project description (optional)"
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="projects",
    )
    owner = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='owned_projects',
        help_text="Project owner (defaults to creator, can be changed)"
    )
    
    # SECTION 2: Project Type & Work Model (multi-select)
    project_type = models.JSONField(
        default=list,
        blank=True,
        help_text="List of project types: ['paid_social', 'paid_search', 'programmatic', 'influencer_ugc', 'cross_channel', 'performance', 'brand_campaigns', 'app_acquisition']"
    )
    work_model = models.JSONField(
        default=list,
        blank=True,
        help_text="List of work models: ['solo_buyer', 'small_team', 'multi_team', 'external_agency']"
    )
    
    # SECTION 3: Advertising Platforms
    advertising_platforms = models.JSONField(
        default=list,
        blank=True,
        help_text="List of advertising platforms (e.g., ['meta', 'google_ads', 'tiktok', 'linkedin', 'snapchat', 'twitter', 'pinterest', 'programmatic_dsp', 'reddit'])"
    )
    
    # SECTION 4: Objectives & KPIs
    objectives = models.JSONField(
        default=list,
        blank=True,
        help_text="Multi-select objectives list (e.g., ['awareness', 'consideration'])"
    )
    kpis = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured KPI configuration: {'ctr': {'target': 0.02, 'suggested_by': ['awareness']}}"
    )
    target_kpi_value = models.CharField(
        max_length=200,
        blank=True,
        null=True,
        help_text="Target KPI value as text (e.g., 'CPA target: $65', 'ROAS target: 3.5')"
    )
    
    # SECTION 5: Budget & Pacing
    BUDGET_MANAGEMENT_CHOICES = [
        ('single_consolidated', 'Single consolidated budget'),
        ('platform_specific', 'Platform-specific budgets'),
        ('campaign_level', 'Campaign-level budgets (recommended)'),
        ('client_mandated', 'Client-mandated budget structure'),
    ]
    budget_management_type = models.CharField(
        max_length=50,
        choices=BUDGET_MANAGEMENT_CHOICES,
        null=True,
        blank=True,
        help_text="How budgets are managed in this project"
    )
    total_monthly_budget = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Total expected monthly budget (used for pacing dashboards and alerts)"
    )
    pacing_enabled = models.BooleanField(
        default=False,
        help_text="Whether pacing insights and alerts are enabled"
    )
    ai_analysis_enabled = models.BooleanField(
        default=True,
        help_text="Whether AI-assisted spreadsheet analysis is available for this project"
    )
    budget_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional budget configuration (pacing settings, alerts, etc.)"
    )
    
    # SECTION 6: Audience & Targeting (Optional)
    PRIMARY_AUDIENCE_CHOICES = [
        ('broad_open', 'Broad / Open Targeting'),
        ('interests_based', 'Interests-Based Audience'),
        ('lookalike_similar', 'Lookalike / Similar Audiences'),
        ('remarketing', 'Remarketing'),
        ('custom_crm', 'Custom CRM-Based Audiences'),
        ('geographic', 'Geographic Targeting'),
    ]
    primary_audience_type = models.CharField(
        max_length=50,
        choices=PRIMARY_AUDIENCE_CHOICES,
        null=True,
        blank=True,
        help_text="Primary target audience type"
    )
    audience_targeting = models.JSONField(
        default=dict,
        blank=True,
        help_text="Audience targeting configuration: {target_regions: [...], geographic_details: {...}, etc.}"
    )

    def __str__(self):
        return self.name

class ProjectMember(TimeStampedModel):
    """Project membership model for managing user-project relationships"""
    user = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.CASCADE,
        related_name='project_memberships'
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='members'
    )
    role = models.CharField(
        max_length=50,
        default='member',
        help_text="Role of the user in this project (e.g., 'owner', 'member', 'viewer')"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this membership is currently active"
    )

    class Meta:
        unique_together = ['user', 'project']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.project.name} ({self.role})"

class AdChannel(TimeStampedModel):
    name = models.CharField(max_length=200)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="ad_channels"
    )

    def __str__(self):
        return self.name


class ProjectInvitation(TimeStampedModel):
    """
    Model for storing project invitations sent to users who don't exist yet.
    When a user registers with the invited email, they'll automatically be added to the project.
    """
    email = models.EmailField(help_text="Email address of the invited user")
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='invitations',
        help_text="Project the user is being invited to"
    )
    role = models.CharField(
        max_length=50,
        default='member',
        help_text="Role the user will have in the project (e.g., 'owner', 'member', 'viewer')"
    )
    approved = models.BooleanField(
        default=False,
        help_text="Whether the invitation has been approved by a project owner"
    )
    approved_by = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_project_invitations',
        help_text="Owner who approved the invitation"
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the invitation was approved"
    )
    invited_by = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.CASCADE,
        related_name='sent_invitations',
        help_text="User who sent the invitation"
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        help_text="Unique token for accepting the invitation"
    )
    accepted = models.BooleanField(
        default=False,
        help_text="Whether the invitation has been accepted"
    )
    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the invitation was accepted"
    )
    expires_at = models.DateTimeField(
        help_text="When the invitation expires"
    )

    class Meta:
        unique_together = ['email', 'project', 'accepted']
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', 'accepted']),
            models.Index(fields=['token']),
        ]

    def __str__(self):
        return f"Invitation to {self.email} for {self.project.name}"

    def is_expired(self):
        """Check if the invitation has expired"""
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def is_valid(self):
        """Check if the invitation is valid (not accepted and not expired)"""
        return self.approved and not self.accepted and not self.is_expired()


class DataExportRequest(TimeStampedModel):
    """Tracks GDPR personal-data export jobs requested by a user."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"

    class ExportFormat(models.TextChoices):
        JSON = "json", "JSON"
        CSV = "csv", "CSV"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "core.CustomUser",
        on_delete=models.CASCADE,
        related_name="data_export_requests",
    )
    export_format = models.CharField(max_length=10, choices=ExportFormat.choices, default=ExportFormat.JSON)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    file = models.FileField(upload_to="privacy_exports/%Y/%m/%d/", blank=True, null=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"Data export {self.id} for {self.user_id} ({self.status})"


class AuditEvent(models.Model):
    """Central append-only audit event with per-row HMAC tamper detection."""

    SIGNATURE_VERSION = "v2"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=120, db_index=True)
    actor = models.ForeignKey(
        "core.CustomUser",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="audit_events",
        db_constraint=False,
    )
    actor_email = models.EmailField(blank=True, default="")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="audit_events",
        db_constraint=False,
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="audit_events",
        db_constraint=False,
    )
    target_type = models.CharField(max_length=120, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=120, blank=True, default="")
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    context = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=120, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    signature_version = models.CharField(max_length=20, default=SIGNATURE_VERSION)
    signature_key_id = models.CharField(max_length=80, blank=True, default="")
    signature_algorithm = models.CharField(max_length=40, blank=True, default="")
    signature = models.CharField(max_length=128, editable=False)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-occurred_at"]),
            models.Index(fields=["project", "-occurred_at"]),
            models.Index(fields=["actor", "-occurred_at"]),
            models.Index(fields=["event_type", "-occurred_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("AuditEvent rows are append-only and cannot be updated.")
        if self.actor_id and not self.actor_email:
            self.actor_email = self.actor.email
        if not self.signature_key_id:
            from core.services.audit_events import active_audit_signature_key_id

            self.signature_key_id = active_audit_signature_key_id()
        if not self.signature_algorithm:
            from core.services.audit_events import audit_signature_algorithm

            self.signature_algorithm = audit_signature_algorithm()
        if not self.signature:
            from core.services.audit_events import sign_audit_event

            self.signature = sign_audit_event(self)
        super().save(*args, **kwargs)

    def verify_signature(self) -> bool:
        from core.services.audit_events import verify_audit_event_signature

        return verify_audit_event_signature(self)

    def __str__(self):
        return f"AuditEvent({self.event_type}, {self.target_type}:{self.target_id})"


class OrganizationMembership(TimeStampedModel):
    """
    Many-to-many relationship between users and organizations.
    Stores organization-level role and membership metadata.
    Lives in PUBLIC schema (not tenant schema).
    """
    user = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.CASCADE,
        related_name='organization_memberships',
        help_text="User who is a member of the organization"
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
        help_text="Organization the user belongs to"
    )
    role = models.CharField(
        max_length=50,
        default='member',
        help_text="Organization-level role: 'admin', 'member', 'viewer'"
    )
    joined_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the user joined the organization"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this membership is currently active"
    )
    invited_by = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='invited_memberships',
        help_text="User who invited this member"
    )

    class Meta:
        unique_together = ['user', 'organization']
        ordering = ['-joined_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self):
        return f"{self.user.email} @ {self.organization.name} ({self.role})"


class OrganizationInvitation(TimeStampedModel):
    """
    Invitation to join an organization.
    Supports reusable invitation links with max_uses limit.
    Lives in PUBLIC schema.
    """
    email = models.EmailField(
        blank=True,
        null=True,
        help_text="Specific email address (optional - if null, anyone can use the link)"
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='invitations',
        help_text="Organization being invited to"
    )
    role = models.CharField(
        max_length=50,
        default='member',
        help_text="Role the invitee will receive: 'admin', 'member', 'viewer'"
    )
    invited_by = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_org_invitations',
        help_text="User who created this invitation"
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        help_text="Unique token for the invitation link"
    )
    max_uses = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of times this invitation can be used (0 = unlimited)"
    )
    use_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of times this invitation has been used"
    )
    expires_at = models.DateTimeField(
        help_text="When the invitation expires"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this invitation is active (can be manually revoked)"
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['organization', 'is_active']),
        ]

    def __str__(self):
        target = self.email or "anyone"
        return f"Invitation to {target} for {self.organization.name}"

    def is_expired(self):
        """Check if the invitation has expired"""
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def is_valid(self):
        """Check if invitation can still be used"""
        if not self.is_active or self.is_expired():
            return False
        # max_uses = 0 means unlimited
        if self.max_uses > 0 and self.use_count >= self.max_uses:
            return False
        return True

    def can_be_used_by(self, email):
        """Check if a specific email can use this invitation"""
        if not self.is_valid():
            return False
        # If invitation has a specific email, it must match
        if self.email and self.email.lower() != email.lower():
            return False
        return True


class OrganizationInvitationUse(TimeStampedModel):
    """
    Audit log of invitation usage.
    Tracks who used which invitation and when.
    """
    invitation = models.ForeignKey(
        OrganizationInvitation,
        on_delete=models.CASCADE,
        related_name='uses',
        help_text="The invitation that was used"
    )
    user = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.CASCADE,
        related_name='invitation_uses',
        help_text="User who used the invitation"
    )
    used_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When the invitation was used"
    )

    class Meta:
        ordering = ['-used_at']
        indexes = [
            models.Index(fields=['invitation', 'used_at']),
        ]

    def __str__(self):
        return f"{self.user.email} used invitation at {self.used_at}"


class OrganizationActivityEvent(models.Model):
    """
    Append-only audit log for organization-level events.
    Covers member joins/leaves/removals, subscription plan changes,
    and token quota milestones.
    """

    class EventType(models.TextChoices):
        # Member events
        MEMBER_JOINED = 'member_joined', 'Member Joined'
        MEMBER_REMOVED = 'member_removed', 'Member Removed'
        MEMBER_LEFT = 'member_left', 'Member Left'
        # Subscription / plan events
        PLAN_SUBSCRIBED = 'plan_subscribed', 'Plan Subscribed'
        PLAN_CHANGED = 'plan_changed', 'Plan Changed'
        PLAN_CANCEL_SCHEDULED = 'plan_cancel_scheduled', 'Plan Cancel Scheduled'
        PLAN_REACTIVATED = 'plan_reactivated', 'Plan Reactivated'
        SEATS_CHANGED = 'seats_changed', 'Seats Changed'
        PLAN_CANCELLED = 'plan_cancelled', 'Plan Cancelled'
        # Token quota events
        TOKEN_QUOTA_WARNING = 'token_quota_warning', 'Token Quota Warning'
        TOKEN_QUOTA_EXCEEDED = 'token_quota_exceeded', 'Token Quota Exceeded'
        TOKEN_OVERAGE_STARTED = 'token_overage_started', 'Token Overage Started'

    CATEGORY_MAP = {
        'member_joined': 'member',
        'member_removed': 'member',
        'member_left': 'member',
        'plan_subscribed': 'plan',
        'plan_changed': 'plan',
        'plan_cancel_scheduled': 'plan',
        'plan_reactivated': 'plan',
        'seats_changed': 'plan',
        'plan_cancelled': 'plan',
        'token_quota_warning': 'token',
        'token_quota_exceeded': 'token',
        'token_overage_started': 'token',
    }

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='activity_events',
        db_index=True,
    )
    event_type = models.CharField(
        max_length=40,
        choices=EventType.choices,
        db_index=True,
    )
    actor = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='org_activity_actor',
        help_text="User who triggered the event (null = system/webhook)",
    )
    target_user = models.ForeignKey(
        'core.CustomUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='org_activity_target',
        help_text="User affected by the event (for member events)",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extra context: plan name, seat count, token threshold, etc.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['organization', '-created_at']),
        ]

    @property
    def category(self):
        return self.CATEGORY_MAP.get(self.event_type, 'other')

    def __str__(self):
        return f"[{self.organization.name}] {self.event_type} at {self.created_at}"
