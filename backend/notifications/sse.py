"""
SSE (Server-Sent Events) support for the notifications app.

Architecture
------------
* When create_notification() saves a new Notification, it calls
  publish_notification_to_redis() which POSTs the payload to a per-user
  Redis Pub/Sub channel: ``user_{user_id}_events``.

* The HTTP endpoint GET /api/notifications/stream/ opens an SSE stream.
  The underlying async generator subscribes to that Redis channel and
  forwards every published message to the browser as an SSE event.

* Heartbeat comments ("": heartbeat) are emitted every HEARTBEAT_INTERVAL
  seconds so that Nginx's proxy_read_timeout doesn't kill the connection.

* Last-Event-ID reconnect: if the browser reconnects with a Last-Event-ID
  header (set to the UUID of the last received Notification), we replay later
  notifications from the database in bounded batches before resuming live
  streaming. The event timestamp is a fallback if that UUID has been deleted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.db import connections
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from prometheus_client import Counter, Gauge
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

sse_connection_drops_total = Counter(
    "sse_connection_drops_total",
    "Total number of dropped SSE connections",
)

sse_active_connections = Gauge(
    "sse_active_connections",
    "Number of currently active SSE connections",
)
HEARTBEAT_INTERVAL = 25  # seconds – below Nginx proxy_read_timeout (60 s typical)
_REDIS_DB = 0  # same DB as Celery broker; Pub/Sub is namespace-isolated by channel name

_REPLAY_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local cutoff_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl_ms = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
    redis.call('PEXPIRE', key, ttl_ms)
    return 0
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, ttl_ms)
return 1
"""


# ── helpers ──────────────────────────────────────────────────────────────────

def _redis_host_port() -> tuple[str, int]:
    """
    Derive Redis host/port from CHANNEL_LAYERS (the authoritative source for
    the Redis container address in this project).  Falls back to 'redis:6379'
    if the config is missing or malformed.
    """
    try:
        hosts = settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"]
        host, port = hosts[0]
        return str(host), int(port)
    except Exception:
        return "redis", 6379


def _parse_replay_timestamp(value: str | None):
    if not value:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _serialize_missed_notifications(
    user_id: int,
    last_event_id: str,
    last_event_created_at: str | None = None,
):
    """Return one bounded replay batch and whether another batch remains."""
    from .models import Notification  # noqa: PLC0415
    from .serializers import NotificationSerializer  # noqa: PLC0415

    try:
        cursor_id = uuid.UUID(str(last_event_id))
    except (TypeError, ValueError, AttributeError):
        cursor_id = None

    cursor = None
    if cursor_id is not None:
        cursor = (
            Notification.objects.filter(recipient_id=user_id, pk=cursor_id)
            .only("id", "created_at")
            .first()
        )

    qs = Notification.objects.filter(recipient_id=user_id)
    if cursor is not None:
        qs = qs.filter(
            Q(created_at__gt=cursor.created_at)
            | Q(created_at=cursor.created_at, id__gt=cursor.id)
        )
    else:
        fallback_created_at = _parse_replay_timestamp(last_event_created_at)
        if fallback_created_at is None:
            return [], False

        # The UUID cursor may have been deleted. Replay the timestamp boundary
        # inclusively so an equal-timestamp event is never silently skipped;
        # the browser suppresses any already-seen UUIDs.
        qs = qs.filter(created_at__gte=fallback_created_at)
        if cursor_id is not None:
            qs = qs.exclude(pk=cursor_id)

    batch_size = max(
        1,
        int(getattr(settings, "NOTIFICATION_SSE_REPLAY_BATCH_SIZE", 500)),
    )
    rows = list(qs.order_by("created_at", "id")[: batch_size + 1])
    has_more = len(rows) > batch_size
    serialized = list(NotificationSerializer(rows[:batch_size], many=True).data)
    return serialized, has_more


def _allow_replay_attempt(user_id: int) -> bool:
    """Apply a Redis-backed rolling-window replay limit per authenticated user."""
    from django_redis import get_redis_connection  # noqa: PLC0415

    limit = max(
        1,
        int(getattr(settings, "NOTIFICATION_SSE_REPLAY_RATE_LIMIT", 5)),
    )
    window_seconds = max(
        1,
        int(getattr(settings, "NOTIFICATION_SSE_REPLAY_RATE_WINDOW_SECONDS", 30)),
    )
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    key = f"mediajira:sse:replay-rate:{user_id}"
    member = f"{now_ms}:{uuid.uuid4().hex}"

    try:
        redis_client = get_redis_connection("default")
        allowed = redis_client.eval(
            _REPLAY_RATE_LIMIT_SCRIPT,
            1,
            key,
            now_ms,
            now_ms - window_ms,
            limit,
            member,
            window_ms,
        )
        return bool(allowed)
    except Exception:
        # Availability wins over throttling if the cache is temporarily down.
        logger.warning(
            "SSE: replay rate limiter unavailable for user_id=%s",
            user_id,
            exc_info=True,
        )
        return True


# ── sync publisher (called from create_notification) ─────────────────────────

