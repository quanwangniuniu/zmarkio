"""Cache key helpers for ad-creative preview payloads.

Creative / ad IDs are only unique within an ad account. Keys that omit
``account_id`` collide across accounts that reuse the same numeric id.
"""

from __future__ import annotations


def creative_preview_cache_key(
    *,
    platform: str,
    account_id: int | str,
    creative_id: int | str,
    variant: str | None = None,
) -> str:
    """Build an account-scoped preview cache key.

    ``platform`` is a short stable label (e.g. ``meta``, ``google``).
    ``variant`` optionally namespaces format / device (iframe format, DESKTOP, …).
    """
    account = str(account_id).strip()
    creative = str(creative_id).strip()
    if not account:
        raise ValueError("account_id is required for preview cache keys")
    if not creative:
        raise ValueError("creative_id is required for preview cache keys")
    platform_label = (platform or "").strip().lower()
    if not platform_label:
        raise ValueError("platform is required for preview cache keys")

    parts = ["ad_creative_preview", platform_label, account, creative]
    if variant:
        parts.append(str(variant).strip())
    return ":".join(parts)
