"""Provision deterministic users, pool, and approval chain for budget Playwright E2E."""

from __future__ import annotations

import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from access_control.models import Role, RolePermission, UserRole
from budget_approval.models import BudgetPool
from core.admin_utils import assign_org_admin
from core.models import AdChannel, Organization, OrganizationMembership, Permission, Project, ProjectMember, Team
from core.services.auth_tokens import build_user_refresh_token
from core.services.tenant import provision_tenant_schema, slug_to_schema_name
from core.tenant_context import tenant_schema_context
from stripe_meta.permissions import generate_organization_access_token
from task.models import ApprovalChain, ApprovalChainStep

User = get_user_model()

E2E_PASSWORD = "password123!"
CHAIN_NAME = "E2E Budget Chain (A → B → C)"
POOL_NAME = "E2E Budget Pool"
CHANNEL_NAME = "E2E Meta"
TEAM_NAME = "E2E Budget Team"
ROLE_NAME = "E2E Budget Approver"

USER_SPECS = (
    ("requester", "e2e-budget-requester@example.com", False),
    ("approver_a", "e2e-budget-approver-a@example.com", False),
    ("approver_b", "e2e-budget-approver-b@example.com", False),
    ("approver_c", "e2e-budget-approver-c@example.com", False),
    ("org_admin", "e2e-budget-org-admin@example.com", True),
    ("regular", "e2e-budget-regular@example.com", False),
)


