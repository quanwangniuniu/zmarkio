"""Centralized helpers for admin role checks."""


def assign_org_admin(user, org):
    """Grant Organization Admin (level=2) to *user* for *org*.

    Idempotent — safe to call on every org-creation path.
    Updates both OrganizationMembership (public schema) and UserRole (tenant schema).
    """
    from core.models import Role, OrganizationMembership
    from access_control.models import UserRole

    # Create or update OrganizationMembership in public schema
    OrganizationMembership.objects.update_or_create(
        user=user,
        organization=org,
        defaults={
            'role': 'admin',
            'is_active': True,
        }
    )

    # Create tenant-schema Role + UserRole (existing logic)
    admin_role, _ = Role.objects.get_or_create(
        organization=org,
        name='Organization Admin',
        defaults={'level': 2},
    )
    UserRole.objects.get_or_create(user=user, role=admin_role)


def is_org_admin(user):
    """Return True if the user holds an Organization Admin role in their CURRENT org.

    Checks both OrganizationMembership (public schema) and UserRole (tenant schema)
    for the user's current_organization.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    # Use current_organization with fallback to organization
    current_org = getattr(user, 'current_organization', None)
    if not current_org:
        current_org = getattr(user, 'organization', None)

    if not current_org:
        return False

    # Check OrganizationMembership in public schema
    from core.models import OrganizationMembership
    has_membership_admin = OrganizationMembership.objects.filter(
        user=user,
        organization=current_org,
        is_active=True,
        role='admin'
    ).exists()

    if has_membership_admin:
        return True

    # Fallback: Check tenant-schema UserRole
    # Must switch to tenant schema to query UserRole
    from django.db import connection
    from core.services.tenant import slug_to_schema_name
    from access_control.models import UserRole

    schema_name = slug_to_schema_name(current_org.slug)

    # Save original search_path before switching
    with connection.cursor() as cursor:
        cursor.execute('SHOW search_path')
        original_path = cursor.fetchone()[0]

    # Temporarily switch to tenant schema
    with connection.cursor() as cursor:
        cursor.execute(f'SET search_path TO {schema_name}, public')

    try:
        return UserRole.objects.filter(
            user=user,
            role__organization=current_org,
            role__level=2,
        ).exists()
    finally:
        # Restore original search_path
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {original_path}')


def get_org_admin_org_ids(user):
    """Return a list of org IDs where the user is an org admin.

    Updated for multi-organization support: returns ALL organizations
    where user has admin role, not just the current one.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return []

    from core.models import OrganizationMembership

    # Get all organizations where user is admin via OrganizationMembership
    membership_org_ids = list(
        OrganizationMembership.objects.filter(
            user=user,
            is_active=True,
            role='admin'
        ).values_list('organization_id', flat=True)
    )

    # Also check tenant-schema UserRole for current org (transition period)
    current_org = getattr(user, 'current_organization', None)
    if not current_org:
        current_org = getattr(user, 'organization', None)

    if current_org:
        from django.db import connection
        from core.services.tenant import slug_to_schema_name
        from access_control.models import UserRole

        schema_name = slug_to_schema_name(current_org.slug)

        # Save original search_path before switching
        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            original_path = cursor.fetchone()[0]

        # Temporarily switch to tenant schema
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO {schema_name}, public')

        try:
            has_role_admin = UserRole.objects.filter(
                user=user,
                role__organization=current_org,
                role__level=2,
            ).exists()
            if has_role_admin and current_org.id not in membership_org_ids:
                membership_org_ids.append(current_org.id)
        finally:
            # Restore original search_path
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {original_path}')

    return membership_org_ids


def get_csm_admin_org_ids(user):
    """Return CustomerOrganisation IDs where user is a CSM admin."""
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    from csm.models import CustomerUser
    return list(
        CustomerUser.objects.filter(
            user=user, user_type='admin', is_active=True,
            organisation__isnull=False,
        ).values_list('organisation_id', flat=True).distinct()
    )


def is_csm_admin(user):
    """Return True if user is a CSM admin for at least one org."""
    return len(get_csm_admin_org_ids(user)) > 0


def get_csm_supervisor_org_ids(user):
    """Return CustomerOrganisation IDs where user supervises CSM work.

    Admins count as supervisors — that is already how queue access
    (``csm.services.scope.accessible_queues_for``) and template-tag management
    treat the two roles.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    from csm.models import CustomerUser
    return list(
        CustomerUser.objects.filter(
            user=user, user_type__in=('supervisor', 'admin'), is_active=True,
            organisation__isnull=False,
        ).values_list('organisation_id', flat=True).distinct()
    )


def is_csm_supervisor(user):
    """Return True if user may review CSM conversation quality."""
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    return len(get_csm_supervisor_org_ids(user)) > 0


def get_user_organizations(user):
    """Get all organizations the user belongs to.

    Returns QuerySet of Organization objects where user has active membership.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        from core.models import Organization
        return Organization.objects.none()

    from core.models import Organization, OrganizationMembership
    return Organization.objects.filter(
        is_active=True,
        memberships__user=user,
        memberships__is_active=True
    ).distinct()


def can_user_access_organization(user, organization_id):
    """Check if user has access to the specified organization.

    Returns True if user has an active membership in the organization.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    from core.models import OrganizationMembership
    return OrganizationMembership.objects.filter(
        user=user,
        organization_id=organization_id,
        is_active=True
    ).exists()


def switch_organization(user, organization_id):
    """Switch user's current organization.

    Args:
        user: The user switching organizations
        organization_id: Target organization ID

    Returns:
        Tuple[bool, str|None]: (success, error_message)
    """
    if not can_user_access_organization(user, organization_id):
        return False, "You do not have access to this organization"

    user.current_organization_id = organization_id
    user.save(update_fields=['current_organization_id'])

    from django.core.cache import cache
    cache.delete(f'user:{user.id}:organizations')

    return True, None
