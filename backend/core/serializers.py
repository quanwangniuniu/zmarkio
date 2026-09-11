from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from core.models import (
    Organization,
    OrganizationActivityEvent,
    OrganizationInvitation,
    OrganizationMembership,
    Project,
    ProjectInvitation,
    ProjectMember,
    Role,
)

User = get_user_model()


PROJECT_BASE_ROLES = {
    # Built-in project roles
    "owner",
    "member",
    "viewer",
    # Higher privilege (also used for admin/super gating)
    "Super Administrator",
    "Organization Admin",
    "Team Leader",
    # Additional role names (kept in sync with frontend `getRoleBadgeClasses()`).
    "Campaign Manager",
    "Approver",
    "Reviewer",
    "Budget Controller",
    "Data Analyst",
    "Senior Media Buyer",
    "Specialist Media Buyer",
    "Junior Media Buyer",
    "Designer",
    "Copywriter",
}


class UserSummarySerializer(serializers.ModelSerializer):
    """Lightweight representation of a user."""

    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'name']

    def get_name(self, obj):
        full_name = obj.get_full_name().strip()
        return full_name or obj.username or obj.email


class OrganizationSummarySerializer(serializers.ModelSerializer):
    """Lightweight representation of an organization."""

    class Meta:
        model = Organization
        fields = ['id', 'name', 'slug']


class ProjectSerializer(serializers.ModelSerializer):
    """Full project serializer with membership metadata."""

    organization = OrganizationSummarySerializer(read_only=True)
    owner = UserSummarySerializer(read_only=True)
    is_active = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ['slug', 
            'id',
            'name',
            'description',
            'organization',
            'owner',
            'project_type',
            'work_model',
            'advertising_platforms',
            'objectives',
            'kpis',
            'target_kpi_value',
            'budget_management_type',
            'total_monthly_budget',
            'pacing_enabled',
            'budget_config',
            'primary_audience_type',
            'audience_targeting',
            'created_at',
            'updated_at',
            'is_active',
            'member_count',
        ]
        read_only_fields = ['slug', 'id', 'created_at', 'updated_at']

    def get_is_active(self, obj):
        request = self.context.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            return request.user.active_project_id == obj.id
        return False

    def get_member_count(self, obj):
        return ProjectMember.objects.filter(project=obj, is_active=True).exclude(role='bot').count()


class ProjectSummarySerializer(serializers.ModelSerializer):
    """Compact serializer for listing projects."""

    owner = UserSummarySerializer(read_only=True)
    is_active = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ['id', 'slug', 'name', 'description', 'owner', 'objectives', 'is_active', 'member_count']

    def get_is_active(self, obj):
        request = self.context.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            return request.user.active_project_id == obj.id
        return False

    def get_member_count(self, obj):
        return ProjectMember.objects.filter(project=obj, is_active=True).exclude(role='bot').count()


