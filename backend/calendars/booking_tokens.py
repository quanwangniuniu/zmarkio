"""
Cancellation tokens for guests.

A guest has no account, so the only way to let them cancel is to hand them
something unforgeable at booking time. A signed token beats a random secret
stored on the row: nothing extra to persist, and it cannot be brute-forced.

The token is scoped to one event and expires when that event's usefulness does
- there is nothing to cancel once the meeting has passed.
"""

from __future__ import annotations

from django.core import signing
from uuid import UUID

SALT = "calendars.booking.cancel"
FEED_SALT = "calendars.booking.feed"

# Long enough to cover a booking made far in advance, since max_advance_days
# already caps how far ahead a slot can be.
MAX_AGE_SECONDS = 400 * 24 * 60 * 60


def make_cancel_token(event_id) -> str:
    return signing.dumps(str(event_id), salt=SALT)


def read_cancel_token(token: str) -> str | None:
    """The event id the token vouches for, or None if it is bad or stale."""
    return _read_token(token, SALT)


def make_feed_token(event_id) -> str:
    """A calendar subscriber may read the feed, but cannot cancel the meeting."""
    return signing.dumps(str(event_id), salt=FEED_SALT)


def read_feed_token(token: str) -> str | None:
    """
    Only a feed token opens the feed.

    Subscriptions from before feed tokens existed carried a cancel token in the
    URL, and those stop resolving here. Nothing has shipped yet, so such links
    only exist in QA runs, and accepting them would keep a token that can call
    the meeting off circulating as a subscription URL - which is the thing a
    read-only feed token exists to avoid.
    """
    return _read_token(token, FEED_SALT)


def _read_token(token, salt):
    try:
        if not isinstance(token, str) or len(token) > 512:
            return None
        value = signing.loads(token, salt=salt, max_age=MAX_AGE_SECONDS)
        return str(UUID(value)) if isinstance(value, str) else None
    except (signing.BadSignature, ValueError, TypeError):
        return None
