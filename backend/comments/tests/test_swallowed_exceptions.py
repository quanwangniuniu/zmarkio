"""
Comment attachment preview and storage cleanup failures must be logged
without changing behaviour (MED-401).
"""
import logging
from unittest.mock import MagicMock, patch

from comments.preview_generation import _get_image_preview_page
from comments.services import _delete_storage_path


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


def test_unreadable_image_dimensions_are_logged_and_page_still_returned(caplog):
    attachment = MagicMock(id=11, content_type="image/svg+xml")
    attachment.file.url = "/media/comments/logo.svg"
    attachment.file.open.side_effect = OSError("cannot identify image file")

    with caplog.at_level(logging.WARNING, logger="comments.preview_generation"):
        page = _get_image_preview_page(attachment)

    assert page == {"page": 1, "image_url": "/media/comments/logo.svg"}
    # Logged without a traceback on purpose: unreadable formats such as SVG are expected.
    assert [r for r in caplog.records if r.name == "comments.preview_generation" and r.levelno == logging.WARNING]


def test_storage_cleanup_failure_is_logged_with_orphaned_path(caplog):
    with patch("comments.services.default_storage.exists", return_value=True), patch(
        "comments.services.default_storage.delete", side_effect=PermissionError("read-only volume")
    ), caplog.at_level(logging.ERROR, logger="comments.services"):
        _delete_storage_path("comments/attachments/a.pdf")

    assert _swallowed_logs(caplog, "comments.services", logging.ERROR)
