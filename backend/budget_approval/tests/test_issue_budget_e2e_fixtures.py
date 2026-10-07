"""Tests for budget Playwright E2E fixture provisioning."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from access_control.models import UserRole
from budget_approval.models import BudgetPool
from core.models import Organization, Project, ProjectMember
from core.services.tenant import provision_tenant_schema, slug_to_schema_name
from core.tenant_context import tenant_schema_context
from task.models import ApprovalChain, ApprovalChainStep

User = get_user_model()


@pytest.mark.django_db
def test_issue_budget_e2e_fixtures_round_trip():
    org = Organization.objects.create(name="Budget E2E Org", email_domain="budget-e2e.test")
    provision_tenant_schema(org.slug)

    anchor = User.objects.create_user(
        username="budget-e2e-anchor",
        email="budget-e2e-anchor@test.com",
        password="password123!",
        organization=org,
        current_organization=org,
        is_verified=True,
    )

    tenant_schema = slug_to_schema_name(org.slug)
    with tenant_schema_context(tenant_schema):
        project = Project.objects.create(name="Anchor Project", organization=org, owner=anchor)
        ProjectMember.objects.create(user=anchor, project=project, role="owner", is_active=True)

    out = StringIO()
    call_command(
        "issue_budget_e2e_fixtures",
        anchor_email=anchor.email,
        project_id=project.id,
        allow_outside_debug=True,
        stdout=out,
    )
    payload = json.loads(out.getvalue())

    assert payload["project_id"] == project.id
    assert payload["team_id"]
    assert payload["role_name"] == "E2E Budget Approver"
    assert payload["users"]["requester"]["email"] == "e2e-budget-requester@example.com"
    assert payload["users"]["approver_a"]["access_token"]
    assert payload["single_approver_project_id"] != project.id
    assert payload["budget_pool_composite"]
    assert payload["single_approver_budget_pool_composite"]

    with tenant_schema_context(tenant_schema):
        chain = ApprovalChain.objects.get(id=payload["approval_chain_id"])
        assert chain.steps.count() == 3
        assert BudgetPool.objects.filter(project_id=project.id).exists()
        assert UserRole.objects.filter(user__email="e2e-budget-requester@example.com").exists()
        assert ApprovalChainStep.objects.filter(chain=chain, order=1).exists()
