"""
SSE Redis teardown failures are swallowed by design but now leave a debug
record, and the client is always closed (MED-401).
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from redis.exceptions import ConnectionError as RedisConnectionError

from notifications.sse import publish_notification_to_redis, sse_event_generator


def _swallowed_logs(caplog, logger, level):
    """Records from `logger` at `level` that carry the swallowed exception (MED-401)."""
    return [r for r in caplog.records if r.name == logger and r.levelno == level and r.exc_info]


LOGGER = "notifications.sse"


def test_publish_client_close_failure_is_logged_at_debug(caplog):
    client = MagicMock()
    client.close.side_effect = RuntimeError("close failed")
    serializer = MagicMock()
    serializer.return_value.data = {"id": "n-1"}

    with patch("redis.Redis", return_value=client), patch(
        "notifications.serializers.NotificationSerializer", serializer
    ), caplog.at_level(logging.DEBUG, logger=LOGGER):
        publish_notification_to_redis(7, MagicMock(pk="n-1"))

    client.publish.assert_called_once()
    assert _swallowed_logs(caplog, LOGGER, logging.DEBUG)


def test_stream_teardown_closes_client_even_if_unsubscribe_fails(caplog):
    pubsub = AsyncMock()
    pubsub.get_message.side_effect = asyncio.CancelledError
    pubsub.unsubscribe.side_effect = RedisConnectionError("redis restarted")
    client = AsyncMock()
    client.pubsub = MagicMock(return_value=pubsub)

    async def consume():
        async for _ in sse_event_generator(7, None):
            pass

    with patch("redis.asyncio.from_url", return_value=client), patch(
        "notifications.sse.connections"
    ), caplog.at_level(logging.DEBUG, logger=LOGGER):
        asyncio.run(consume())

    client.aclose.assert_awaited_once()
    assert _swallowed_logs(caplog, LOGGER, logging.DEBUG)
