"""
iCalendar output for a single booking.

Two audiences, one format:

  * the .ics the guest downloads once, and
  * the URL their calendar app subscribes to and re-fetches.

The subscription is the reason this lives server-side rather than only in the
browser. A downloaded file is a snapshot: if the host later cancels, the guest's
calendar keeps showing a meeting that is not happening. A subscribed feed is
re-read, so publishing STATUS:CANCELLED marks the entry as cancelled on the next
refresh. Clients may keep a crossed-out entry rather than hide it.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

PRODID = "-//Marketing Simplified//Booking//EN"


def as_webcal_url(url: str) -> str:
    """
    Subscribe links use webcal://, not http://.

    webcal is not a transport. The calendar app rewrites it to https (or
    http on an explicitly insecure host) and GETs ordinary ICS. Publishing
    http:// makes a browser download a file; publishing webcal:// makes
    Outlook / Apple Calendar offer a subscription.
    """
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            return "webcal://" + url[len(prefix) :]
    return url


def _stamp(value: datetime) -> str:
    return value.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def escape_text(value: str) -> str:
    """Escape per RFC 5545. Backslashes first, or later escapes double up."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> list[str]:
    """
    Wrap at 75 octets, continuing with a leading space.

    Long titles and URLs routinely exceed the limit, and strict parsers reject
    an over-length line outright rather than tolerating it.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]

    chunks: list[str] = []
    current = b""
    for char in line:
        encoded = char.encode("utf-8")
        # 74 on continuation lines to leave room for the leading space.
        limit = 75 if not chunks else 74
        if len(current) + len(encoded) > limit:
            chunks.append(current.decode("utf-8"))
            current = b""
        current += encoded
    if current:
        chunks.append(current.decode("utf-8"))
    return [chunks[0]] + [f" {chunk}" for chunk in chunks[1:]]


def build_booking_ics(
    *,
    uid: str,
    title: str,
    start: datetime,
    end: datetime,
    description: str = "",
    url: str = "",
    organizer_email: str = "",
    cancelled: bool = False,
) -> str:
    """
    One VEVENT, as a complete VCALENDAR.

    The UID stays stable across fetches so a subscribing client updates the
    entry it already has instead of accumulating duplicates.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(title)}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(datetime.now(dt_timezone.utc))}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{escape_text(title)}",
        # Cancelled entries are published rather than dropped: a subscriber has
        # to be told the meeting is off; clients decide how to display it.
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        f"SEQUENCE:{1 if cancelled else 0}",
    ]
    if description:
        lines.append(f"DESCRIPTION:{escape_text(description)}")
    if url:
        lines.append(f"URL:{escape_text(url)}")
    if organizer_email:
        lines.append(f"ORGANIZER:mailto:{organizer_email.replace(chr(13), '').replace(chr(10), '')}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    folded: list[str] = []
    for line in lines:
        folded.extend(fold_line(line))
    # RFC 5545: the stream ends with CRLF. Outlook rejects a file that doesn't.
    return "\r\n".join(folded) + "\r\n"
