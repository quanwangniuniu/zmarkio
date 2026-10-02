"""
Swallowed exceptions in chat presence, forwarding and scheduled messages must
leave a log record without changing behaviour (MED-401).
"""
import io
import logging
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from django.db import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError

from chat.models import ScheduledMessage
from chat.services import ChatService, MessageService, OnlineStatusService
from chat.tasks import send_scheduled_message


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


def test_touch_cache_key_failure_is_logged_at_debug(caplog):
    with patch("chat.services.cache.touch", side_effect=RedisConnectionError("down")), caplog.at_level(
        logging.DEBUG, logger="chat.services"
    ):
        OnlineStatusService._touch_cache_key("user_online:7")

    assert _swallowed_logs(caplog, "chat.services", logging.DEBUG)


def test_presence_recipients_cache_write_failure_still_returns_db_result(caplog):
    participants = MagicMock()
    participants.filter.return_value.exclude.return_value.values_list.return_value.distinct.return_value = [3, 4]

    with patch("chat.services.ChatParticipant.objects", participants), patch(
        "chat.services.cache.get", return_value=None
    ), patch("chat.services.cache.set", side_effect=RedisConnectionError("down")), caplog.at_level(
        logging.DEBUG, logger="chat.services"
    ):
        recipients = ChatService.get_presence_recipient_ids(7)

    assert recipients == [3, 4]
    assert _swallowed_logs(caplog, "chat.services", logging.DEBUG)


def test_forward_source_close_failure_is_logged_and_copy_succeeds(caplog):
    source = MagicMock()
    source.name = "chat/attachments/report.pdf"
    source.file = io.BytesIO(b"pdf")
    source.close.side_effect = OSError("EIO")
    target = MagicMock()

    with caplog.at_level(logging.WARNING, logger="chat.services"):
        MessageService._copy_file_field_for_forward(
            source_field=source, target_field=target, fallback_filename="report.pdf"
        )

    target.save.assert_called_once()
    assert _swallowed_logs(caplog, "chat.services", logging.WARNING)


def test_scheduled_message_failed_status_write_failure_is_logged(caplog):
    scheduled = MagicMock(status=ScheduledMessage.STATUS_PENDING)
    # First save marks SENDING; the second (marking FAILED) hits the outage.
    scheduled.save.side_effect = [None, OperationalError("connection lost")]
    scheduled_manager = MagicMock()
    scheduled_manager.select_related.return_value.get.return_value = scheduled

    with patch("chat.tasks.tenant_schema_context", lambda _schema: nullcontext()), patch(
        "chat.tasks.transaction.atomic", nullcontext
    ), patch("chat.models.ScheduledMessage.objects", scheduled_manager), patch(
        "chat.models.Message.objects.create", side_effect=ValueError("attachments are no longer available")
    ), caplog.at_level(logging.ERROR, logger="chat.tasks"):
        send_scheduled_message(5)

    # The task already logged the original failure without a traceback; the new
    # record is the one carrying the swallowed status-update error.
    assert _swallowed_logs(caplog, "chat.tasks", logging.ERROR)
