"""
Swallowed exceptions in the tenant middleware cleanup and project deletion
must leave a log record without changing behaviour (MED-401).
"""
import logging
from unittest.mock import MagicMock, patch

import pytest
from django.db import InterfaceError, OperationalError
from django.db.backends.utils import CursorWrapper
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from rest_framework import status

from core.middleware.tenant_schema import TenantSchemaMiddleware


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


class _FakeCursor:
    """Accepts the initial SET search_path, fails the reset in `finally`."""

    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.calls.append(query)
        if len(self.calls) == 2:
            raise OperationalError("current transaction is aborted")


def test_failed_search_path_reset_is_logged_and_response_kept(caplog):
    calls = []
    fake_connection = MagicMock()
    fake_connection.cursor.side_effect = lambda: _FakeCursor(calls)
    fake_connection.rollback.side_effect = InterfaceError("connection already closed")
    response = HttpResponse("ok")
    middleware = TenantSchemaMiddleware(lambda request: response)

    with patch("core.middleware.tenant_schema.connection", fake_connection), patch.object(
        TenantSchemaMiddleware, "_authenticate_jwt"
    ), patch.object(TenantSchemaMiddleware, "_resolve_schema", return_value="public"), caplog.at_level(
        logging.DEBUG, logger="core.middleware.tenant_schema"
    ):
        result = middleware(RequestFactory().get("/api/core/projects/"))

    assert result is response
    # One record for the failed reset, one for the failed rollback.
    assert len(_swallowed_logs(caplog, "core.middleware.tenant_schema", logging.DEBUG)) == 2


@pytest.mark.django_db
def test_calendar_lookup_failure_is_logged_and_project_still_deleted(authenticated_client, project, caplog):
    original_execute = CursorWrapper.execute

    def execute(cursor, sql, params=None):
        if isinstance(sql, str) and sql.startswith("SELECT id FROM calendars_calendar WHERE project_id"):
            raise OperationalError("connection lost")
        return original_execute(cursor, sql, params)

    with patch.object(CursorWrapper, "execute", execute), caplog.at_level(logging.WARNING, logger="core.views"):
        response = authenticated_client.delete(reverse("project-detail", kwargs={"pk": project.slug}))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert _swallowed_logs(caplog, "core.views", logging.WARNING)
