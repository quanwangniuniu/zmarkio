"""
The generic virus scanner's follow-up status update can fail and leave the
file in SCANNING, so it must be logged (MED-401).
"""
import logging
from unittest.mock import MagicMock

from django.db import OperationalError

from utils.virus_scanner import scan_file_generic


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


class FakeCreative:
    class DoesNotExist(Exception):
        pass

    objects = MagicMock()


def test_status_update_failure_after_scan_error_is_logged(caplog):
    FakeCreative.objects.get.side_effect = [RuntimeError("ClamAV unavailable"), OperationalError("connection lost")]

    with caplog.at_level(logging.ERROR, logger="utils.virus_scanner"):
        result = scan_file_generic("/tmp/missing.mp4", FakeCreative, 9)

    assert result is False
    assert _swallowed_logs(caplog, "utils.virus_scanner", logging.ERROR)
