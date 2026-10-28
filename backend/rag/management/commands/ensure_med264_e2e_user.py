"""
Management command: ensure_med264_e2e_user (MED-264 Playwright acceptance test).

Idempotently creates/repairs the dedicated E2E test user for the MED-264
"ask about my retrospective" Playwright test, and gives it active membership
on the MED-264 RAG Eval Fixture project (the fixture seeded by
generate_med264_seed_sql.py / run_project_rag_eval.py).

Why a dedicated user
---------------------
frontend/e2e/auth.setup.ts logs in as DEV_USER_EMAIL/DEV_USER_PASSWORD and
picks the FIRST project returned by GET /api/core/projects/ (which requires
an active ProjectMember row -- see core/views.py ProjectViewSet.get_queryset).
Reusing a real developer's personal account for this would be both unsafe
(depends on someone's actual login) and non-reproducible (their project list
is arbitrary). This command exists so anyone can reproduce the exact fixture
this test depends on with one command, regardless of what else is in the DB.

Two separate membership records are required, not one
-------------------------------------------------------
- ProjectMember (tenant-scoped, inside the org's schema): grants
  GET /api/core/projects/ visibility into the MED-264 project (via
  ProjectViewSet.get_queryset's `member_ids` filter).
- OrganizationMembership (public schema): GET /api/core/onboarding-status/
  computes `needs_onboarding` purely from
  `OrganizationMembership.objects.filter(user=user, is_active=True).exists()`
  -- independent of User.organization/current_organization. Without an active
  OrganizationMembership row, the frontend's onboarding-status check reports
  needs_onboarding=True and the app redirects to the onboarding wizard
  instead of the normal dashboard, breaking auth.setup.ts's assumptions
  regardless of the user's organization/current_organization FKs being set.

Credentials
-----------
The password is never hardcoded/committed: pass --password explicitly, or set
it via the MED264_E2E_USER_PASSWORD environment variable (e.g. in
frontend/.env.local, gitignored, alongside the matching DEV_USER_EMAIL/
DEV_USER_PASSWORD Playwright reads).
"""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.models import Organization, OrganizationMembership, Project, ProjectMember
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from rag.management.commands.run_project_rag_eval import EVAL_ORG_NAME, EVAL_PROJECT_NAME

User = get_user_model()

DEFAULT_EMAIL = 'med264-e2e@rag-eval-harness.internal'
DEFAULT_USERNAME = 'med264_e2e_user'


class Command(BaseCommand):
    help = (
        "Idempotently create/repair the dedicated E2E test user for the MED-264 "
        "Playwright acceptance test, with active Organization + Project membership "
        "on the MED-264 RAG Eval Fixture. Run this once before `npx playwright test` "
        "locally or in CI; safe to re-run any time."
    )

    def add_arguments(self, parser):
        parser.add_argument('--email', default=DEFAULT_EMAIL, help='E2E user email (also USERNAME_FIELD).')
        parser.add_argument(
            '--password', default=None,
            help='E2E user password. If omitted, read from the MED264_E2E_USER_PASSWORD env var.',
        )

    def handle(self, *args, **options):
        email: str = options['email']
        password = options['password'] or os.environ.get('MED264_E2E_USER_PASSWORD')
        if not password:
            raise CommandError(
                "No password provided. Pass --password or set MED264_E2E_USER_PASSWORD "
                "(e.g. in frontend/.env.local, gitignored)."
            )

        try:
            org = Organization.objects.get(name=EVAL_ORG_NAME)
        except Organization.DoesNotExist:
            raise CommandError(
                f"Organization {EVAL_ORG_NAME!r} does not exist yet. Run "
                "`python manage.py run_project_rag_eval` (or generate_med264_seed_sql's seed) first "
                "to provision the MED-264 eval tenant."
            )
        schema = slug_to_schema_name(org.slug)

        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={'username': DEFAULT_USERNAME, 'organization': org, 'current_organization': org},
        )
        user.set_password(password)
        user.organization = org
        user.current_organization = org
        user.is_active = True
        user.save()

        org_membership, org_membership_created = OrganizationMembership.objects.get_or_create(
            user=user, organization=org, defaults={'role': 'member', 'is_active': True},
        )
        if not org_membership.is_active:
            org_membership.is_active = True
            org_membership.save(update_fields=['is_active'])

        with tenant_schema_context(schema):
            try:
                project = Project.objects.get(name=EVAL_PROJECT_NAME)
            except Project.DoesNotExist:
                raise CommandError(
                    f"Project {EVAL_PROJECT_NAME!r} does not exist in schema {schema!r}. Run "
                    "`python manage.py run_project_rag_eval` (or generate_med264_seed_sql's seed) first."
                )

            project_membership, project_membership_created = ProjectMember.objects.get_or_create(
                user=user, project=project, defaults={'role': 'member', 'is_active': True},
            )
            if not project_membership.is_active:
                project_membership.is_active = True
                project_membership.save(update_fields=['is_active'])

        # active_project (public-schema FK, db_constraint=False -- Project is
        # tenant-scoped): the frontend's "active project" state is not purely
        # client-side. Without this set server-side, the app shows "No
        # active project" / "No project selected" regardless of what
        # auth.setup.ts writes into localStorage's project-storage-v1,
        # because the app re-derives the active project from the server on
        # mount and overwrites the client-side guess.
        if user.active_project_id != project.id:
            user.active_project_id = project.id
            user.save(update_fields=['active_project'])

        self.stdout.write(self.style.SUCCESS(
            f"E2E user ready: {email} (user_created={user_created}, org={org.slug!r}, "
            f"org_membership_created={org_membership_created}, project={project.name!r}, "
            f"project_membership_created={project_membership_created})"
        ))
