"""
The generic virus scanner's follow-up status update can fail and leave the
file in SCANNING, so it must be logged (MED-401).
"""
import logging
from unittest.mock import MagicMock

from django.db import OperationalError

from utils.virus_scanner import scan_file_generic


class FakeCreative:
    class DoesNotExist(Exception):
        pass

    objects = MagicMock()


def test_status_update_failure_after_scan_error_is_logged(caplog):
    FakeCreative.objects.get.side_effect = [RuntimeError("ClamAV unavailable"), OperationalError("connection lost")]

    with caplog.at_level(logging.ERROR, logger="utils.virus_scanner"):
        result = scan_file_generic("/tmp/missing.mp4", FakeCreative, 9)

    assert result is False
    assert "Failed to mark FakeCreative 9 as error_scanning; it may stay in SCANNING" in caplog.text