class ProjectMemberSerializer(serializers.ModelSerializer):
    """Serializer for project memberships."""

    user = UserSummarySerializer(read_only=True)
    project = ProjectSummarySerializer(read_only=True)

    class Meta:
        model = ProjectMember
        fields = ['id', 'user', 'project', 'role', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_role(self, value):
        request = self.context.get("request")
        allowed_roles = set(PROJECT_BASE_ROLES)

        # Add organization-scoped RBAC roles from Role model.
        # - For org roles: Role.organization == request.user.organization
        # - For system roles: Role.organization is NULL
        if request and getattr(request, "user", None) and request.user.is_authenticated:
            user_org = getattr(request.user, "organization", None)
            if user_org:
                rbac_roles = Role.objects.filter(
                    is_deleted=False
                ).filter(Q(organization=user_org) | Q(organization__isnull=True)).values_list("name", flat=True)
            else:
                rbac_roles = Role.objects.filter(
                    is_deleted=False,
                    organization__isnull=True,
                ).values_list("name", flat=True)
        else:
            rbac_roles = Role.objects.filter(is_deleted=False, organization__isnull=True).values_list("name", flat=True)

        allowed_roles.update(set(rbac_roles))
        if value not in allowed_roles:
            raise serializers.ValidationError(
                f'Invalid role "{value}". Allowed roles: {sorted(allowed_roles)}'
            )
        return value


class ProjectMemberInviteSerializer(serializers.Serializer):
    """Serializer for inviting members to a project."""

    email = serializers.EmailField(required=True)
    role = serializers.CharField(required=False, default='member')

    def validate_role(self, value):
        """
        Allow project roles for invites (excluding owner). RBAC names are merged from
        Role rows scoped to the request user's organization when available; options
        align with GET /api/core/projects/{id}/roles/.
        """
        if value == "owner":
            raise serializers.ValidationError("Cannot invite users as project owner.")

        request = self.context.get("request")
        allowed_roles = set(PROJECT_BASE_ROLES) - {"owner"}

        if request and getattr(request, "user", None) and request.user.is_authenticated:
            user_org = getattr(request.user, "organization", None)
            if user_org:
                rbac_roles = Role.objects.filter(is_deleted=False).filter(
                    Q(organization=user_org) | Q(organization__isnull=True)
                ).values_list("name", flat=True)
            else:
                rbac_roles = Role.objects.filter(
                    is_deleted=False,
                    organization__isnull=True,
                ).values_list("name", flat=True)
        else:
            rbac_roles = Role.objects.filter(
                is_deleted=False, organization__isnull=True
            ).values_list("name", flat=True)

        allowed_roles.update(set(rbac_roles))
        if value not in allowed_roles:
            raise serializers.ValidationError(
                f'Invalid role "{value}". Allowed roles: {sorted(allowed_roles)}'
            )
        return value


PROJECT_TYPE_CHOICES = [
    'paid_social',
    'paid_search',
    'programmatic',
    'influencer_ugc',
    'cross_channel',
    'performance',
    'brand_campaigns',
    'app_acquisition',
]

WORK_MODEL_CHOICES = [
    'solo_buyer',
    'small_team',
    'multi_team',
    'external_agency',
]

ADVERTISING_PLATFORM_CHOICES = [
    'meta',
    'google_ads',
    'tiktok',
    'linkedin',
    'snapchat',
    'twitter',
    'pinterest',
    'programmatic_dsp',
    'reddit',
    'other',
]


class ProjectOnboardingSerializer(serializers.Serializer):
    """Serializer for multi-step onboarding payload validation."""

    # SECTION 1: Project Basics
    name = serializers.CharField(max_length=200, required=True)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    # SECTION 2: Project Type & Work Model (JSONField lists)
    project_type = serializers.ListField(
        child=serializers.ChoiceField(choices=PROJECT_TYPE_CHOICES), required=False, allow_empty=True
    )
    work_model = serializers.ListField(
        child=serializers.ChoiceField(choices=WORK_MODEL_CHOICES), required=False, allow_empty=True
    )

    # SECTION 3: Advertising Platforms
    advertising_platforms = serializers.ListField(
        child=serializers.ChoiceField(choices=ADVERTISING_PLATFORM_CHOICES), required=False, allow_empty=True
    )
    advertising_platforms_other = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    # SECTION 4: Objectives & KPIs
    OBJECTIVE_CHOICES = ['awareness', 'consideration', 'conversion', 'retention_loyalty']
    objectives = serializers.ListField(
        child=serializers.ChoiceField(choices=OBJECTIVE_CHOICES),
        required=False,
        allow_empty=True,
        allow_null=True,
        default=list,
        help_text="(Deprecated) Optional objectives to guide dashboard setup",
    )
    kpis = serializers.DictField(
        required=False,
        allow_empty=True,
        allow_null=True,
        default=dict,
        help_text="(Deprecated) Optional KPI data: {'ctr': {'target': 0.02, 'suggested_by': ['awareness']}}",
    )

    # SECTION 5: Budget & Pacing
    budget_management_type = serializers.ChoiceField(
        choices=Project.BUDGET_MANAGEMENT_CHOICES, required=False, allow_null=True
    )
    total_monthly_budget = serializers.DecimalField(
        max_digits=15, decimal_places=2, required=False, allow_null=True, min_value=0
    )
    pacing_enabled = serializers.BooleanField(required=False, default=False)
    budget_config = serializers.DictField(required=False, allow_empty=True)

    # SECTION 6: Audience & Targeting
    primary_audience_type = serializers.ChoiceField(
        choices=Project.PRIMARY_AUDIENCE_CHOICES, required=False, allow_null=True
    )
    target_regions = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True, help_text="Array of ISO region identifiers"
    )
    audience_targeting = serializers.DictField(required=False, allow_empty=True)

    # SECTION 7: Team & Collaboration
    owner_id = serializers.IntegerField(required=False, allow_null=True)
    invite_members = ProjectMemberInviteSerializer(many=True, required=False)

    def validate_project_type(self, value):
        return value or []

    def validate_work_model(self, value):
        return value or []

    def validate_advertising_platforms(self, value):
        return value or []

    def validate_objectives(self, value):
        if not value:
            raise serializers.ValidationError("At least one objective is required.")
        invalid = [item for item in value if item not in self.OBJECTIVE_CHOICES]
        if invalid:
            raise serializers.ValidationError(f"Invalid objectives: {invalid}")
        return value

    def validate_kpis(self, value):
        if not value:
            raise serializers.ValidationError("At least one KPI is required.")
        if not isinstance(value, dict):
            raise serializers.ValidationError("KPIs must be a dictionary.")
        for kpi_key, kpi_data in value.items():
            if not isinstance(kpi_data, dict):
                raise serializers.ValidationError(
                    f"KPI '{kpi_key}' must be a dictionary with 'target' and optional 'suggested_by'."
                )
            if 'target' in kpi_data and kpi_data['target'] is not None:
                try:
                    float(kpi_data['target'])
                except (TypeError, ValueError):
                    raise serializers.ValidationError(f"KPI '{kpi_key}' target must be numeric.")
            suggested_by = kpi_data.get('suggested_by')
            if suggested_by is not None and not isinstance(suggested_by, list):
                raise serializers.ValidationError(f"KPI '{kpi_key}' suggested_by must be a list.")
        return value

    def validate(self, attrs):
        target_regions = attrs.get('target_regions')
        if target_regions:
            audience_targeting = attrs.get('audience_targeting', {})
            audience_targeting['target_regions'] = target_regions
            attrs['audience_targeting'] = audience_targeting
        return attrs