class Command(BaseCommand):
    help = (
        "Create deterministic budget E2E users, pool, and a 3-step approval chain. "
        "Prints JSON fixtures for Playwright (tokens, ids, pool composite)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--anchor-email",
            default="devuser@example.com",
            help="Existing user whose org/project anchors the E2E fixtures.",
        )
        parser.add_argument(
            "--project-id",
            type=int,
            help="Optional project id (must belong to the anchor user's org).",
        )
        parser.add_argument(
            "--allow-outside-debug",
            action="store_true",
            help="Required when DEBUG is False (disposable environments only).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_outside_debug"]:
            raise CommandError(
                "Refusing to run with DEBUG=False: this command creates verified "
                "accounts and writes valid JWTs. Pass --allow-outside-debug on "
                "disposable stacks only."
            )

        try:
            anchor = User.objects.get(email=options["anchor_email"], is_active=True)
        except User.DoesNotExist as exc:
            raise CommandError(
                f"Anchor user {options['anchor_email']!r} not found. "
                "Log in locally first or pass --anchor-email."
            ) from exc

        organization = getattr(anchor, "current_organization", None) or getattr(
            anchor, "organization", None
        )
        if organization is None:
            raise CommandError("Anchor user has no organization.")

        try:
            from django.db.models.signals import post_save
            from stripe_meta.signals import create_default_subscription

            post_save.disconnect(create_default_subscription, sender=Organization)
            try:
                provision_tenant_schema(organization.slug)
            finally:
                post_save.connect(create_default_subscription, sender=Organization)
        except ImportError:
            provision_tenant_schema(organization.slug)

        tenant_schema = slug_to_schema_name(organization.slug)
        with tenant_schema_context(tenant_schema):
            payload = self._provision(anchor, organization, options.get("project_id"))
        self.stdout.write(json.dumps(payload, separators=(",", ":")))

    @transaction.atomic
    def _provision(self, anchor, organization, project_id):
        project = self._resolve_project(anchor, organization, project_id)
        team = self._ensure_team(organization)
        role = self._ensure_budget_role(organization)
        users = self._ensure_users(organization, project, team, role)
        pool, channel = self._ensure_pool_and_channel(project)
        single_project = self._ensure_single_approver_project(organization, anchor, team, role, users)
        single_pool, single_channel = self._ensure_pool_and_channel(single_project)
        chain = self._ensure_approval_chain(
            project,
            users["approver_a"],
            users["approver_b"],
            users["approver_c"],
        )

        for user in users.values():
            user.active_project = project
            user.save(update_fields=["active_project"])

        composite = f"{pool.id}:{channel.id}:{pool.currency}"
        single_composite = f"{single_pool.id}:{single_channel.id}:{single_pool.currency}"
        tokens = {}
        for key, user in users.items():
            refresh = build_user_refresh_token(user)
            tokens[key] = {
                "user_id": user.id,
                "email": user.email,
                "username": user.username,
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh),
                "organization_access_token": generate_organization_access_token(user),
            }

        return {
            "organization_id": organization.id,
            "organization_slug": organization.slug,
            "project_id": project.id,
            "project_slug": project.slug,
            "project_name": project.name,
            "team_id": team.id,
            "role_name": ROLE_NAME,
            "budget_pool_id": pool.id,
            "ad_channel_id": channel.id,
            "budget_pool_composite": composite,
            "approval_chain_id": chain.id,
            "approval_chain_name": chain.name,
            "single_approver_project_id": single_project.id,
            "single_approver_project_slug": single_project.slug,
            "single_approver_project_name": single_project.name,
            "single_approver_budget_pool_composite": single_composite,
            "password": E2E_PASSWORD,
            "users": tokens,
        }

    def _resolve_project(self, anchor, organization, project_id):
        if project_id is not None:
            try:
                project = Project.objects.get(pk=project_id, is_deleted=False)
            except Project.DoesNotExist as exc:
                raise CommandError(f"Project {project_id} not found.") from exc
            if project.organization_id != organization.id:
                raise CommandError("Project does not belong to the anchor user's organization.")
            return project

        membership = (
            ProjectMember.objects.filter(user=anchor, is_active=True, project__is_deleted=False)
            .select_related("project")
            .order_by("project_id")
            .first()
        )
        if membership is None:
            raise CommandError(
                "Anchor user has no active project membership. "
                "Create a project or pass --project-id."
            )
        return membership.project

    def _ensure_team(self, organization):
        team, _ = Team.objects.get_or_create(
            organization=organization,
            name=TEAM_NAME,
            defaults={"desc": "Playwright budget E2E"},
        )
        return team

    def _ensure_budget_role(self, organization):
        role, _ = Role.objects.get_or_create(
            organization=organization,
            name=ROLE_NAME,
            defaults={"level": 5},
        )
        permission_specs = [
            ("BUDGET_REQUEST", "VIEW"),
            ("BUDGET_REQUEST", "EDIT"),
            ("BUDGET_REQUEST", "APPROVE"),
            ("BUDGET_POOL", "VIEW"),
            ("BUDGET_POOL", "EDIT"),
        ]
        for module, action in permission_specs:
            permission, _ = Permission.objects.get_or_create(module=module, action=action)
            RolePermission.objects.get_or_create(role=role, permission=permission)
        return role

    def _ensure_users(self, organization, project, team, role):
        users = {}
        for key, email, make_org_admin in USER_SPECS:
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "username": email.split("@")[0],
                    "organization": organization,
                    "current_organization": organization,
                    "is_verified": True,
                    "password_set": True,
                },
            )
            user.organization = organization
            user.current_organization = organization
            user.is_verified = True
            user.password_set = True
            user.set_password(E2E_PASSWORD)
            user.save()

            OrganizationMembership.objects.update_or_create(
                user=user,
                organization=organization,
                defaults={"role": "admin" if make_org_admin else "member", "is_active": True},
            )
            ProjectMember.objects.update_or_create(
                user=user,
                project=project,
                defaults={"role": "member", "is_active": True},
            )

            if make_org_admin:
                assign_org_admin(user, organization)
            elif key != "regular":
                UserRole.objects.update_or_create(
                    user=user,
                    role=role,
                    team=team,
                    defaults={"valid_from": timezone.now(), "valid_to": None},
                )

            users[key] = user
        return users

    def _ensure_pool_and_channel(self, project):
        channel, _ = AdChannel.objects.get_or_create(
            project=project,
            name=CHANNEL_NAME,
        )
        pool, _ = BudgetPool.objects.get_or_create(
            project=project,
            ad_channel=channel,
            name=POOL_NAME,
            currency="AUD",
            defaults={
                "total_amount": Decimal("100000.00"),
                "used_amount": Decimal("0.00"),
            },
        )
        if pool.total_amount < Decimal("50000.00"):
            pool.total_amount = Decimal("100000.00")
            pool.save(update_fields=["total_amount", "updated_at"])
        return pool, channel

    def _ensure_single_approver_project(self, organization, anchor, team, role, users):
        project, _ = Project.objects.get_or_create(
            organization=organization,
            name="E2E Budget Single Approver",
            defaults={"owner": anchor},
        )
        for user in users.values():
            ProjectMember.objects.update_or_create(
                user=user,
                project=project,
                defaults={"role": "member", "is_active": True},
            )
            if user.email != users["regular"].email:
                UserRole.objects.update_or_create(
                    user=user,
                    role=role,
                    team=team,
                    defaults={"valid_from": timezone.now(), "valid_to": None},
                )
        return project

    def _ensure_approval_chain(self, project, approver_a, approver_b, approver_c):
        chain, _ = ApprovalChain.objects.get_or_create(
            project=project,
            task_type="budget",
            defaults={"name": CHAIN_NAME},
        )
        if chain.name != CHAIN_NAME:
            chain.name = CHAIN_NAME
            chain.save(update_fields=["name", "updated_at"])

        step_specs = (
            (1, approver_a),
            (2, approver_b),
            (3, approver_c),
        )
        for order, approver in step_specs:
            ApprovalChainStep.objects.update_or_create(
                chain=chain,
                order=order,
                defaults={"approver": approver},
            )
        return chain
