"""
A failed follow-up status update after a task attachment scan error can
leave the attachment in SCANNING, so it must be logged (MED-401).
"""
import logging
from unittest.mock import patch

from django.db import OperationalError

from task.tasks import scan_task_attachment


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


def test_status_update_failure_after_scan_error_is_logged(caplog):
    # The "task" logger does not propagate to root (settings.LOGGING), so attach
    # caplog's handler to it directly.
    task_logger = logging.getLogger("task")
    task_logger.addHandler(caplog.handler)
    try:
        with patch(
            "task.tasks.TaskAttachment.objects.get",
            side_effect=[RuntimeError("ClamAV unavailable"), OperationalError("connection lost")],
        ):
            result = scan_task_attachment(9)
    finally:
        task_logger.removeHandler(caplog.handler)

    assert result is False
    assert _swallowed_logs(caplog, "task.tasks", logging.ERROR)
