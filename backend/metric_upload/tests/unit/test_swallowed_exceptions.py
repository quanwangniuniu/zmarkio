"""
A failed metric file scan, and a failed follow-up status update that can
leave the file in SCANNING, must both be logged (MED-401).
"""
import logging
from unittest.mock import patch

from django.db import OperationalError

from metric_upload.tasks import scan_file_for_virus


def test_scan_failure_and_status_update_failure_are_logged(caplog):
    with patch(
        "metric_upload.tasks.MetricFile.objects.get",
        side_effect=[RuntimeError("ClamAV unavailable"), OperationalError("connection lost")],
    ), caplog.at_level(logging.ERROR, logger="metric_upload.tasks"):
        result = scan_file_for_virus(9)

    assert result is False
    assert "Virus scan failed for MetricFile 9" in caplog.text
    assert "Failed to mark MetricFile 9 as error_scanning; it may stay in SCANNING" in caplog.text
