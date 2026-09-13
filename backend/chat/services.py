import ipaddress
import logging
import os
import re
import socket
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from urllib3.util import connection as urllib3_connection
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Prefetch, Max
from django.core.cache import cache
from django.utils import timezone
from django_redis import get_redis_connection
from .models import Chat, ChatOutboxEvent, ChatParticipant, ChatStar, LinkPreview, Message, MessageAttachment, MessageMention, MessageStatus, ChatType, ChannelVisibility, ThreadReadStatus
from core.models import ProjectMember
from core.tenant_context import current_tenant_schema
from .realtime import broadcast_event_to_user_groups_sync

User = get_user_model()


class _OutboxAckSerializerRequest:
    """Minimal DRF-request stand-in for serializing messages inside a WebSocket
    consumer, where no HTTP request exists.

    MessageSerializer reads ``context['request'].user`` for per-user fields
    (e.g. status, can_revoke) and calls ``request.build_absolute_uri`` for file
    URLs. We expose the authenticated viewer and return URLs unchanged so the
    client receives the same relative paths it already uses against its origin.
    """

    def __init__(self, user):
        self.user = user

    def build_absolute_uri(self, url):
        return url


logger = logging.getLogger(__name__)

ALLOWED_ATTACHMENT_IMAGE_MIME_PREFIX = "image/"
ALLOWED_ATTACHMENT_MIME_TYPES = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
})

class UnsupportedAttachmentMimeType(ValueError):
    """Raised when an uploaded attachment MIME type is not allowed."""


def is_allowed_attachment_mime_type(mime_type: str) -> bool:
    normalized_mime_type = (mime_type or "").strip().lower()
    return (
        normalized_mime_type.startswith(ALLOWED_ATTACHMENT_IMAGE_MIME_PREFIX)
        or normalized_mime_type in ALLOWED_ATTACHMENT_MIME_TYPES
    )


def validate_attachment_mime_type(mime_type: str) -> None:
    """
    Raise UnsupportedAttachmentMimeType if MIME type is not allowed.
    """
    if not is_allowed_attachment_mime_type(mime_type):
        raise UnsupportedAttachmentMimeType(
            f'Unsupported MIME type: {mime_type or "<empty>"}'
        )


# --- Link preview URL safety (MED-279) ---------------------------------------
# A chat message can contain any URL, and the server fetches it to build a preview.
# That makes this an SSRF surface: an attacker can point us at an address our
# server can reach but they cannot (cloud metadata, internal admin pages).
# Safety is therefore decided by the *resolved IP*, never by matching the URL
# string — DNS can point an ordinary-looking domain at 127.0.0.1.
ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
LINK_PREVIEW_TIMEOUT_SECONDS = 5
LINK_PREVIEW_MAX_BYTES = 1024 * 1024      # 1 MiB — plenty to reach <head>
LINK_PREVIEW_MAX_REDIRECTS = 3
LINK_PREVIEW_HTML_CONTENT_TYPE = "text/html"
LINK_PREVIEW_USER_AGENT = "MediaJira-LinkPreview/1.0"
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


class UnsafeUrlError(ValueError):
    """Raised when a URL must not be fetched by the server (SSRF guard)."""


class LinkPreviewFetchError(ValueError):
    """Raised when a safe URL could not be fetched (upstream error)."""


def extract_first_url(content: Optional[str]) -> Optional[str]:
    """First http(s) URL in a message body, or None. Only the first gets a preview."""
    if not content:
        return None
    match = _URL_RE.search(content)
    return match.group(0) if match else None


def normalize_preview_url(url: Optional[str]) -> str:
    """Canonical cache key for a URL: fragment dropped, host lowercased.

    The path stays case-sensitive on purpose — only the host is case-insensitive.
    Normalizing means the same link written slightly differently is fetched once.
    """
    parsed = urlparse((url or "").strip())
    return urlunparse(parsed._replace(netloc=parsed.netloc.lower(), fragment=""))


def _is_public_ip(ip: Any) -> bool:
    """True only for globally routable addresses."""
    # An IPv4-mapped IPv6 address (::ffff:127.0.0.1) must be judged by its IPv4 form.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_host_is_public(host: str) -> str:
    """Resolve a host, reject it if ANY answer is not public, and return one address.

    Checking every answer matters: multi-record DNS must not let one public
    address launder a private one.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Cannot resolve host: {host}") from exc

    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise UnsafeUrlError(f"Host resolved to no address: {host}")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%")[0])  # drop IPv6 zone id
        except ValueError as exc:
            raise UnsafeUrlError(f"Unparseable address for host {host}: {address}") from exc
        if not _is_public_ip(ip):
            raise UnsafeUrlError(f"URL resolves to a non-public address: {address}")

    # Every answer passed, so any of them is safe to pin; take the first.
    return addresses[0].split("%")[0]


def resolve_public_url(url: Optional[str]) -> Tuple[str, str]:
    """Validate a URL and return (normalized url, the address that passed).

    Returning the address matters: validating a hostname and then letting the HTTP
    client resolve it again leaves a TOCTOU window, because DNS can answer publicly
    for the check and privately for the connection. The caller pins this address.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        raise UnsafeUrlError(f'Unsupported URL scheme: {parsed.scheme or "<none>"}')
    if not parsed.hostname:
        raise UnsafeUrlError("URL has no host")
    address = _assert_host_is_public(parsed.hostname)
    return normalize_preview_url(url), address


def validate_public_url(url: Optional[str]) -> str:
    """Raise UnsafeUrlError unless the server may fetch this URL; return its cache key."""
    return resolve_public_url(url)[0]


