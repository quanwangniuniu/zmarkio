"""
Artifact title lookup falls back to a generic label. A missing artifact is
expected and stays silent; any other failure is now logged (MED-401).
"""
import logging
from unittest.mock import patch

from django.db import OperationalError

from decision.models import Decision
from meetings.views import ArtifactLinkViewSet


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


LOGGER = "meetings.views"


def test_missing_artifact_uses_generic_title_without_warning(caplog):
    with patch("decision.models.Decision.objects.only") as only, caplog.at_level(logging.WARNING, logger=LOGGER):
        only.return_value.get.side_effect = Decision.DoesNotExist
        title = ArtifactLinkViewSet._resolve_artifact_title("decision", 999)

    assert title == "Decision #999"
    assert caplog.records == []


def test_unexpected_lookup_failure_uses_generic_title_and_is_logged(caplog):
    with patch("decision.models.Decision.objects.only") as only, caplog.at_level(logging.WARNING, logger=LOGGER):
        only.return_value.get.side_effect = OperationalError("connection lost")
        title = ArtifactLinkViewSet._resolve_artifact_title("decision", 999)

    assert title == "Decision #999"
    assert _swallowed_logs(caplog, LOGGER, logging.WARNING)
