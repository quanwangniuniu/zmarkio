import os
import uuid
import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from freezegun import freeze_time
from django.utils import timezone

from core.services.tenant import slug_to_schema_name

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

# Delay imports until Django is configured
import django
from django.conf import settings

if not settings.configured:
    django.setup()

from rest_framework.test import APIClient
from core.models import Organization, Project, AdChannel, Team
from task.models import Task
from access_control.models import Role, UserRole, RolePermission
from budget_approval.models import BudgetPool, BudgetRequest, BudgetEscalationRule, BudgetRequestStatus

User = get_user_model()


@pytest.fixture
def api_client():
    """API client for testing"""
    return APIClient()


@pytest.fixture
def django_client():
    """Django test client"""
    return Client()


@pytest.fixture
def organization(db):
    """Create a test organization with a unique name to prevent cross-test UniqueViolation"""
    return Organization.objects.create(
        name=f"Test Org {uuid.uuid4().hex[:8]}",
        email_domain="test.com"
    )


@pytest.fixture
def tenant_schema(organization):
    """
    Switch search_path to the org's tenant schema for the duration of the test.

    access_control tables (UserRole, RolePermission, etc.) live only in the
    tenant schema, not in the public schema.  provision_tenant_schema() resets
    search_path to public after provisioning, so any fixture that writes to
    tenant-only tables must depend on this fixture to restore the correct path.
    """
    _schema = slug_to_schema_name(organization.slug)
    with connection.cursor() as cursor:
        cursor.execute(f'SET search_path TO {_schema}, public')
    yield
    with connection.cursor() as cursor:
        cursor.execute('SET search_path TO public')


@pytest.fixture
def team(organization, tenant_schema):
    """Create a test team in the tenant schema.

    Team is a tenant model; creating it with search_path=public means
    UserRole FK lookups that search org_<slug>.core_team find nothing.
    Depending on tenant_schema sets the correct search_path first.
    """
    return Team.objects.create(
        name="Test Team",
        organization=organization
    )


@pytest.fixture
def project(organization, tenant_schema):
    """Create a test project (in tenant schema so tenant search_path finds it)"""
    return Project.objects.create(
        name="Test Project",
        organization=organization
    )


@pytest.fixture
def task(project):
    """Create a test task"""
    return Task.objects.create(
        summary="Test Task",
        type="budget",
        project=project
    )


@pytest.fixture
def ad_channel(project):
    """Create a test ad channel"""
    return AdChannel.objects.create(
        name="Test Ad Channel",
        project=project
    )


@pytest.fixture
def budget_pool(project, ad_channel):
    """Create a test budget pool"""
    return BudgetPool.objects.create(
        project=project,
        ad_channel=ad_channel,
        total_amount=Decimal('10000.00'),
        used_amount=Decimal('0.00'),
        currency='AUD'
    )


@pytest.fixture
def role(organization, tenant_schema):
    """Create a test role (created in org schema so tenant FK lookups find it)"""
    return Role.objects.create(
        name="Budget Approver",
        organization=organization,
        level=5
    )


@pytest.fixture
def permissions(db):
    """Create test permissions"""
    from core.models import Permission

    permissions = []
    # Create permissions for budget request module
    permissions.append(Permission.objects.get_or_create(module='BUDGET_REQUEST', action='VIEW')[0])
    permissions.append(Permission.objects.get_or_create(module='BUDGET_REQUEST', action='EDIT')[0])
    permissions.append(Permission.objects.get_or_create(module='BUDGET_REQUEST', action='APPROVE')[0])

    # Create permissions for budget pool module
    permissions.append(Permission.objects.get_or_create(module='BUDGET_POOL', action='VIEW')[0])
    permissions.append(Permission.objects.get_or_create(module='BUDGET_POOL', action='EDIT')[0])

    # Create permissions for budget escalation module
    permissions.append(Permission.objects.get_or_create(module='BUDGET_ESCALATION', action='VIEW')[0])
    permissions.append(Permission.objects.get_or_create(module='BUDGET_ESCALATION', action='EDIT')[0])

    return permissions


@pytest.fixture
def role_permissions(role, permissions, tenant_schema):
    """Create role permissions (tenant-schema table: access_control_rolepermission)"""
    role_permissions = []
    for permission in permissions:
        role_permissions.append(RolePermission.objects.create(
            role=role,
            permission=permission
        ))
    return role_permissions


@pytest.fixture
def user1(organization):
    """Create test user 1 with a unique username to avoid parallel-worker conflicts"""
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        username=f'user1_{uid}',
        email=f'user1_{uid}@test.com',
        password='testpass123',
        organization=organization
    )


@pytest.fixture
def user2(organization):
    """Create test user 2 with a unique username to avoid parallel-worker conflicts"""
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        username=f'user2_{uid}',
        email=f'user2_{uid}@test.com',
        password='testpass123',
        organization=organization
    )


@pytest.fixture
def user3(organization):
    """Create test user 3 with a unique username to avoid parallel-worker conflicts"""
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_user(
        username=f'user3_{uid}',
        email=f'user3_{uid}@test.com',
        password='testpass123',
        organization=organization
    )