def publish_notification_to_redis(user_id: int, notification) -> None:
    """
    Publish a serialized Notification to the per-user Redis Pub/Sub channel.

    This is a *synchronous* function intentionally: it is called from the
    synchronous ``create_notification()`` service inside a DB transaction.
    A short-lived Redis connection is acceptable here because notifications
    are sparse relative to the connection overhead.
    """
    import redis as sync_redis  # noqa: PLC0415

    from .serializers import NotificationSerializer  # noqa: PLC0415

    r: sync_redis.Redis | None = None
    try:
        host, port = _redis_host_port()
        r = sync_redis.Redis(
            host=host,
            port=port,
            db=_REDIS_DB,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        channel = f"user_{user_id}_events"
        payload = json.dumps(
            {"type": "notification", "data": NotificationSerializer(notification).data},
            default=str,
        )
        r.publish(channel, payload)
        logger.debug("SSE: published to channel=%s notification_id=%s", channel, notification.pk)
    except Exception:
        logger.exception(
            "SSE: Redis publish failed – user_id=%s notification_id=%s",
            user_id,
            getattr(notification, "pk", None),
        )
    finally:
        if r is not None:
            try:
                r.close()
            except (RedisError, OSError):
                # Teardown of a short-lived client; the publish outcome is already
                # logged above. This function runs from on_commit hooks and inside
                # create_or_update_chat_notification's atomic block, so it must not raise.
                pass


# ── async SSE generator ───────────────────────────────────────────────────────

async def sse_event_generator(
    user_id: int,
    last_event_id: str | None,
    last_event_created_at: str | None = None,
):
    """
    Async generator that yields SSE-formatted byte strings.

    Lifecycle
    ---------
    1. Subscribe to Redis channel ``user_{user_id}_events`` so notifications
       created during replay are queued rather than lost.
    2. If *last_event_id* is given (the UUID of the last event the browser
       received), replay one bounded batch of later Notifications from the DB.
    3. Forward every published JSON payload as an SSE ``data:`` line, using
       the Notification's UUID as the event id.
    4. Emit a comment heartbeat every HEARTBEAT_INTERVAL seconds.
    5. On disconnect (CancelledError) or error: unsubscribe and close Redis.
    """
    import redis.asyncio as aioredis  # noqa: PLC0415
    from asgiref.sync import sync_to_async  # noqa: PLC0415

    # Subscribe before replaying so an event created between the database
    # query and subscription cannot fall through the gap.
    host, port = _redis_host_port()
    redis_url = f"redis://{host}:{port}/{_REDIS_DB}"
    r = aioredis.from_url(redis_url, decode_responses=True)
    pubsub = r.pubsub()
    channel = f"user_{user_id}_events"
    subscribed = False
    active_connection_counted = False
    replayed_ids: set[str] = set()
    try:
        await pubsub.subscribe(channel)
        subscribed = True
        sse_active_connections.inc()
        active_connection_counted = True
        logger.info("SSE: user_id=%s subscribed to channel=%s", user_id, channel)

        if last_event_id:
            try:
                missed, has_more = await sync_to_async(
                    _serialize_missed_notifications
                )(
                    user_id,
                    last_event_id,
                    last_event_created_at,
                )
                for item in missed:
                    event_id = str(item.get("id", ""))
                    if not event_id:
                        continue
                    replayed_ids.add(event_id)
                    payload = json.dumps(
                        {"type": "notification", "data": dict(item)},
                        default=str,
                    )
                    yield f"id: {event_id}\ndata: {payload}\n\n"

                logger.debug(
                    "SSE: replayed %d notification(s) for user_id=%s after id=%s has_more=%s",
                    len(missed),
                    user_id,
                    last_event_id,
                    has_more,
                )

                if has_more:
                    # End this bounded batch. EventSource reconnects with the
                    # last UUID it received and continues with the next batch.
                    return
            except Exception:
                logger.exception(
                    "SSE: replay failed for user_id=%s last_event_id=%s",
                    user_id,
                    last_event_id,
                )

        # Release the request's database connection before going long-lived.
        await sync_to_async(connections.close_all)()
        last_heartbeat = time.monotonic()

        while True:
            # Block up to 1 s waiting for the next message, then fall through
            # to the heartbeat check so we never stall longer than ~1 s.
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )

            if message and message.get("type") == "message":
                try:
                    raw = message["data"]
                    parsed = json.loads(raw)
                    event_id = str(parsed.get("data", {}).get("id", ""))
                    if not event_id or event_id in replayed_ids:
                        continue
                    sse_line = f"id: {event_id}\ndata: {raw}\n\n"
                    yield sse_line
                except Exception:
                    logger.exception(
                        "SSE: failed to forward message for user_id=%s", user_id
                    )

            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                # SSE comment – browsers ignore it, but it keeps the TCP
                # connection alive through Nginx's proxy_read_timeout.
                yield ": heartbeat\n\n"
                last_heartbeat = now

    except asyncio.CancelledError:
        # Normal path: client closed the tab / navigated away.
        sse_connection_drops_total.inc()
        logger.info("SSE: stream cancelled for user_id=%s", user_id)
    except Exception:
        sse_connection_drops_total.inc()
        logger.exception("SSE: generator error for user_id=%s", user_id)
    finally:
        if active_connection_counted:
            sse_active_connections.dec()
        # Teardown: the stream is already over and any real error was logged above.
        # r.aclose() sits in its own finally so it still runs (and closes the pubsub
        # connection with the pool) if unsubscribe raises or is cancelled.
        # RuntimeError covers "Event loop is closed" when the server shuts down.
        try:
            if subscribed:
                await pubsub.unsubscribe(channel)
        except (RedisError, OSError, RuntimeError):
            pass
        finally:
            try:
                await r.aclose()
            except (RedisError, OSError, RuntimeError):
                pass
        logger.info("SSE: connection closed for user_id=%s", user_id)
