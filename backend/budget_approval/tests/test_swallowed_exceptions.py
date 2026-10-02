"""
Organization resolution in budget object permissions falls back to
user.organization when the object's relation chain cannot be loaded. The
fallback stays, but it must be logged (MED-401).
"""
import logging
from unittest.mock import MagicMock, patch

from django.db import OperationalError

from budget_approval.permissions import ApprovalPermission, BudgetRequestPermission

LOGGER = "budget_approval.permissions"


class _BrokenPool:
    @property
    def project(self):
        raise OperationalError("connection lost")


class _BudgetRequest:
    pk = 42
    budget_pool = _BrokenPool()
    requested_by = None
    current_approver = None


def _request(method):
    user = MagicMock(is_superuser=False, id=7, organization="fallback-org")
    return MagicMock(user=user, method=method, headers={})


def test_budget_request_permission_logs_failed_organization_lookup(caplog):
    with patch("budget_approval.permissions.user_has_team", return_value=False), patch(
        "budget_approval.permissions.has_rbac_permission", return_value=False
    ) as rbac, caplog.at_level(logging.WARNING, logger=LOGGER):
        allowed = BudgetRequestPermission().has_object_permission(
            _request("GET"), MagicMock(action="retrieve"), _BudgetRequest()
        )

    assert allowed is False
    assert rbac.call_args.args[3] == "fallback-org"
    assert "BudgetRequestPermission: cannot resolve organization for budget_request_id=42" in caplog.text


def test_approval_permission_logs_failed_organization_lookup(caplog):
    with patch("budget_approval.permissions.user_may_process_budget_approval", return_value=True), patch(
        "budget_approval.permissions.user_is_org_admin_for_budget_request", return_value=False
    ), patch("budget_approval.permissions.user_has_team", return_value=False), patch(
        "budget_approval.permissions.has_rbac_permission", return_value=True
    ) as rbac, caplog.at_level(logging.WARNING, logger=LOGGER):
        allowed = ApprovalPermission().has_object_permission(_request("PATCH"), MagicMock(), _BudgetRequest())

    assert allowed is True
    assert rbac.call_args.args[3] == "fallback-org"
    assert "ApprovalPermission: cannot resolve organization for budget_request_id=42" in caplog.text