class ProjectInvitationSerializer(serializers.ModelSerializer):
    """Serializer for project invitations"""
    project = ProjectSummarySerializer(read_only=True)
    invited_by = UserSummarySerializer(read_only=True)
    approved_by = UserSummarySerializer(read_only=True)
    is_expired = serializers.SerializerMethodField()
    is_valid = serializers.SerializerMethodField()

    class Meta:
        model = ProjectInvitation
        fields = [
            'id',
            'email',
            'project',
            'role',
            'invited_by',
            'token',
            'approved',
            'approved_by',
            'approved_at',
            'accepted',
            'accepted_at',
            'expires_at',
            'created_at',
            'is_expired',
            'is_valid',
        ]
        read_only_fields = [
            'id',
            'token',
            'accepted',
            'accepted_at',
            'created_at',
            'approved',
            'approved_by',
            'approved_at',
        ]

    def get_is_expired(self, obj):
        return obj.is_expired()

    def get_is_valid(self, obj):
        return obj.is_valid()


class AcceptInvitationSerializer(serializers.Serializer):
    """Serializer for accepting an invitation"""
    token = serializers.CharField(required=True, help_text="Invitation token")
    password = serializers.CharField(
        required=False,
        write_only=True,
        min_length=8,
        help_text="Password for new user account (required if user doesn't exist)"
    )
    username = serializers.CharField(
        required=False,
        help_text="Username for new user account (optional, defaults to email)"
    )


# ============================================================================
# Multi-Organization Support Serializers
# ============================================================================


