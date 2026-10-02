"""
A failed metric file scan, and a failed follow-up status update that can
leave the file in SCANNING, must both be logged (MED-401).
"""
import logging
from unittest.mock import patch

from django.db import OperationalError

from metric_upload.tasks import scan_file_for_virus


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


def test_scan_failure_and_status_update_failure_are_logged(caplog):
    with patch(
        "metric_upload.tasks.MetricFile.objects.get",
        side_effect=[RuntimeError("ClamAV unavailable"), OperationalError("connection lost")],
    ), caplog.at_level(logging.ERROR, logger="metric_upload.tasks"):
        result = scan_file_for_virus(9)

    assert result is False
    # One record for the scan failure, one for the failed status update.
    assert len(_swallowed_logs(caplog, "metric_upload.tasks", logging.ERROR)) == 2