def _read_capped_body(response: Any) -> str:
    """Read at most LINK_PREVIEW_MAX_BYTES so a huge page cannot exhaust memory."""
    chunks: List[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        remaining = LINK_PREVIEW_MAX_BYTES - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += len(chunk[:remaining])
    return b"".join(chunks).decode("utf-8", errors="replace")


_PINS = threading.local()


@contextmanager
def _pinned_address(host: str, address: str):
    """Force connections to `host` to go to `address` for the duration of the block.

    urllib3 funnels every connection through `create_connection`, which is patched
    once at import to consult this thread-local map first. Thread-local because the
    Celery workers here run a thread pool — a module-level dict would let one
    thread's pin leak into another's request.
    """
    pins = getattr(_PINS, "map", None)
    if pins is None:
        pins = _PINS.map = {}
    previous = pins.get(host)
    pins[host] = address
    try:
        yield
    finally:
        if previous is None:
            pins.pop(host, None)
        else:
            pins[host] = previous


def _create_connection_with_pin(address, *args, **kwargs):
    """urllib3 connection factory that honours a pinned address."""
    host, port = address
    pinned = getattr(_PINS, "map", {}).get(host)
    if pinned:
        address = (pinned, port)
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


# Patched once, globally, but inert unless the calling thread has a pin set. TLS
# still verifies against the hostname because only the address is substituted.
_ORIGINAL_CREATE_CONNECTION = urllib3_connection.create_connection
urllib3_connection.create_connection = _create_connection_with_pin


def fetch_url_safely(url: str) -> str:
    """Fetch a URL's HTML under the SSRF guard, re-validating every redirect hop.

    Redirects are followed manually — a public URL is allowed to 302 towards an
    internal address, so the HTTP client must never follow them for us. Each hop
    connects to the address that just passed validation rather than resolving the
    hostname again, which is what closes the DNS-rebinding window.
    """
    current, address = resolve_public_url(url)

    for _ in range(LINK_PREVIEW_MAX_REDIRECTS + 1):
        host = urlparse(current).hostname
        with _pinned_address(host, address):
            response = requests.get(
                current,
                timeout=LINK_PREVIEW_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": LINK_PREVIEW_USER_AGENT},
            )
        try:
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("Location")
                if not location:
                    raise UnsafeUrlError("Redirect without a Location header")
                current, address = resolve_public_url(urljoin(current, location))
                continue

            if response.status_code >= 400:
                raise LinkPreviewFetchError(f"Upstream returned {response.status_code}")

            content_type = (response.headers.get("Content-Type") or "").lower()
            if not content_type.startswith(LINK_PREVIEW_HTML_CONTENT_TYPE):
                raise UnsafeUrlError(f"Refusing non-HTML content type: {content_type or '<none>'}")

            return _read_capped_body(response)
        finally:
            response.close()

    raise UnsafeUrlError(f"Too many redirects (max {LINK_PREVIEW_MAX_REDIRECTS})")


# Magic bytes for the formats we are willing to re-serve. The declared
# Content-Type is the remote host's claim; these are the file's own testimony, and
# a proxy that trusted the claim would be a new hole rather than a fix for one.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
LINK_PREVIEW_IMAGE_MAX_BYTES = 5 * 1024 * 1024


def _sniff_image_type(data: bytes) -> Optional[str]:
    """The real type of these bytes, or None if they are not an image we allow."""
    for signature, content_type in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return content_type
    # WebP is RIFF....WEBP — the marker sits at offset 8, not at the start.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def fetch_image_safely(url: str) -> Tuple[bytes, str]:
    """Fetch a thumbnail under the same guard as a page, and verify it really is one.

    Serving remote images from our own domain is what stops every viewer's browser
    contacting the third-party host. It also means we vouch for the bytes, so the
    content type is decided by sniffing rather than by what the host claims.
    """
    current, address = resolve_public_url(url)

    for _ in range(LINK_PREVIEW_MAX_REDIRECTS + 1):
        host = urlparse(current).hostname
        with _pinned_address(host, address):
            response = requests.get(
                current,
                timeout=LINK_PREVIEW_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
                headers={"User-Agent": LINK_PREVIEW_USER_AGENT},
            )
        try:
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("Location")
                if not location:
                    raise UnsafeUrlError("Redirect without a Location header")
                current, address = resolve_public_url(urljoin(current, location))
                continue

            if response.status_code >= 400:
                raise LinkPreviewFetchError(f"Upstream returned {response.status_code}")

            chunks: List[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                remaining = LINK_PREVIEW_IMAGE_MAX_BYTES - total
                if remaining <= 0:
                    raise UnsafeUrlError("Image exceeds the size cap")
                chunks.append(chunk[:remaining])
                total += len(chunk[:remaining])

            data = b"".join(chunks)
            content_type = _sniff_image_type(data)
            if content_type is None:
                raise UnsafeUrlError("Content is not an image we will re-serve")
            return data, content_type
        finally:
            response.close()

    raise UnsafeUrlError(f"Too many redirects (max {LINK_PREVIEW_MAX_REDIRECTS})")


_OG_FIELD_BY_PROPERTY = {
    "og:title": "title",
    "og:description": "description",
    "og:image": "image_url",
}


def parse_opengraph(html: Optional[str]) -> Dict[str, Optional[str]]:
    """Pull og:title / og:description / og:image out of a page's HTML.

    Falls back to the <title> tag when og:title is absent. Every field is optional —
    a page with no metadata yields all None rather than an error, because "this link
    has no preview" is a normal outcome, not a failure.
    """
    parsed: Dict[str, Optional[str]] = {"title": None, "description": None, "image_url": None}
    if not html:
        return parsed

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("meta"):
        # Some sites write name="og:title" instead of the spec's property=
        key = (tag.get("property") or tag.get("name") or "").strip().lower()
        field = _OG_FIELD_BY_PROPERTY.get(key)
        if field and parsed[field] is None:
            content = (tag.get("content") or "").strip()
            if content:
                parsed[field] = content

    if parsed["title"] is None and soup.title and soup.title.string:
        parsed["title"] = soup.title.string.strip() or None

    # The image URL is handed straight to the browser, so only ever emit http(s):
    # a javascript:/data: value here would become an injection vector in the card.
    image_url = parsed["image_url"]
    if image_url and urlparse(image_url).scheme.lower() not in ALLOWED_URL_SCHEMES:
        parsed["image_url"] = None

    return parsed


def _link_preview_payload(preview: "LinkPreview") -> Optional[Dict[str, Any]]:
    """The client-facing shape, or None when there is nothing worth drawing."""
    if not (preview.title or preview.image_url):
        return None
    return {
        "url": preview.url,
        "title": preview.title,
        "description": preview.description,
        "image_url": preview.image_url,
    }


def claim_link_preview_fetch_slot(user_id: int) -> bool:
    """Reserve one new-URL fetch for this user, or return False if they are over.

    Caching already stops the *same* URL being fetched twice; nothing stopped one
    account posting many *different* URLs, which is how this could be turned into a
    request amplifier. Cache hits never reach here, so ordinary use — where people
    share links others have already posted — is unaffected.

    A fixed window is deliberate: it is one Redis increment, and the worst case is
    twice the limit across a window boundary, which is fine for a cosmetic feature.
    """
    limit = getattr(settings, "LINK_PREVIEW_RATE_LIMIT_PER_MINUTE", 5)
    if limit <= 0:
        return True

    window = int(timezone.now().timestamp() // 60)
    key = f"link_preview_rate:{user_id}:{window}"
    try:
        # add() only succeeds once, which is what creates the counter with a TTL.
        cache.add(key, 0, timeout=120)
        used = cache.incr(key)
    except Exception:
        # A cache outage must not silently disable the limit *or* break sending.
        logger.exception("Link preview rate limit check failed for user %s", user_id)
        return False
    return used <= limit


def build_link_preview_map(messages: Iterable["Message"]) -> Dict[str, Dict[str, Any]]:
    """Resolve every URL on a page of messages in one query.

    Serializing a page one message at a time costs a lookup per message; a page of
    50 is 50 queries for what is really one `url IN (...)`. Callers that serialize
    many messages build this once and hand it to build_message_link_preview.
    """
    urls = set()
    for message in messages:
        url = extract_first_url(getattr(message, "content", None))
        if url:
            urls.add(normalize_preview_url(url))
    if not urls:
        return {}

    rows = LinkPreview.objects.filter(url__in=urls, status=LinkPreview.STATUS_READY)
    resolved: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        payload = _link_preview_payload(row)
        if payload is not None:
            resolved[row.url] = payload
    return resolved


def _viewer_dismissed_preview(message: "Message", viewer) -> bool:
    """True when this viewer hid the card, preferring a prefetched answer.

    The queryset can prefetch the current user's row into
    `_link_preview_hidden_for_viewer`; without it we fall back to one query, which
    is correct but only acceptable for a single message.
    """
    prefetched = getattr(message, "_link_preview_hidden_for_viewer", None)
    if prefetched is not None:
        return bool(prefetched)
    return message.link_preview_hidden_by.filter(id=viewer.id).exists()


def build_message_link_preview(
    message: "Message",
    viewer=None,
    preview_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Read-through lookup: the preview payload for a message, or None.

    Returns None unless the message's first URL has a *ready* cache entry with
    something worth drawing — pending, failed, blocked, and empty-but-ready
    entries all render as a plain message.

    A viewer who dismissed this card sees None. That is a per-message, per-user
    view preference: the message and the URL-keyed cache are left alone, so the
    same link still previews elsewhere and nothing is re-fetched.

    `preview_map` comes from build_link_preview_map when a whole page is being
    serialized, so the lookup costs no query at all.
    """
    url = extract_first_url(message.content)
    if not url:
        return None

    if viewer is not None and getattr(viewer, "is_authenticated", False):
        if _viewer_dismissed_preview(message, viewer):
            return None

    cache_key = normalize_preview_url(url)
    if preview_map is not None:
        return preview_map.get(cache_key)

    preview = LinkPreview.objects.filter(
        url=cache_key, status=LinkPreview.STATUS_READY
    ).first()
    return _link_preview_payload(preview) if preview is not None else None


def extract_message_plain_text(rich_body) -> str:
    """Extract searchable plain text from a Tiptap JSON document."""
    if rich_body is None:
        return ""
    if isinstance(rich_body, str):
        return rich_body.strip()
    parts = []

    def visit(value):
        if isinstance(value, dict):
            # mention nodes render as @username
            if value.get("type") == "mention":
                attrs = value.get("attrs") or {}
                label = attrs.get("label") or attrs.get("id") or ""
                if label:
                    parts.append(f"@{label}")
                return
            text = value.get("text")
            if isinstance(text, str):
                parts.append(text)
            for child in value.get("content", []):
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(rich_body)
    return " ".join(p.strip() for p in parts if p and p.strip())


def sync_message_mentions(message: Message, mention_ids: list[int]) -> None:
    """Sync MessageMention rows for an edited message and return new mention user ids."""
    existing = set(message.mentions.values_list('mentioned_user_id', flat=True))
    new_ids = set(mention_ids) - existing
    removed_ids = existing - set(mention_ids)
    if removed_ids:
        message.mentions.filter(mentioned_user_id__in=removed_ids).delete()
    if new_ids:
        MessageMention.objects.bulk_create(
            [MessageMention(message=message, mentioned_user_id=uid) for uid in new_ids],
            ignore_conflicts=True,
        )
    return list(new_ids)


def claim_recipients_for_delivery(message_id: int, user_ids: List[int]) -> List[int]:
    """Take ownership of a message's delivery to these users, and return who we won.

    The single claim mechanism for all three delivery paths — the realtime
    fan-out, the offline delivery task, and reconnect recovery. Winning the
    ``sent -> delivered`` transition *is* the claim: whichever path moves the
    row publishes, and the others find nothing left to take. The alternative,
    a lock keyed per recipient, costs a round-trip each on the hot path.

    ``skip_locked`` so a concurrent claimer takes the rows it can and leaves
    the rest rather than blocking on them.

    Callers must publish only to the returned users, and hand back anything
    they failed to publish with ``release_unpublished_recipients`` — claiming
    before publishing trades a duplicate delivery for a possible lost one.
    """
    if not user_ids:
        return []

    delivered_at = timezone.now()
    with transaction.atomic():
        claimed = list(
            MessageStatus.objects
            .select_for_update(skip_locked=True)
            .filter(message_id=message_id, user_id__in=user_ids, status='sent')
            .values_list('user_id', flat=True)
        )
        if claimed:
            MessageStatus.objects.filter(
                message_id=message_id,
                user_id__in=claimed,
            ).update(
                status='delivered',
                delivered_at=delivered_at,
                updated_at=delivered_at,
            )
    return claimed


def chat_group_name(chat_id: int) -> str:
    """Channel-layer group carrying events for one chat."""
    return f'chat_{int(chat_id)}'


def get_joinable_chat_ids(user_id: int) -> List[int]:
    """Chats this user is entitled to receive events for, straight from the database.

    Deliberately not cached and never derived from anything the client sends:
    this is the authorisation decision behind chat-group membership, so a stale
    or forged answer means someone receives a channel they are not in.
    """
    return list(
        ChatParticipant.objects
        .filter(user_id=user_id, is_active=True)
        .values_list('chat_id', flat=True)
    )


def notify_chat_membership_changed(chat_id: int, user_ids: Iterable[int]) -> None:
    """Tell affected users' live connections to re-sync their chat groups.

    Sent to each user's personal group, which they are always entitled to, so
    the consumer can re-read its entitlements from the database rather than
    trusting anything in the event. Best effort: a connection that misses this
    still re-syncs on its next connect, and the message path is unaffected when
    chat groups are disabled.
    """
    user_ids = [int(user_id) for user_id in user_ids]
    if not user_ids or not getattr(settings, 'CHAT_CHANNEL_GROUPS_ENABLED', False):
        return
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        broadcast_event_to_user_groups_sync(
            channel_layer,
            user_ids,
            {'type': 'chat_membership_changed', 'chat_id': int(chat_id)},
        )
    except Exception:
        # Never let a revocation broadcast undo a committed membership change.
        # The connection re-syncs on reconnect; surface it so a persistent
        # failure is visible rather than silently leaving stale memberships.
        logger.exception(
            'Failed to broadcast chat membership change for chat %s', chat_id
        )


def release_unpublished_recipients(message_id: int, user_ids: Iterable[int]) -> int:
    """Return claimed rows whose publish did not happen, so a retry can take them."""
    user_ids = list(user_ids)
    if not user_ids:
        return 0
    return MessageStatus.objects.filter(
        message_id=message_id,
        user_id__in=user_ids,
        status='delivered',
    ).update(
        status='sent',
        delivered_at=None,
        updated_at=timezone.now(),
    )


class OnlineStatusService:
    """Service for managing user online status"""
    
    ONLINE_KEY_PREFIX = 'user_online'
    ONLINE_CONNECTION_KEY_PREFIX = 'user_online_connections'
    PENDING_OFFLINE_KEY_PREFIX = 'user_pending_offline'
    PRESENCE_VERSION_KEY_PREFIX = 'user_presence_version'
    PRESENCE_RECIPIENTS_KEY_PREFIX = 'user_presence_recipients'
    ONLINE_TIMEOUT = 60 * 5  # 5 minutes
    OFFLINE_GRACE_SECONDS = 15
    PRESENCE_VERSION_TIMEOUT = 60 * 60 * 24 * 30  # 30 days
    # Recipient lists are explicitly invalidated on chat membership changes
    # (see ChatService.invalidate_presence_recipients_for_chat), so a longer TTL
    # is safe and keeps the cache effective beyond a single connect.
    PRESENCE_RECIPIENTS_TIMEOUT = 60 * 5  # 5 minutes

    @classmethod
    def _online_key(cls, user_id: int) -> str:
        return f'{cls.ONLINE_KEY_PREFIX}:{user_id}'

    @classmethod
    def _connection_key(cls, user_id: int) -> str:
        return f'{cls.ONLINE_CONNECTION_KEY_PREFIX}:{user_id}'

    @classmethod
    def _pending_offline_key(cls, user_id: int) -> str:
        return f'{cls.PENDING_OFFLINE_KEY_PREFIX}:{user_id}'

    @classmethod
    def _presence_version_key(cls, user_id: int) -> str:
        return f'{cls.PRESENCE_VERSION_KEY_PREFIX}:{user_id}'

    @classmethod
    def _presence_recipients_key(cls, user_id: int) -> str:
        return f'{cls.PRESENCE_RECIPIENTS_KEY_PREFIX}:{user_id}'

    @classmethod
    def invalidate_presence_recipients(cls, user_ids) -> None:
        """Drop cached recipient lists so chat membership changes take effect immediately."""
        keys = [cls._presence_recipients_key(uid) for uid in set(user_ids)]
        if not keys:
            return
        try:
            cache.delete_many(keys)
        except Exception:
            logger.exception("[OnlineStatus] Failed to invalidate presence recipient cache")

    @classmethod
    def _touch_cache_key(cls, key: str) -> None:
        try:
            cache.touch(key, cls.ONLINE_TIMEOUT)
        except Exception:
            pass

    @classmethod
    def _redis(cls):
        return get_redis_connection("default")

    @classmethod
    def _get_cached_connections(cls, user_id: int) -> set[str]:
        value = cache.get(cls._connection_key(user_id), [])
        if isinstance(value, (list, tuple, set)):
            return {str(connection_id) for connection_id in value if connection_id}
        return set()

    @classmethod
    def _set_cached_connections(cls, user_id: int, connection_ids: set[str]) -> int:
        connection_key = cls._connection_key(user_id)
        if connection_ids:
            cache.set(connection_key, list(connection_ids), timeout=cls.ONLINE_TIMEOUT)
        else:
            cache.delete(connection_key)
        return len(connection_ids)

    @classmethod
    def _add_connection(cls, user_id: int, connection_id: str) -> int:
        connection_key = cls._connection_key(user_id)
        try:
            redis = cls._redis()
            pipe = redis.pipeline(transaction=True)
            pipe.sadd(connection_key, connection_id)
            pipe.expire(connection_key, cls.ONLINE_TIMEOUT)
            pipe.scard(connection_key)
            _, _, count = pipe.execute()
            return int(count)
        except NotImplementedError:
            connections = cls._get_cached_connections(user_id)
            connections.add(str(connection_id))
            return cls._set_cached_connections(user_id, connections)

    @classmethod
    def _remove_connection(cls, user_id: int, connection_id: str) -> int:
        connection_key = cls._connection_key(user_id)
        try:
            redis = cls._redis()
            pipe = redis.pipeline(transaction=True)
            pipe.srem(connection_key, connection_id)
            pipe.scard(connection_key)
            pipe.expire(connection_key, cls.ONLINE_TIMEOUT)
            _, remaining, _ = pipe.execute()
            remaining = int(remaining)
            if remaining <= 0:
                redis.delete(connection_key)
            return max(0, remaining)
        except NotImplementedError:
            connections = cls._get_cached_connections(user_id)
            connections.discard(str(connection_id))
            return cls._set_cached_connections(user_id, connections)

    @classmethod
    def _connection_count(cls, user_id: int) -> Optional[int]:
        connection_key = cls._connection_key(user_id)
        try:
            return int(cls._redis().scard(connection_key))
        except NotImplementedError:
            return len(cls._get_cached_connections(user_id))
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to read Redis connection count for user {user_id}")
            return None

    @classmethod
    def next_presence_version(cls, user_id: int) -> Optional[int]:
        """Monotonic presence version for ordering presence events on the client."""
        key = cls._presence_version_key(user_id)
        seed = int(timezone.now().timestamp() * 1000)
        try:
            cache.add(key, seed, timeout=cls.PRESENCE_VERSION_TIMEOUT)
            current = cache.get(key)
            if current is None or int(current) < seed:
                cache.set(key, seed, timeout=cls.PRESENCE_VERSION_TIMEOUT)
            else:
                cache.touch(key, cls.PRESENCE_VERSION_TIMEOUT)
            return int(cache.incr(key))
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to increment presence version for user {user_id}")
            return None

    @classmethod
    def get_presence_version(cls, user_id: int) -> int:
        try:
            return int(cache.get(cls._presence_version_key(user_id), 0) or 0)
        except Exception:
            return 0
    
    @classmethod
    def set_online(cls, user_id: int) -> bool:
        """Mark user as online"""
        try:
            cache.set(cls._online_key(user_id), True, timeout=cls.ONLINE_TIMEOUT)
            cache.delete(cls._pending_offline_key(user_id))
            logger.info(f"[OnlineStatus] User {user_id} marked as ONLINE (timeout: {cls.ONLINE_TIMEOUT}s)")
            return True
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to mark user {user_id} ONLINE")
            return False
    
    @classmethod
    def set_offline(cls, user_id: int) -> bool:
        """Mark user as offline"""
        try:
            cache.delete(cls._online_key(user_id))
            cache.delete(cls._pending_offline_key(user_id))
            cache.delete(cls._connection_key(user_id))
            try:
                cls._redis().delete(cls._connection_key(user_id))
            except Exception:
                pass
            logger.info(f"[OnlineStatus] User {user_id} marked as OFFLINE")
            return True
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to mark user {user_id} OFFLINE")
            return False

    @classmethod
    def _set_offline_keep_connection_set(cls, user_id: int) -> bool:
        """
        Mark offline without deleting the connection set.

        Only call from finalize_offline_if_still_disconnected. The connection set
        must survive until the post-offline re-check so a racing reconnect can
        cancel the stale offline transition.
        """
        try:
            cache.delete(cls._online_key(user_id))
            cache.delete(cls._pending_offline_key(user_id))
            logger.info(f"[OnlineStatus] User {user_id} marked as OFFLINE")
            return True
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to mark user {user_id} OFFLINE")
            return False

    @classmethod
    def connection_opened(cls, user_id: int, connection_id: str) -> Tuple[int, bool, Optional[int]]:
        """Track a websocket connection and mark the user online."""
        try:
            count = cls._add_connection(user_id, connection_id)
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to add connection for user {user_id}; presence degraded")
            return 1, False, None

        if not cls.set_online(user_id):
            return count, False, None

        version = cls.next_presence_version(user_id) if count == 1 else None
        became_online = count == 1 and version is not None
        if count == 1 and version is None:
            # The user is online for queries (online_key is set) but we cannot emit an
            # ordered presence_update without a version, so recipients won't learn about
            # this transition until the user emits another visible event or reconnects.
            # Suppressing the undedupable broadcast is intentional; surface it so the
            # "silent missed event" failure mode is observable in monitoring.
            logger.warning(
                f"[OnlineStatus] presence_broadcast_skipped reason=no_version user={user_id}; "
                f"user is online but online transition was not broadcast"
            )
        logger.info(f"[OnlineStatus] User {user_id} connection opened (connections: {count})")
        return count, became_online, version

    @classmethod
    def connection_closed(cls, user_id: int, connection_id: str) -> Tuple[int, Optional[str]]:
        """Track a websocket disconnection and start delayed offline if no connections remain."""
        try:
            remaining = cls._remove_connection(user_id, connection_id)
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to remove connection for user {user_id}; presence degraded")
            return 0, None
        if remaining > 0:
            cls.set_online(user_id)
            logger.info(f"[OnlineStatus] User {user_id} connection closed (connections: {remaining})")
            return remaining, None

        offline_token = uuid.uuid4().hex
        try:
            cache.set(
                cls._pending_offline_key(user_id),
                offline_token,
                timeout=cls.OFFLINE_GRACE_SECONDS + 30,
            )
            cls._touch_cache_key(cls._online_key(user_id))
        except Exception:
            logger.exception(f"[OnlineStatus] Failed to set pending offline for user {user_id}")
            return 0, None
        logger.info(f"[OnlineStatus] User {user_id} last connection closed; pending offline token {offline_token}")
        return 0, offline_token

    @classmethod
    def finalize_offline_if_still_disconnected(cls, user_id: int, offline_token: str) -> Optional[int]:
        """Mark offline only if no newer connection canceled this pending offline token."""
        if cache.get(cls._pending_offline_key(user_id)) != offline_token:
            return None
        connection_count = cls._connection_count(user_id)
        if connection_count is None:
            return None
        if connection_count > 0:
            cache.delete(cls._pending_offline_key(user_id))
            cls.set_online(user_id)
            return None
        if not cls._set_offline_keep_connection_set(user_id):
            return None
        connection_count = cls._connection_count(user_id)
        if connection_count is None:
            cls.set_online(user_id)
            return None
        if connection_count > 0:
            cls.set_online(user_id)
            return None
        version = cls.next_presence_version(user_id)
        if version is None:
            return None
        logger.info(f"[OnlineStatus] User {user_id} finalized as OFFLINE")
        return version
    
    @classmethod
    def is_online(cls, user_id: int) -> bool:
        """Check if user is online"""
        try:
            result = cache.get(cls._online_key(user_id), False)
        except Exception:
            result = False
        logger.debug(f"[OnlineStatus] Checking user {user_id}: {result}")
        return result
    
    @classmethod
    def get_online_users(cls, user_ids: List[int]) -> List[int]:
        """Get list of online users from given user IDs"""
        keys_by_user_id = {
            user_id: cls._online_key(user_id)
            for user_id in user_ids
        }
        try:
            values_by_key = cache.get_many(keys_by_user_id.values())
        except Exception:
            values_by_key = {}
        return [
            user_id
            for user_id, key in keys_by_user_id.items()
            if values_by_key.get(key, False)
        ]
    
    @classmethod
    def heartbeat(cls, user_id: int, connection_id: Optional[str] = None) -> None:
        """Update user's online status (extend timeout)"""
        connection_key = cls._connection_key(user_id)
        try:
            redis = cls._redis()
            redis.expire(connection_key, cls.ONLINE_TIMEOUT)
        except Exception:
            cls._touch_cache_key(connection_key)
        cls.set_online(user_id)

    @classmethod
    def presence_snapshot(cls, user_ids: List[int]) -> List[Dict[str, Any]]:
        """Return current presence for a batch of users.

        Both lookups are batched. The online flags always were, but the
        versions were fetched one user at a time inside the comprehension, so a
        snapshot for a 99-member channel cost 99 sequential cache round-trips
        on the connect path — measured at 14.5 ms against 0.79 ms for the same
        output once batched.
        """
        online_keys = {
            user_id: cls._online_key(user_id)
            for user_id in user_ids
        }
        version_keys = {
            user_id: cls._presence_version_key(user_id)
            for user_id in user_ids
        }
        try:
            online_values = cache.get_many(online_keys.values())
        except Exception:
            online_values = {}
        try:
            version_values = cache.get_many(version_keys.values())
        except Exception:
            version_values = {}
        return [
            {
                'user_id': user_id,
                'is_online': bool(online_values.get(online_keys[user_id], False)),
                # Same coercion get_presence_version applies: a missing or
                # unparseable value reads as version 0.
                'version': int(version_values.get(version_keys[user_id], 0) or 0),
            }
            for user_id in user_ids
        ]


class ChatService:
    """Service for chat-related business logic"""

    @staticmethod
    def get_presence_recipient_ids(user_id: int) -> List[int]:
        """Users sharing an active chat with this user should receive presence changes."""
        cache_key = OnlineStatusService._presence_recipients_key(user_id)
        try:
            cached = cache.get(cache_key)
            if isinstance(cached, list) and all(isinstance(value, int) for value in cached):
                return cached
        except Exception:
            cached = None

        recipient_ids = list(
            ChatParticipant.objects.filter(
                chat__participants__user_id=user_id,
                chat__participants__is_active=True,
                is_active=True,
            )
            .exclude(user_id=user_id)
            .values_list('user_id', flat=True)
            .distinct()
        )
        try:
            cache.set(cache_key, recipient_ids, timeout=OnlineStatusService.PRESENCE_RECIPIENTS_TIMEOUT)
        except Exception:
            pass
        return recipient_ids

    @staticmethod
    def invalidate_presence_recipients_for_chat(chat: Chat, extra_user_ids=()) -> None:
        """
        Invalidate cached presence-recipient lists for everyone affected by a
        membership change in this chat.

        When a participant is added or removed, the recipient list of every other
        active participant changes (they should/shouldn't receive this user's
        presence), and so does the changed user's own list. Pass the changed
        user(s) via extra_user_ids when they are no longer active participants and
        so won't appear in the active-participant query — e.g. leave/remove, or a
        future chat delete/archive path that should pass the former participants
        (there is no such path today; the only membership mutators are the ones in
        this service, all of which already call this helper).

        Runs after the surrounding transaction commits so concurrent readers don't
        repopulate the cache from a not-yet-committed view of membership.
        """
        affected_ids = set(
            ChatParticipant.objects.filter(chat=chat, is_active=True)
            .values_list('user_id', flat=True)
        )
        affected_ids.update(extra_user_ids)
        if not affected_ids:
            return
        # Membership just changed, so any live connection's chat-group
        # membership is now stale. This runs for every membership mutator in
        # the codebase, which makes it the one place revocation has to be
        # correct: a connection that keeps a group it is no longer entitled to
        # goes on receiving that channel's messages for as long as it stays
        # open, which can be hours. Told before the cache invalidation below so
        # a removal is acted on even if that part is skipped for size.
        chat_id = chat.id
        transaction.on_commit(
            lambda: notify_chat_membership_changed(chat_id, affected_ids)
        )

        transaction.on_commit(
            lambda: OnlineStatusService.invalidate_presence_recipients(affected_ids)
        )

    @staticmethod
    def get_fallback_manager_user_id(chat: Chat) -> Optional[int]:
        return (
            ChatParticipant.objects.filter(chat=chat, is_active=True)
            .order_by('joined_at', 'id')
            .values_list('user_id', flat=True)
            .first()
        )

    @staticmethod
    def is_channel_manager(chat: Chat, user: User) -> bool:
        participant = ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).first()
        if not participant:
            return False
        if participant.is_manager:
            return True
        if chat.created_by_id and chat.created_by_id == user.id:
            return True
        if not ChatParticipant.objects.filter(chat=chat, is_active=True, is_manager=True).exists():
            return ChatService.get_fallback_manager_user_id(chat) == user.id
        return False
    
    @staticmethod
    @transaction.atomic
    def create_private_chat(
        current_user: User,
        other_user: User,
        project_id: int
    ) -> Tuple[Chat, bool]:
        """
        Create or get existing private chat between two users.
        
        Returns:
            Tuple[Chat, bool]: (chat, created)
        """
        # Check if chat already exists
        existing_chat = Chat.objects.filter(
            project_id=project_id,
            type=ChatType.PRIVATE,
            participants__user=current_user
        ).filter(
            participants__user=other_user
        ).distinct().first()
        
        if existing_chat:
            logger.info(f"Found existing private chat {existing_chat.id} between users {current_user.id} and {other_user.id}")
            return existing_chat, False
        
        # Validate users can chat
        can_chat, reason = Chat.can_users_chat(current_user, other_user)
        if not can_chat:
            logger.warning(f"Users {current_user.id} and {other_user.id} cannot chat: {reason}")
            raise ValueError(f"Cannot create chat: {reason}")
        
        # Create new chat
        chat = Chat.objects.create(
            project_id=project_id,
            type=ChatType.PRIVATE
        )
        
        # Add participants
        ChatParticipant.objects.create(chat=chat, user=current_user, is_active=True)
        ChatParticipant.objects.create(chat=chat, user=other_user, is_active=True)

        logger.info(f"Created private chat {chat.id} between users {current_user.id} and {other_user.id}")
        return chat, True
    
    @staticmethod
    @transaction.atomic
    def create_group_chat(
        current_user: User,
        project_id: int,
        name: str,
        participant_ids: List[int]
    ) -> Chat:
        """
        Create a group chat.
        
        Args:
            current_user: User creating the chat
            project_id: Project ID
            name: Chat name
            participant_ids: List of user IDs to add as participants
        
        Returns:
            Chat: Created chat
        """
        # Validate all participants are project members
        all_user_ids = participant_ids + [current_user.id]
        project_member_count = ProjectMember.objects.filter(
            project_id=project_id,
            user_id__in=all_user_ids,
            is_active=True
        ).count()
        
        if project_member_count != len(all_user_ids):
            logger.warning(f"Not all users are project members for project {project_id}")
            raise ValueError("All participants must be members of the project")
        
        # Create chat
        chat = Chat.objects.create(
            project_id=project_id,
            type=ChatType.GROUP,
            name=name,
            created_by=current_user,
        )
        
        # Add participants
        ChatParticipant.objects.bulk_create([
            ChatParticipant(
                chat=chat,
                user_id=user_id,
                is_active=True,
                is_manager=user_id == current_user.id,
            )
            for user_id in all_user_ids
        ])

        ChatService.invalidate_presence_recipients_for_chat(chat)
        logger.info(f"Created group chat {chat.id} '{name}' with {len(all_user_ids)} participants")
        return chat
    
    @staticmethod
    def display_name_for_user(user: User) -> str:
        """Human-readable name for notifications and UI."""
        full = (user.get_full_name() or "").strip()
        if full:
            return full
        if getattr(user, "username", None):
            return str(user.username)
        return user.email or str(user.pk)

    @staticmethod
    def get_user_chats(user: User, project_id: Optional[int] = None):
        """Get all chats for a user, optionally filtered by project"""
        query = Chat.objects.filter(
            participants__user=user,
            participants__is_active=True
        ).distinct()
        
        if project_id:
            query = query.filter(project_id=project_id)
        
        # Prefetch related data for performance
        query = query.prefetch_related(
            Prefetch(
                'participants',
                queryset=ChatParticipant.objects.select_related('user').filter(is_active=True)
            )
        ).select_related('project', 'created_by')
        
        return query
    
    @staticmethod
    @transaction.atomic
    def add_participant(chat: Chat, user: User, added_by: User) -> ChatParticipant:
        """
        Add a participant to a group chat.
        
        Args:
            chat: Chat to add participant to
            user: User to add
            added_by: User performing the action
        
        Returns:
            ChatParticipant: Created participant
        """
        if chat.type != ChatType.GROUP:
            raise ValueError("Can only add participants to group chats")
        
        # Self-join is allowed (user joining themselves via Browse channels).
        # It is only allowed for public channels that appear in Browse channels.
        if user == added_by and chat.visibility != ChannelVisibility.PUBLIC:
            raise ValueError("This channel can only be joined by invitation")

        # When adding someone else, the caller must already be a participant,
        # and restricted channels require a manager.
        if user != added_by:
            added_by_participant = ChatParticipant.objects.filter(chat=chat, user=added_by, is_active=True).first()
            if not added_by_participant:
                raise ValueError("Only participants can add new members")
            if chat.visibility == ChannelVisibility.MANAGER_INVITE and not ChatService.is_channel_manager(chat, added_by):
                raise ValueError("Only channel managers can add members")
        
        # Check if user can join
        if not chat.can_user_join(user):
            raise ValueError("User cannot join this chat (not a project member)")
        
        # Check if already a participant
        existing = ChatParticipant.objects.filter(chat=chat, user=user).first()
        if existing:
            if existing.is_active:
                logger.warning(f"User {user.id} already active participant in chat {chat.id}")
                raise ValueError("User is already a participant")
            else:
                # Reactivate
                existing.is_active = True
                existing.is_manager = False
                existing.joined_at = timezone.now()
                existing.save()
                logger.info(f"Reactivated participant {user.id} in chat {chat.id}")
                return existing
        
        # Add new participant
        participant = ChatParticipant.objects.create(
            chat=chat,
            user=user,
            is_active=True
        )

        logger.info(f"Added participant {user.id} to chat {chat.id} by user {added_by.id}")
        return participant
    
    @staticmethod
    @transaction.atomic
    def leave_chat(chat: Chat, user: User) -> None:
        """
        Remove current user from a chat (soft delete participant).
        Works for both private and group chats.
        """
        participant = ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).first()
        if not participant:
            raise ValueError("You are not a participant of this chat")

        participant.is_active = False
        participant.save(update_fields=['is_active', 'updated_at'])

        logger.info(f"User {user.id} left chat {chat.id}")

    @staticmethod
    @transaction.atomic
    def remove_participant(chat: Chat, user: User, removed_by: User) -> None:
        """
        Remove a participant from a group chat.
        
        Args:
            chat: Chat to remove participant from
            user: User to remove
            removed_by: User performing the action
        """
        if chat.type != ChatType.GROUP:
            raise ValueError("Can only remove participants from group chats")
        
        # Check if removed_by is a participant (or removing themselves)
        if removed_by != user:
            if not ChatParticipant.objects.filter(chat=chat, user=removed_by, is_active=True).exists():
                raise ValueError("Only participants can remove members")
        
        # Remove participant (soft delete)
        participant = ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).first()
        if not participant:
            raise ValueError("User is not a participant")
        
        participant.is_active = False
        participant.save()

        logger.info(f"Removed participant {user.id} from chat {chat.id} by user {removed_by.id}")


class ChatStarService:
    """Starred chats per user (project-scoped ordering)."""

    @staticmethod
    def _ensure_participant(user: User, chat: Chat) -> None:
        if not ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).exists():
            raise ValueError('You are not a participant of this chat')

    @staticmethod
    def list_starred_for_project(user: User, project_id: int):
        """Return ChatStar queryset for user in project, ordered by position."""
        return (
            ChatStar.objects.filter(user=user, chat__project_id=project_id)
            .select_related('chat', 'chat__project')
            .order_by('position', 'id')
        )

    @staticmethod
    @transaction.atomic
    def star_chat(user: User, chat_id: int) -> Tuple[ChatStar, bool]:
        chat = Chat.objects.filter(id=chat_id).select_related('project').first()
        if not chat:
            raise ValueError('Chat not found')
        ChatStarService._ensure_participant(user, chat)
        existing = ChatStar.objects.filter(user=user, chat=chat).first()
        if existing:
            return existing, False
        max_pos = ChatStar.objects.filter(
            user=user, chat__project_id=chat.project_id
        ).aggregate(m=Max('position'))['m']
        next_pos = (max_pos + 1) if max_pos is not None else 0
        star = ChatStar.objects.create(user=user, chat=chat, position=next_pos)
        return star, True

    @staticmethod
    @transaction.atomic
    def unstar_chat(user: User, chat_id: int) -> None:
        deleted, _ = ChatStar.objects.filter(user=user, chat_id=chat_id).delete()
        if not deleted:
            raise ValueError('Star not found')

    @staticmethod
    @transaction.atomic
    def reorder_starred(user: User, project_id: int, ordered_chat_ids: List[int]) -> None:
        """Set positions 0..n-1 for the given chat ids (must all be starred in project)."""
        if not isinstance(ordered_chat_ids, list):
            raise ValueError('chat_ids must be a list')
        unique_ids = list(dict.fromkeys(ordered_chat_ids))
        if len(unique_ids) != len(ordered_chat_ids):
            raise ValueError('Duplicate chat_ids are not allowed')

        total_starred = ChatStar.objects.filter(
            user=user,
            chat__project_id=project_id,
        ).count()
        if len(unique_ids) != total_starred:
            raise ValueError('chat_ids must include all starred chats in this project')

        stars = list(
            ChatStar.objects.filter(
                user=user,
                chat__project_id=project_id,
                chat_id__in=unique_ids,
            ).select_related('chat')
        )
        if len(stars) != len(unique_ids):
            raise ValueError('All chat_ids must be starred chats in this project')

        star_by_chat = {s.chat_id: s for s in stars}
        for idx, cid in enumerate(unique_ids):
            s = star_by_chat[cid]
            if s.position != idx:
                s.position = idx
                s.save(update_fields=['position', 'updated_at'])


