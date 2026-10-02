"""
Swallowed exceptions in the authorization middleware must leave a log record
(MED-401). Each test forces the guarded call to fail and checks that the
request still behaves as before and that the failure is logged.
"""
import logging
from unittest.mock import MagicMock, patch

from django.db import DataError, OperationalError
from django.test import RequestFactory

from access_control.middleware.authorization import AuthorizationMiddleware

LOGGER = "access_control.middleware.authorization"


def _user():
    return MagicMock(is_authenticated=True, is_superuser=False, id=7)


def test_org_admin_lookup_failure_is_logged_and_falls_back(caplog):
    request = RequestFactory().get("/api/auth/me/")
    request.user = _user()

    with patch(
        "access_control.middleware.authorization.UserRole.objects.filter",
        side_effect=OperationalError("connection lost"),
    ), caplog.at_level(logging.WARNING, logger=LOGGER):
        result = AuthorizationMiddleware().process_view(request, None, (), {})

    assert result is None
    assert request.is_org_admin is False
    assert "Org-admin bypass lookup failed for user_id=7" in caplog.text


def test_admin_override_audit_write_failure_is_logged(caplog):
    request = RequestFactory().get("/api/assets/1/")
    middleware = AuthorizationMiddleware()

    with patch.object(middleware, "_resolve_org_id_from_search_path", return_value=None), patch(
        "access_control.middleware.authorization.AdminOverrideAudit.objects.create",
        side_effect=DataError("value too long"),
    ), caplog.at_level(logging.ERROR, logger=LOGGER):
        middleware._log_override(request, _user(), "SUPERUSER", "ASSET", "VIEW")

    assert "Failed to write AdminOverrideAudit user_id=7 type=SUPERUSER" in caplog.text