@pytest.fixture
def superuser(db):
    """Create a superuser with a unique username to avoid parallel-worker conflicts"""
    uid = uuid.uuid4().hex[:8]
    return User.objects.create_superuser(
        username=f'superuser_{uid}',
        email=f'superuser_{uid}@test.com',
        password='testpass123'
    )


@pytest.fixture
def org_admin(organization, tenant_schema):
    """Org admin in the same org, but NOT the chain approver (MED-240).

    Uses assign_org_admin() so is_org_admin() returns True. Sets
    current_organization because is_org_admin checks that field first.
    """
    from core.admin_utils import assign_org_admin

    uid = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        username=f'orgadmin_{uid}',
        email=f'orgadmin_{uid}@test.com',
        password='testpass123',
        organization=organization,
        current_organization=organization,
    )
    assign_org_admin(user, organization)
    return user


@pytest.fixture
def different_organization(db):
    """Create a different organization for cross-org testing"""
    return Organization.objects.create(
        name=f"Different Organization {uuid.uuid4().hex[:8]}",
        email_domain="different.com"
    )


@pytest.fixture
def different_project(different_organization):
    """Create a project in different organization"""
    return Project.objects.create(
        name="Different Project",
        organization=different_organization
    )


@pytest.fixture
def different_task(different_project):
    """Create a task in different organization"""
    return Task.objects.create(
        summary="Different Task",
        type="budget",
        project=different_project
    )


@pytest.fixture
def different_ad_channel(different_project):
    """Create an ad channel in different organization"""
    return AdChannel.objects.create(
        name="Different Ad Channel",
        project=different_project
    )


@pytest.fixture
def different_budget_pool(different_project, different_ad_channel):
    """Create a budget pool in different organization"""
    return BudgetPool.objects.create(
        project=different_project,
        ad_channel=different_ad_channel,
        total_amount=Decimal('5000.00'),
        used_amount=Decimal('0.00'),
        currency='AUD'
    )


@pytest.fixture
def budget_request_different_org(user1, different_task, different_budget_pool, user2, different_ad_channel):
    """Create a budget request in different organization"""
    return BudgetRequest.objects.create(
        task=different_task,
        requested_by=user1,
        amount=Decimal('500.00'),
        currency='AUD',
        status=BudgetRequestStatus.DRAFT,
        budget_pool=different_budget_pool,
        current_approver=user2,
        ad_channel=different_ad_channel,
        notes="Test budget request in different org"
    )


@pytest.fixture
def user_role1(user1, role, team, tenant_schema):
    """Create user role for user1 (tenant-schema table: access_control_userrole)"""
    return UserRole.objects.create(
        user=user1,
        role=role,
        team=team,
        valid_from=timezone.now()
    )


@pytest.fixture
def user_role2(user2, role, team, tenant_schema):
    """Create user role for user2 (tenant-schema table: access_control_userrole)"""
    return UserRole.objects.create(
        user=user2,
        role=role,
        team=team,
        valid_from=timezone.now()
    )


@pytest.fixture
def user_role3(user3, role, team, tenant_schema):
    """Create user role for user3 (tenant-schema table: access_control_userrole)"""
    return UserRole.objects.create(
        user=user3,
        role=role,
        team=team,
        valid_from=timezone.now()
    )


@pytest.fixture
def escalation_rule(budget_pool, role):
    """Create a test escalation rule"""
    return BudgetEscalationRule.objects.create(
        budget_pool=budget_pool,
        threshold_amount=Decimal('5000.00'),
        threshold_currency='AUD',
        escalate_to_role=role,
        is_active=True
    )


@pytest.fixture
def budget_request_draft(user1, task, budget_pool, user2, ad_channel):
    """Create a draft budget request"""
    return BudgetRequest.objects.create(
        task=task,
        requested_by=user1,
        amount=Decimal('1000.00'),
        currency='AUD',
        status=BudgetRequestStatus.DRAFT,
        budget_pool=budget_pool,
        current_approver=user2,
        ad_channel=ad_channel,
        notes="Test budget request"
    )


@pytest.fixture
def budget_request_submitted(user1, task, budget_pool, user2, ad_channel):
    """Create a submitted budget request"""
    return BudgetRequest.objects.create(
        task=task,
        requested_by=user1,
        amount=Decimal('1000.00'),
        currency='AUD',
        status=BudgetRequestStatus.SUBMITTED,
        budget_pool=budget_pool,
        current_approver=user2,
        ad_channel=ad_channel,
        notes="Test budget request"
    )


@pytest.fixture
def budget_request_under_review(user1, task, budget_pool, user2, ad_channel):
    """Create a budget request under review"""
    return BudgetRequest.objects.create(
        task=task,
        requested_by=user1,
        amount=Decimal('1000.00'),
        currency='AUD',
        status=BudgetRequestStatus.UNDER_REVIEW,
        budget_pool=budget_pool,
        current_approver=user2,
        ad_channel=ad_channel,
        notes="Test budget request"
    )


@pytest.fixture
@freeze_time("2024-01-01 10:00:00")
def frozen_time():
    """Freeze time for consistent testing"""
    return "2024-01-01 10:00:00" 