class MessageService:
    """Service for message-related business logic"""

    class SourceAttachmentMissingError(Exception):
        """Raised when source attachment file cannot be read during forward."""

    class AttachmentCopyError(Exception):
        """Raised when attachment file copy fails during forward."""

    @staticmethod
    def _get_message_by_client_key(sender: User, client_message_id: str) -> Message:
        return (
            Message.objects
            .select_related('sender', 'reply_to', 'reply_to__sender', 'chat')
            .prefetch_related('attachments')
            .get(sender=sender, client_message_id=client_message_id)
        )

    @staticmethod
    def _assert_message_matches_request(message: Message, sender: User, chat: Chat) -> None:
        if message.sender_id != sender.id or message.chat_id != chat.id:
            raise ValueError('Cached client message does not match sender or chat')

    @staticmethod
    def _load_message_for_sender(message_id: int, sender: User, chat: Chat) -> Message:
        message = (
            Message.objects
            .select_related('sender', 'reply_to', 'reply_to__sender', 'chat')
            .prefetch_related('attachments')
            .get(id=message_id)
        )
        if message.sender_id != sender.id or message.chat_id != chat.id:
            raise ValueError('Cached client message does not match sender or chat')
        return message

    @staticmethod
    def _link_attachments_to_message(
        message: Message,
        sender: User,
        attachment_ids: List[int],
    ) -> None:
        if not attachment_ids:
            return
        linked_count = MessageAttachment.objects.filter(
            id__in=attachment_ids,
            uploader=sender,
            message__isnull=True,
        ).update(message=message)
        if linked_count > 0 and not message.has_attachments:
            message.has_attachments = True
            message.save(update_fields=['has_attachments', 'updated_at'])

    @staticmethod
    def _create_recipient_statuses(
        message: Message,
        sender: User,
        *,
        ignore_conflicts: bool = False,
    ) -> None:
        recipient_ids = ChatParticipant.objects.filter(
            chat=message.chat,
            is_active=True,
        ).exclude(user=sender).values_list('user_id', flat=True)
        MessageStatus.objects.bulk_create([
            MessageStatus(
                message_id=message.id,
                user_id=recipient_id,
                status='sent',
            )
            for recipient_id in recipient_ids
        ], batch_size=1000, ignore_conflicts=ignore_conflicts)

    @staticmethod
    def _schedule_new_message_side_effects(
        message: Message,
        sender: User,
        *,
        include_notifications: bool = True,
        route_agent: bool = True,
    ) -> None:
        message_id = message.id
        tenant_schema = current_tenant_schema()

        # These rows are written in the same PostgreSQL transaction as Message
        # and MessageStatus. A broker outage therefore cannot lose the hand-off:
        # the public dispatcher will publish every still-pending row later.
        outbox_events = [
            ChatOutboxEvent(
                tenant_schema=tenant_schema,
                event_type=ChatOutboxEvent.EVENT_MESSAGE_REALTIME,
                aggregate_id=message_id,
            ),
        ]
        if include_notifications:
            outbox_events.append(
                ChatOutboxEvent(
                    tenant_schema=tenant_schema,
                    event_type=ChatOutboxEvent.EVENT_MESSAGE_NOTIFICATIONS,
                    aggregate_id=message_id,
                )
            )
        ChatOutboxEvent.objects.bulk_create(outbox_events, ignore_conflicts=True)

        # Link preview: cosmetic and slow (an external site has to answer), so it
        # is queued after commit and never blocks the send.
        preview_url = extract_first_url(message.content)
        if preview_url:
            def enqueue_link_preview() -> None:
                try:
                    from .tasks import fetch_link_preview_task
                    fetch_link_preview_task.delay(
                        message_id,
                        preview_url,
                        tenant_schema=tenant_schema,
                    )
                except Exception:
                    logger.exception(
                        'Failed to enqueue link preview for message %s', message_id
                    )

            transaction.on_commit(enqueue_link_preview)

        if not route_agent:
            return

        def route_agent_bot() -> None:
            try:
                agent_bot_email = 'agent-bot@system.local'
                if sender.email == agent_bot_email:
                    return
                bot_participant = ChatParticipant.objects.filter(
                    chat_id=message.chat_id,
                    user__email=agent_bot_email,
                    is_active=True,
                ).first()
                if bot_participant:
                    from agent.tasks import handle_chat_message_for_agent
                    handle_chat_message_for_agent.delay(
                        message_id,
                        tenant_schema=tenant_schema,
                    )
            except Exception:
                logger.exception(
                    'Failed to route message to agent bot for message %s',
                    message_id,
                )

        transaction.on_commit(route_agent_bot)

    @staticmethod
    @transaction.atomic
    def _persist_message_with_attachments(
        *,
        sender: User,
        chat: Chat,
        content: str,
        attachment_ids: List[int],
        mention_ids: List[int],
        rich_body: Any,
        reply_to_id: Optional[int],
        parent_message_id: Optional[int],
        client_message_id: Optional[str] = None,
    ) -> Message:
        if not ChatParticipant.objects.filter(chat=chat, user=sender, is_active=True).exists():
            logger.warning('User %s is not a participant of chat %s', sender.id, chat.id)
            raise ValueError('You are not a participant of this chat')

        message = Message.objects.create(
            chat=chat,
            sender=sender,
            content=content,
            rich_body=rich_body,
            reply_to_id=reply_to_id,
            parent_message_id=parent_message_id,
            client_message_id=client_message_id,
        )

        if mention_ids:
            MessageMention.objects.bulk_create(
                [MessageMention(message=message, mentioned_user_id=uid) for uid in mention_ids],
                ignore_conflicts=True,
            )

        MessageService._link_attachments_to_message(message, sender, attachment_ids)
        # Recipient status fan-out is intentionally deferred to the durable
        # realtime outbox task. Writing O(channel members) rows in each HTTP
        # thread made 100 concurrent sends exhaust PostgreSQL connections and
        # kept requests open for tens of seconds.
        MessageService._schedule_new_message_side_effects(message, sender)

        logger.info(
            'Created message %s in chat %s by user %s with %s attachment(s)',
            message.id,
            chat.id,
            sender.id,
            len(attachment_ids),
        )
        return message

    @classmethod
    def resolve_client_message_commits(
        cls,
        viewer: User,
        client_message_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Map client outbox ids to committed server messages (DB lookup).

        Each entry embeds the fully-serialized message body so the reconnect
        ``outbox_ack`` can hydrate the client's outbox in the single WS round
        trip it already makes, instead of a follow-up REST fetch per id.
        """
        # Local import avoids a circular import (serializers import services).
        from .serializers import MessageSerializer

        valid_ids = [client_message_id for client_message_id in client_message_ids if client_message_id]
        if not valid_ids:
            return []
        # Mirror MessageViewSet._message_queryset so MessageSerializer's method
        # fields (mentions, reactions, thread summary, ...) read prefetched data
        # in bulk instead of issuing a query per message. Keep in sync with it.
        thread_replies_for_summary = (
            Message.objects
            .select_related('sender')
            .only(
                'id', 'chat_id', 'parent_message_id', 'sender_id', 'created_at',
                'sender__id', 'sender__username', 'sender__email', 'sender__avatar',
            )
            .order_by('created_at')
        )
        messages = (
            Message.objects
            .filter(sender_id=viewer.id, client_message_id__in=valid_ids)
            .select_related(
                'sender', 'chat', 'chat__project',
                'reply_to', 'reply_to__sender', 'forwarded_from_message',
            )
            .prefetch_related(
                'attachments',
                'reply_to__attachments',
                'mentions__mentioned_user',
                'reactions__user',
                'statuses',
                Prefetch(
                    'thread_replies',
                    queryset=thread_replies_for_summary,
                    to_attr='_thread_replies_for_summary',
                ),
            )
        )
        context = {'request': _OutboxAckSerializerRequest(viewer)}
        return [
            {
                'client_message_id': message.client_message_id,
                'message_id': message.id,
                'message': MessageSerializer(message, context=context).data,
            }
            for message in messages
        ]

    @classmethod
    def create_message_with_attachments(
        cls,
        *,
        sender: User,
        chat: Chat,
        content: str,
        attachment_ids: Optional[List[int]] = None,
        mention_ids: Optional[List[int]] = None,
        rich_body: Any = None,
        reply_to_id: Optional[int] = None,
        parent_message_id: Optional[int] = None,
        client_message_id: Optional[str] = None,
    ) -> Tuple[Message, bool]:
        """
        Create a chat message with optional attachments and idempotent retries.

        When client_message_id is supplied, concurrent or retried requests with the
        same key return the existing message without re-running statuses, Celery
        fanout, or agent-bot routing.

        Returns:
            (message, created) where created is False on a dedupe DB hit.
        """
        attachment_ids = attachment_ids or []
        mention_ids = mention_ids or []

        if not client_message_id:
            message = cls._persist_message_with_attachments(
                sender=sender,
                chat=chat,
                content=content,
                attachment_ids=attachment_ids,
                mention_ids=mention_ids,
                rich_body=rich_body,
                reply_to_id=reply_to_id,
                parent_message_id=parent_message_id,
            )
            return message, True

        try:
            with transaction.atomic():
                message = cls._persist_message_with_attachments(
                    sender=sender,
                    chat=chat,
                    content=content,
                    attachment_ids=attachment_ids,
                    mention_ids=mention_ids,
                    rich_body=rich_body,
                    reply_to_id=reply_to_id,
                    parent_message_id=parent_message_id,
                    client_message_id=client_message_id,
                )
            return message, True
        except IntegrityError as exc:
            # Only treat this as an idempotency hit if a message with this key
            # actually exists. Otherwise the IntegrityError came from a different
            # constraint (FK, NOT NULL, ...) and must not be masked as dedupe.
            try:
                message = cls._get_message_by_client_key(sender, client_message_id)
            except Message.DoesNotExist:
                raise exc
            cls._assert_message_matches_request(message, sender, chat)
            return message, False

    @staticmethod
    @transaction.atomic
    def create_message(
        chat: Chat,
        sender: User,
        content: str,
        forwarded_from_message: Optional[Message] = None,
        forwarded_from_sender_display: Optional[str] = None,
        forwarded_from_created_at: Optional[timezone.datetime] = None,
    ) -> Message:
        """
        Create a message in a chat.
        
        Args:
            chat: Chat to send message to
            sender: User sending the message
            content: Message content
            forwarded_from_message: Forwarded source message reference
            forwarded_from_sender_display: Snapshot of source sender display
            forwarded_from_created_at: Snapshot of source message created_at
        
        Returns:
            Message: Created message
        """
        # Validate sender is a participant
        if not ChatParticipant.objects.filter(chat=chat, user=sender, is_active=True).exists():
            logger.warning(f"User {sender.id} is not a participant of chat {chat.id}")
            raise ValueError("You are not a participant of this chat")
        
        # Create message
        message = Message.objects.create(
            chat=chat,
            sender=sender,
            content=content,
            forwarded_from_message=forwarded_from_message,
            forwarded_from_sender_display=forwarded_from_sender_display,
            forwarded_from_created_at=forwarded_from_created_at,
        )
        
        # Create message status for all recipients (excluding sender)
        recipients = ChatParticipant.objects.filter(
            chat=chat,
            is_active=True
        ).exclude(user=sender).select_related('user')
        
        MessageStatus.objects.bulk_create([
            MessageStatus(
                message=message,
                user=recipient.user,
                status='sent'
            )
            for recipient in recipients
        ])

        # Forwarding and other legacy service callers use this method too. Keep
        # their realtime hand-off durable, while avoiding duplicate persisted
        # notifications and agent routing already performed below.
        MessageService._schedule_new_message_side_effects(
            message,
            sender,
            include_notifications=False,
            route_agent=False,
        )
        
        logger.info(f"Created message {message.id} in chat {chat.id} by user {sender.id}")

        try:
            from notifications.services import create_or_update_chat_notification

            for recipient in recipients:
                if recipient.is_currently_muted() or recipient.notification_level != 'all':
                    continue
                create_or_update_chat_notification(
                    recipient_id=recipient.user_id,
                    actor_id=sender.id,
                    chat_id=chat.id,
                    message_id=message.id,
                    project_id=chat.project_id,
                    message_preview=content,
                    actor_name=sender.username or sender.email or "",
                )
        except Exception:
            logger.exception("In-app notification for chat message failed")

        # Route message to Agent Bot if it is a participant in this chat.
        # Wrapped in try/except so this never breaks normal chat functionality.
        try:
            AGENT_BOT_EMAIL = 'agent-bot@system.local'
            if sender.email != AGENT_BOT_EMAIL:
                bot_participant = ChatParticipant.objects.filter(
                    chat=chat,
                    user__email=AGENT_BOT_EMAIL,
                    is_active=True,
                ).first()
                if bot_participant:
                    from agent.tasks import handle_chat_message_for_agent
                    handle_chat_message_for_agent.delay(
                        message.id,
                        tenant_schema=current_tenant_schema(),
                    )
        except Exception:
            logger.exception("Failed to route message to agent bot for message %s", message.id)

        return message
    
    @staticmethod
    def get_chat_messages(
        chat: Chat,
        user: User,
        before: Optional[timezone.datetime] = None,
        after: Optional[timezone.datetime] = None,
        limit: int = 20
    ):
        """
        Get messages for a chat with cursor-based pagination.
        
        Args:
            chat: Chat to get messages from
            user: User requesting messages
            before: Get messages before this timestamp (for scrolling up)
            after: Get messages after this timestamp (for new messages)
            limit: Maximum number of messages to return
        
        Returns:
            QuerySet: Messages
        """
        # Validate user is a participant
        if not ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).exists():
            raise ValueError("You are not a participant of this chat")
        
        # Root messages only — thread replies are fetched via the thread_replies endpoint
        thread_replies_for_summary = (
            Message.objects
            .filter(chat=chat)
            .select_related('sender')
            .only(
                'id',
                'parent_message_id',
                'sender_id',
                'created_at',
                'sender__id',
                'sender__username',
                'sender__email',
                'sender__avatar',
            )
            .order_by('created_at')
        )
        thread_read_statuses_for_user = (
            ThreadReadStatus.objects
            .filter(user=user)
            .only('id', 'root_message_id', 'last_read_at')
        )
        hidden_by_current_user = User.objects.filter(id=user.id).only('id')
        message_statuses = MessageStatus.objects.select_related('user')

        query = Message.objects.filter(
            chat=chat,
            parent_message__isnull=True,
        ).select_related(
            'sender',
            'reply_to',
            'reply_to__sender',
            'forwarded_from_message',
        ).prefetch_related(
            'attachments',
            'reply_to__attachments',
            'mentions',
            'reactions__user',
            Prefetch(
                'statuses',
                queryset=message_statuses,
            ),
            Prefetch(
                'hidden_by_users',
                queryset=hidden_by_current_user,
                to_attr='_hidden_by_current_user',
            ),
            Prefetch(
                'thread_replies',
                queryset=thread_replies_for_summary,
                to_attr='_thread_replies_for_summary',
            ),
            Prefetch(
                'thread_read_statuses',
                queryset=thread_read_statuses_for_user,
                to_attr='_thread_read_status_for_user',
            ),
        ).annotate(
            _thread_reply_count=Count(
                'thread_replies',
                filter=Q(thread_replies__chat=chat),
                distinct=True,
            ),
            _thread_last_reply_at=Max(
                'thread_replies__created_at',
                filter=Q(thread_replies__chat=chat),
            ),
        )

        if before:
            query = query.filter(created_at__lt=before)

        if after:
            query = query.filter(created_at__gt=after)

        # Order by created_at descending for "before" (scrolling up)
        # Order by created_at ascending for "after" (new messages)
        if after:
            query = query.order_by('created_at')
        else:
            query = query.order_by('-created_at')

        return query[:limit]
    
    @staticmethod
    @transaction.atomic
    def mark_message_as_delivered(message: Message, user: User) -> None:
        """Mark a message as delivered for a user"""
        try:
            status = MessageStatus.objects.get(message=message, user=user)
            if status.status == 'sent':
                status.mark_as_delivered()
                logger.info(f"Marked message {message.id} as delivered for user {user.id}")
        except MessageStatus.DoesNotExist:
            logger.warning(f"MessageStatus not found for message {message.id} and user {user.id}")
    
    @staticmethod
    @transaction.atomic
    def mark_message_as_read(message: Message, user: User) -> None:
        """Mark a message as read for a user"""
        try:
            status = MessageStatus.objects.get(message=message, user=user)
            status.mark_as_read()
            logger.info(f"Marked message {message.id} as read for user {user.id}")
        except MessageStatus.DoesNotExist:
            logger.warning(f"MessageStatus not found for message {message.id} and user {user.id}")
    
    @staticmethod
    @transaction.atomic
    def mark_chat_as_read(chat: Chat, user: User, up_to_message: Optional[Message] = None) -> None:
        """
        Mark all messages in a chat as read for a user.
        
        Args:
            chat: Chat to mark as read
            user: User marking as read
            up_to_message: Optional message to mark up to (inclusive)
        """
        # Update participant's last_read_at
        participant = ChatParticipant.objects.filter(chat=chat, user=user, is_active=True).first()
        if not participant:
            raise ValueError("You are not a participant of this chat")
        
        if up_to_message:
            # Mark up to specific message
            participant.last_read_at = up_to_message.created_at
            participant.save()
            
            # Mark all message statuses as read up to this message
            MessageStatus.objects.filter(
                message__chat=chat,
                message__created_at__lte=up_to_message.created_at,
                user=user,
                status__in=['sent', 'delivered']
            ).update(
                status='read',
                read_at=timezone.now()
            )
            
            logger.info(f"Marked messages up to {up_to_message.id} as read for user {user.id} in chat {chat.id}")
        else:
            # Mark all messages as read
            old_last_read_at = participant.last_read_at
            participant.last_read_at = timezone.now()
            participant.save()
            
            logger.info(
                f"mark_chat_as_read: chat={chat.id}, user={user.id}, "
                f"old_last_read_at={old_last_read_at}, new_last_read_at={participant.last_read_at}, "
                f"unread_count_after={participant.get_unread_count()}"
            )
            
            MessageStatus.objects.filter(
                message__chat=chat,
                user=user,
                status__in=['sent', 'delivered']
            ).update(
                status='read',
                read_at=timezone.now()
            )
            
            logger.info(f"Marked all messages as read for user {user.id} in chat {chat.id}")
    
    @staticmethod
    def get_unread_count(user: User, chat: Optional[Chat] = None) -> int:
        """
        Get unread message count for a user.
        Uses ChatParticipant.get_unread_count() as single source of truth.
        
        Args:
            user: User to check
            chat: Optional specific chat to check (if None, returns total across all chats)
        
        Returns:
            int: Unread message count
        """
        if chat:
            # Get unread count for a specific chat
            try:
                participant = ChatParticipant.objects.get(
                    chat=chat,
                    user=user,
                    is_active=True
                )
                return participant.get_unread_count()
            except ChatParticipant.DoesNotExist:
                return 0
        else:
            # Get total unread count across all chats
            total = 0
            participants = ChatParticipant.objects.filter(
                user=user,
                is_active=True
            ).select_related('chat')
            
            for participant in participants:
                total += participant.get_unread_count()
            
            return total
    @staticmethod
    def _copy_file_field_for_forward(*, source_field, target_field, fallback_filename: str) -> None:
        """
        Copy a FileField/ImageField stream into a new target field.

        Uses streamed file handles and avoids loading the entire file into memory.
        """
        if not source_field:
            raise MessageService.SourceAttachmentMissingError("Source attachment file is missing")

        filename = os.path.basename(getattr(source_field, 'name', '') or fallback_filename)
        if not filename:
            filename = f"attachment-{uuid.uuid4().hex}"

        try:
            source_field.open('rb')
        except FileNotFoundError as exc:
            raise MessageService.SourceAttachmentMissingError("Source attachment file not found") from exc
        except Exception as exc:
            raise MessageService.AttachmentCopyError("Failed to open source attachment file") from exc

        try:
            target_field.save(filename, File(source_field.file), save=False)
        except FileNotFoundError as exc:
            raise MessageService.SourceAttachmentMissingError("Source attachment file not found") from exc
        except Exception as exc:
            raise MessageService.AttachmentCopyError("Failed to copy attachment file") from exc
        finally:
            try:
                source_field.close()
            except Exception:
                pass

    @staticmethod
    def _clone_attachments_for_forward(
        *,
        source_message: Message,
        target_message: Message,
        uploader: User,
    ) -> int:
        """Clone all attachments from source message to target message."""
        source_attachments = list(source_message.attachments.all())
        cloned_count = 0

        for source_attachment in source_attachments:
            cloned_attachment = MessageAttachment(
                message=target_message,
                uploader=uploader,
                file_type=source_attachment.file_type,
                file_size=source_attachment.file_size,
                original_filename=source_attachment.original_filename,
                mime_type=source_attachment.mime_type,
            )

            try:
                MessageService._copy_file_field_for_forward(
                    source_field=source_attachment.file,
                    target_field=cloned_attachment.file,
                    fallback_filename=source_attachment.original_filename or f"attachment-{source_attachment.id}",
                )

                if source_attachment.thumbnail:
                    thumbnail_name = os.path.basename(source_attachment.thumbnail.name or '')
                    if thumbnail_name:
                        thumbnail_name = f"{uuid.uuid4().hex}_{thumbnail_name}"
                    else:
                        thumbnail_name = f"thumb-{source_attachment.id}-{uuid.uuid4().hex}.jpg"

                    MessageService._copy_file_field_for_forward(
                        source_field=source_attachment.thumbnail,
                        target_field=cloned_attachment.thumbnail,
                        fallback_filename=thumbnail_name,
                    )

                cloned_attachment.save()
                cloned_count += 1
            except Exception:
                # Best effort cleanup for partially copied files in failed unit sends.
                if cloned_attachment.file:
                    cloned_attachment.file.delete(save=False)
                if cloned_attachment.thumbnail:
                    cloned_attachment.thumbnail.delete(save=False)
                raise

        if cloned_count > 0 and not target_message.has_attachments:
            target_message.has_attachments = True
            target_message.save(update_fields=['has_attachments', 'updated_at'])

        return cloned_count

    @staticmethod
    def forward_messages_batch(
        *,
        source_chat_id: int,
        source_message_ids: List[int],
        target_chat_ids: List[int],
        target_user_ids: List[int],
        user: User
    ) -> Dict[str, Any]:
        """
        Forward multiple source messages to multiple target chats and users.

        Notes:
        - Supports partial success.
        - Supports forwarding text-only and attachment-only messages.
        """
        source_chat = Chat.objects.select_related('project').filter(id=source_chat_id).first()
        if not source_chat:
            raise ValueError("Source chat not found")

        if not ChatParticipant.objects.filter(chat=source_chat, user=user, is_active=True).exists():
            raise ValueError("You are not a participant of the source chat")

        source_messages = list(
            Message.objects.filter(
                chat=source_chat,
                id__in=source_message_ids,
                is_deleted=False
            )
            .select_related('sender')
            .prefetch_related('attachments')
            .order_by('created_at')
        )

        found_message_ids = {msg.id for msg in source_messages}
        missing_message_ids = [msg_id for msg_id in source_message_ids if msg_id not in found_message_ids]
        if missing_message_ids:
            raise ValueError(f"Invalid source_message_ids for this chat: {missing_message_ids}")

        forwardable_messages: List[Message] = []
        skipped_message_ids: List[int] = []
        for message in source_messages:
            has_text = bool((message.content or '').strip())
            has_attachments = message.attachments.exists()
            if not has_text and not has_attachments:
                skipped_message_ids.append(message.id)
            else:
                forwardable_messages.append(message)

        resolved_target_chat_ids = set()
        created_private_chat_ids = set()
        resolution_errors: List[Dict[str, Any]] = []

        for target_chat_id in target_chat_ids:
            target_chat = Chat.objects.filter(id=target_chat_id).first()
            if not target_chat:
                resolution_errors.append({
                    'target_chat_id': target_chat_id,
                    'reason': 'target_chat_not_found'
                })
                continue

            if target_chat.project_id != source_chat.project_id:
                resolution_errors.append({
                    'target_chat_id': target_chat_id,
                    'reason': 'target_chat_project_mismatch'
                })
                continue

            if not ChatParticipant.objects.filter(
                chat=target_chat,
                user=user,
                is_active=True
            ).exists():
                resolution_errors.append({
                    'target_chat_id': target_chat_id,
                    'reason': 'not_participant'
                })
                continue

            resolved_target_chat_ids.add(target_chat.id)

        target_users = User.objects.filter(id__in=target_user_ids)
        users_by_id = {u.id: u for u in target_users}
        project_member_ids = set(
            ProjectMember.objects.filter(
                project=source_chat.project,
                user_id__in=target_user_ids,
                is_active=True
            ).values_list('user_id', flat=True)
        )

        for target_user_id in target_user_ids:
            if target_user_id == user.id:
                resolution_errors.append({
                    'target_user_id': target_user_id,
                    'reason': 'target_user_is_self'
                })
                continue

            target_user = users_by_id.get(target_user_id)
            if not target_user:
                resolution_errors.append({
                    'target_user_id': target_user_id,
                    'reason': 'target_user_not_found'
                })
                continue

            if target_user_id not in project_member_ids:
                resolution_errors.append({
                    'target_user_id': target_user_id,
                    'reason': 'target_user_not_project_member'
                })
                continue

            try:
                private_chat, created = ChatService.create_private_chat(
                    current_user=user,
                    other_user=target_user,
                    project_id=source_chat.project_id
                )
                resolved_target_chat_ids.add(private_chat.id)
                if created:
                    created_private_chat_ids.add(private_chat.id)
            except ValueError:
                resolution_errors.append({
                    'target_user_id': target_user_id,
                    'reason': 'cannot_create_private_chat'
                })

        sorted_target_chat_ids = sorted(resolved_target_chat_ids)
        failures: List[Dict[str, Any]] = []
        attempted_sends = 0
        succeeded_sends = 0
        created_messages: List[Dict[str, Any]] = []

        # Expand target resolution failures into message-level failures for clearer UI reporting.
        if forwardable_messages:
            for resolution_error in resolution_errors:
                for source_message in forwardable_messages:
                    failures.append({
                        'target_chat_id': resolution_error.get('target_chat_id'),
                        'target_user_id': resolution_error.get('target_user_id'),
                        'source_message_id': source_message.id,
                        'reason': resolution_error['reason']
                    })

        for target_chat_id in sorted_target_chat_ids:
            target_chat = Chat.objects.filter(id=target_chat_id).first()
            if not target_chat:
                for source_message in forwardable_messages:
                    failures.append({
                        'target_chat_id': target_chat_id,
                        'source_message_id': source_message.id,
                        'reason': 'target_chat_not_found'
                    })
                continue

            for source_message in forwardable_messages:
                attempted_sends += 1
                try:
                    with transaction.atomic():
                        new_message = MessageService.create_message(
                            chat=target_chat,
                            sender=user,
                            content=source_message.content or '',
                            forwarded_from_message=source_message,
                            forwarded_from_sender_display=(
                                source_message.sender.username or source_message.sender.email
                            ),
                            forwarded_from_created_at=source_message.created_at,
                        )

                        MessageService._clone_attachments_for_forward(
                            source_message=source_message,
                            target_message=new_message,
                            uploader=user
                        )

                    from .tasks import build_realtime_message_payload
                    created_messages.append(build_realtime_message_payload(new_message))
                    succeeded_sends += 1
                except MessageService.SourceAttachmentMissingError:
                    logger.warning(
                        "forward_messages_batch source_attachment_missing source_chat=%s source_message=%s target_chat=%s",
                        source_chat.id,
                        source_message.id,
                        target_chat_id,
                    )
                    failures.append({
                        'target_chat_id': target_chat_id,
                        'source_message_id': source_message.id,
                        'reason': 'source_attachment_missing'
                    })
                except MessageService.AttachmentCopyError as exc:
                    logger.warning(
                        "forward_messages_batch attachment_copy_failed source_chat=%s source_message=%s target_chat=%s error=%s",
                        source_chat.id,
                        source_message.id,
                        target_chat_id,
                        str(exc),
                    )
                    failures.append({
                        'target_chat_id': target_chat_id,
                        'source_message_id': source_message.id,
                        'reason': 'attachment_copy_failed'
                    })
                except Exception as exc:
                    logger.warning(
                        "forward_messages_batch send_failed source_chat=%s source_message=%s target_chat=%s error=%s",
                        source_chat.id,
                        source_message.id,
                        target_chat_id,
                        str(exc)
                    )
                    failures.append({
                        'target_chat_id': target_chat_id,
                        'source_message_id': source_message.id,
                        'reason': 'send_failed'
                    })

        if succeeded_sends == 0:
            status_value = 'failed'
        elif failures or skipped_message_ids:
            status_value = 'partial_success'
        else:
            status_value = 'success'

        result = {
            'status': status_value,
            'summary': {
                'requested_messages': len(source_message_ids),
                'forwardable_messages': len(forwardable_messages),
                'target_chats': len(sorted_target_chat_ids),
                'attempted_sends': attempted_sends,
                'succeeded_sends': succeeded_sends,
                'failed_sends': len(failures),
            },
            'resolved': {
                'target_chat_ids': sorted_target_chat_ids,
                'created_private_chat_ids': sorted(created_private_chat_ids),
                'skipped_message_ids': skipped_message_ids,
            },
            'failures': failures,
            'created_messages': created_messages,
        }

        logger.info(
            "forward_messages_batch source_chat=%s source_messages=%s target_chats=%s status=%s succeeded=%s failed=%s skipped=%s",
            source_chat.id,
            len(source_message_ids),
            len(sorted_target_chat_ids),
            status_value,
            succeeded_sends,
            len(failures),
            len(skipped_message_ids)
        )
        return result
