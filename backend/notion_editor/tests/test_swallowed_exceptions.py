"""
A temp file that cannot be removed on the media upload error path is now
logged, and the JSON 500 response is unchanged (MED-401).
"""
import logging
import os
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from notion_editor.views import MediaUploadView


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


def test_temp_file_cleanup_failure_is_logged_and_error_response_kept(caplog):
    request = APIRequestFactory().post(
        "/api/notion/api/media/upload/",
        {"file": SimpleUploadedFile("note.txt", b"hello", content_type="text/plain"), "media_type": "file"},
        format="multipart",
    )
    force_authenticate(request, user=MagicMock(is_authenticated=True))
    real_unlink = os.unlink

    with patch("notion_editor.views.perform_clamav_scan", side_effect=RuntimeError("ClamAV unavailable")), patch(
        "notion_editor.views.os.unlink", side_effect=OSError("read-only /tmp")
    ), caplog.at_level(logging.WARNING, logger="notion_editor.views"):
        response = MediaUploadView.as_view()(request)

    leaked = _swallowed_logs(caplog, "notion_editor.views", logging.WARNING)
    for record in leaked:
        real_unlink(record.args[0])

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert leaked
