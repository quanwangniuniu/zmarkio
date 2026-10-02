"""
TikTok upload and delete paths swallow storage cleanup errors; those errors
are now logged and the responses are unchanged (MED-401).
"""
import json
import logging
import os
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from tiktok.views import material_delete, upload_video_ad


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


LOGGER = "tiktok.views"


def _authenticated(request):
    force_authenticate(request, user=MagicMock(is_authenticated=True))
    return request


def test_temp_file_cleanup_failure_is_logged_and_response_kept(caplog):
    request = _authenticated(APIRequestFactory().post(
        "/api/tiktok/file/video/ad/upload/",
        {"file": SimpleUploadedFile("clip.mp4", b"not-really-a-video", content_type="video/mp4"), "name": "clip"},
        format="multipart",
    ))
    no_video_track = MagicMock(stdout=json.dumps({"media": {"track": [{"@type": "General"}]}}))
    real_unlink = os.unlink

    with patch("tiktok.views.TikTokCreative.objects.filter") as creatives, patch(
        "tiktok.views.default_storage.save", return_value="tiktok/videos/clip.mp4"
    ), patch("tiktok.views.subprocess.run", return_value=no_video_track), patch(
        "tiktok.views.os.unlink", side_effect=OSError("read-only /tmp")
    ), caplog.at_level(logging.WARNING, logger=LOGGER):
        creatives.return_value.first.return_value = None
        response = upload_video_ad(request)

    leaked = _swallowed_logs(caplog, LOGGER, logging.WARNING)
    for record in leaked:
        real_unlink(record.args[0])

    # The in-flight 400 from inside the try survives the failing cleanup in `finally`.
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data == {"error": "No video stream found in file"}
    assert leaked


def test_stored_file_delete_failure_is_logged_and_material_still_deleted(caplog):
    request = _authenticated(APIRequestFactory().delete("/api/tiktok/material/delete/5/"))
    creative = MagicMock(id=5, storage_path="tiktok/videos/abc.mp4")

    with patch("tiktok.views.TikTokCreative.objects.get", return_value=creative), patch(
        "tiktok.views.default_storage.delete", side_effect=PermissionError("read-only volume")
    ), caplog.at_level(logging.ERROR, logger=LOGGER):
        response = material_delete(request, id=5)

    assert response.status_code == status.HTTP_200_OK
    creative.delete.assert_called_once()
    assert _swallowed_logs(caplog, LOGGER, logging.ERROR)