class OrganizationSerializer(serializers.ModelSerializer):
    """Full organization serializer with membership details."""

    is_current = serializers.SerializerMethodField()
    user_role = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = [
            'id',
            'name',
            'slug',
            'desc',
            'is_active',
            'created_at',
            'updated_at',
            'is_current',
            'user_role',
            'member_count',
            'max_concurrent_sessions',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']

    def get_is_current(self, obj):
        """Check if this is the user's current organization."""
        request = self.context.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            return request.user.current_organization_id == obj.id
        return False

    def get_user_role(self, obj):
        """Get the user's role in this organization."""
        request = self.context.get('request')
        if request and hasattr(request, 'user') and request.user.is_authenticated:
            membership = OrganizationMembership.objects.filter(
                user=request.user,
                organization=obj,
                is_active=True
            ).first()
            return membership.role if membership else None
        return None

    def get_member_count(self, obj):
        """Count active members in this organization."""
        return OrganizationMembership.objects.filter(
            organization=obj,
            is_active=True
        ).count()


class OrganizationMembershipSerializer(serializers.ModelSerializer):
    """Serializer for organization memberships."""

    user = UserSummarySerializer(read_only=True)
    organization = OrganizationSummarySerializer(read_only=True)
    invited_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = [
            'id',
            'user',
            'organization',
            'role',
            'joined_at',
            'is_active',
            'invited_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'joined_at', 'created_at', 'updated_at']


class OrganizationInvitationSerializer(serializers.ModelSerializer):
    """Serializer for organization invitations."""

    organization = OrganizationSummarySerializer(read_only=True)
    invited_by = UserSummarySerializer(read_only=True)
    is_expired = serializers.SerializerMethodField()
    is_valid = serializers.SerializerMethodField()
    invitation_url = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationInvitation
        fields = [
            'id',
            'email',
            'organization',
            'role',
            'invited_by',
            'token',
            'max_uses',
            'use_count',
            'expires_at',
            'is_active',
            'is_expired',
            'is_valid',
            'invitation_url',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'token',
            'use_count',
            'created_at',
            'updated_at',
        ]

    def get_is_expired(self, obj):
        """Check if invitation has expired."""
        return obj.is_expired()

    def get_is_valid(self, obj):
        """Check if invitation is valid for use."""
        return obj.is_valid()

    def get_invitation_url(self, obj):
        """Generate the invitation URL."""
        request = self.context.get('request')
        if request:
            # Build absolute URL for the invitation
            # Frontend will handle the /invite/organization/{token} route
            base_url = request.build_absolute_uri('/').rstrip('/')
            return f"{base_url}/invite/organization/{obj.token}"
        return None


class AcceptOrganizationInvitationSerializer(serializers.Serializer):
    """Serializer for accepting an organization invitation."""

    token = serializers.CharField(required=True, help_text="Organization invitation token")
    password = serializers.CharField(
        required=False,
        write_only=True,
        min_length=8,
        help_text="Password for new user account (required if user doesn't exist)"
    )
    username = serializers.CharField(
        required=False,
        help_text="Username for new user account (optional, defaults to email)"
    )


class SwitchOrganizationSerializer(serializers.Serializer):
    """Serializer for switching current organization."""

    organization_id = serializers.IntegerField(required=True, help_text="Target organization ID")


class OrgActivityUserSerializer(serializers.Serializer):
    """Minimal user info embedded in activity events."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    name = serializers.CharField(allow_null=True, default=None)


class OrganizationActivityEventSerializer(serializers.ModelSerializer):
    """Read-only serializer for OrganizationActivityEvent."""

    actor = OrgActivityUserSerializer(read_only=True)
    target_user = OrgActivityUserSerializer(read_only=True)
    category = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationActivityEvent
        fields = [
            'id',
            'event_type',
            'category',
            'actor',
            'target_user',
            'metadata',
            'message',
            'created_at',
        ]
        read_only_fields = fields

    def get_category(self, obj):
        return obj.category

    def get_message(self, obj):
        from core.services.organization_activity import build_human_readable  # noqa: PLC0415
        return build_human_readable(obj)
