"""Share-link lifecycle for a project's Custom KPIs.

The database constraint allows only one row per project with ``revoked_at``
null. It cannot see the clock, so this module decides whether that row is
still usable.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from report.models import ReportShareLink

SHARE_LINK_DAYS = (7, 14, 30)


class ShareLinkNotFound(Exception):
    """No row for this token, or the user revoked it before it expired."""


class ShareLinkExpired(Exception):
    """``expires_at`` is in the past. Checked before revocation."""


def create_share_link(*, project, created_by, days: int) -> tuple[ReportShareLink, bool]:
    """Return ``(link, created)``.

    An unexpired, unrevoked link is returned as-is. An unrevoked link that has
    already expired is stamped ``revoked_at`` in the same transaction so the
    unique slot is free, then a new link is inserted. ``created`` is False
    when the existing live link was reused.
    """
    if days not in SHARE_LINK_DAYS:
        raise ValueError(f"days must be one of {SHARE_LINK_DAYS}.")

    try:
        with transaction.atomic():
            return _create_share_link_locked(project, created_by, days)
    except IntegrityError:
        # Two creates raced on an empty slot. The winner holds the live link.
        now = timezone.now()
        existing = (
            ReportShareLink.objects.filter(project=project, revoked_at__isnull=True)
            .order_by("-created_at", "-id")
            .first()
        )
        if existing is not None and existing.expires_at > now:
            return existing, False
        raise


def _create_share_link_locked(project, created_by, days: int) -> tuple[ReportShareLink, bool]:
    now = timezone.now()
    existing = (
        ReportShareLink.objects.select_for_update()
        .filter(project=project, revoked_at__isnull=True)
        .order_by("-created_at", "-id")
        .first()
    )
    if existing is not None and existing.expires_at > now:
        return existing, False
    if existing is not None:
        existing.revoked_at = now
        existing.save(update_fields=["revoked_at", "updated_at"])
    link = ReportShareLink(
        project=project,
        created_by=created_by,
        expires_at=now + timedelta(days=days),
    )
    link.save()
    return link, True


def revoke_share_link(*, project) -> ReportShareLink:
    """Stamp the project's current unrevoked link. Missing link raises."""
    with transaction.atomic():
        link = (
            ReportShareLink.objects.select_for_update()
            .filter(project=project, revoked_at__isnull=True)
            .order_by("-created_at", "-id")
            .first()
        )
        if link is None:
            raise ShareLinkNotFound()
        link.revoked_at = timezone.now()
        link.save(update_fields=["revoked_at", "updated_at"])
        return link


def resolve_public_share_link(token: str) -> ReportShareLink:
    """Load a link for anonymous read.

    Expiry wins over revocation: a row released after it expired still raises
    ``ShareLinkExpired``. A revoke that happened while the link was unexpired
    raises ``ShareLinkNotFound``.
    """
    link = (
        ReportShareLink.objects.select_related("project")
        .filter(token=token)
        .first()
    )
    if link is None:
        raise ShareLinkNotFound()
    now = timezone.now()
    if link.expires_at <= now:
        raise ShareLinkExpired()
    if link.revoked_at is not None:
        raise ShareLinkNotFound()
    return link
