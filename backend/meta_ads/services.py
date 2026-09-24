"""Sync services that hydrate meta_ads tables from Graph API responses.

Each `sync_*` function is idempotent (update_or_create). The entry point
`sync_ad_account` runs campaigns -> adsets -> ads -> insights and records a
`MetaSyncRun` audit row.
"""

import datetime as _dt
import logging
import re
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from facebook_integration.models import MetaAdAccount
from campaign.services import CampaignPlatformIntegrationService
from utils.ad_preview_cache_keys import creative_preview_cache_key

from .meta_client import MetaApiError, graph_get, graph_paged
from .models import (
    MetaAd,
    MetaAdCreative,
    MetaAdSet,
    MetaCampaign,
    MetaInsightDaily,
    MetaSyncRun,
)

# Default preview cache TTL. 
CREATIVE_PREVIEW_CACHE_TTL_SECONDS = 3600


class CreativePreviewError(Exception):
    """Domain error while building a Meta creative preview payload."""

    def __init__(
        self,
        detail: str,
        *,
        status: int = 400,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        self.detail = detail
        self.status = status
        self.code = code
        self.extra = extra or {}
        super().__init__(detail)


def get_creative_preview(creative: MetaAdCreative, ad_format: str) -> dict[str, Any]:
    """Return a Meta iframe preview payload, cached per account + creative + format.

    Cache keys use ``meta_creative_id`` (which can collide across accounts) plus
    ``ad_account_id`` so another account's preview cannot leak .
    """
    key = creative_preview_cache_key(
        platform="meta",
        account_id=creative.ad_account_id,
        # Platform creative id can reuse the same numeric range across accounts;
        # account_id is what keeps those keys from colliding .
        creative_id=creative.meta_creative_id,
        variant=ad_format,
    )
    cached = cache.get(key)
    if cached is not None:
        return cached

    ad = creative.ads.order_by("-updated_at").first()
    if ad is None:
        raise CreativePreviewError(
            "No ad references this creative in the synced data, "
            "so no preview can be rendered. Try re-running sync.",
            status=400,
            code="no_linked_ad",
        )

    token = creative.ad_account.connection.get_access_token()
    if not token:
        raise CreativePreviewError(
            "Meta connection is missing its access token.",
            status=400,
        )

    try:
        graph_payload = graph_get(
            f"/{ad.meta_ad_id}/previews",
            token,
            params={"ad_format": ad_format},
        )
    except MetaApiError as err:
        raise CreativePreviewError(
            f"Graph API error: {err}",
            status=502,
            extra={"status_code": err.status_code},
        ) from err

    previews = (graph_payload or {}).get("data") or []
    if not previews:
        raise CreativePreviewError(
            "Meta returned no preview for this ad.",
            status=502,
        )

    body = previews[0].get("body", "") or ""
    match = re.search(r"""src\s*=\s*["']([^"']+)["']""", body)
    iframe_src = match.group(1).replace("&amp;", "&") if match else ""
    if not iframe_src.strip():
        # VideoModal only renders iframe_src; a 200 with an empty src looks
        # successful but shows "No preview data available." Do not cache it.
        raise CreativePreviewError(
            "Meta preview HTML did not include an iframe src.",
            status=502,
            code="missing_iframe_src",
        )

    payload = {
        "creative_id": creative.id,
        "video_id": creative.video_id,
        "meta_ad_id": ad.meta_ad_id,
        "ad_name": ad.name,
        "ad_format": ad_format,
        "iframe_src": iframe_src,
        "iframe_html": body,
        "thumbnail_url": creative.thumbnail_url,
        "permalink_url": "",
    }
    cache.set(key, payload, timeout=CREATIVE_PREVIEW_CACHE_TTL_SECONDS)
    return payload


logger = logging.getLogger(__name__)


# Meta returns the same conversion event under multiple action_type aliases
# (e.g. one purchase appears as `purchase`, `omni_purchase`,
# `offsite_conversion.fb_pixel_purchase`, `onsite_web_app_purchase`, etc.).
# Summing all of them would multiply the real count. Instead we use a priority
# list: first match wins, everything else is ignored.
LEAD_PRIORITY = ["lead", "onsite_conversion.lead_grouped"]
CALL_PRIORITY = ["phone_call", "onsite_conversion.click_to_call"]
PURCHASE_PRIORITY = [
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
]
MESSAGE_PRIORITY = [
    "onsite_conversion.messaging_conversation_started_7d",
    "messaging_conversation_started_7d",
]

# Landing page views: omni rolls up across web/app/on-Facebook surfaces, so it
# typically reports a higher-or-equal count than the plain pixel-only variant.
LANDING_PAGE_VIEW_PRIORITY = ["omni_landing_page_view", "landing_page_view"]

# 3-second video views: per Meta's 2018 video-metrics changelog, the legacy
# `video_3_sec_watched_actions` field was removed. The replacement is the
# `video_view` action_type inside the `actions` array.
VIDEO_3SEC_PRIORITY = ["video_view"]

# Comments: prefer the gross count (raw creative-resonance signal) and fall
# back to the on-Facebook net-of-removals variant when only that is reported.
COMMENT_PRIORITY = ["comment", "onsite_conversion.post_net_comment"]

CAMPAIGN_FIELDS = (
    "id,name,objective,status,effective_status,start_time,stop_time,"
    "daily_budget,lifetime_budget,special_ad_categories"
)

ADSET_FIELDS = (
    "id,name,campaign_id,status,effective_status,billing_event,optimization_goal,"
    "bid_amount,targeting,daily_budget,lifetime_budget"
)

AD_FIELDS = (
    "id,name,adset_id,status,effective_status,creative{id}"
)

CREATIVE_FIELDS = (
    "id,name,title,body,image_url,video_id,thumbnail_url,object_type,"
    "call_to_action_type,asset_feed_spec"
)

INSIGHT_FIELDS = (
    "ad_id,date_start,spend,impressions,reach,clicks,frequency,ctr,cpc,cpm,"
    "actions,action_values,"
    "video_p25_watched_actions,video_p50_watched_actions,"
    "video_p75_watched_actions,video_p100_watched_actions,"
    "video_avg_time_watched_actions"
)


def _set_phase(run_id: int, phase: str, progress: str) -> None:
    """Write the phase signals on a running MetaSyncRun without re-fetching it.

    Single .filter().update() so the phase write does not race with the row's
    own status / level_counts / error_message writes that the orchestrator
    issues on completion.
    """
    MetaSyncRun.objects.filter(pk=run_id).update(
        current_phase=phase,
        current_progress=progress,
        updated_at=timezone.now(),
    )


def sync_ad_account(ad_account: MetaAdAccount, access_token: str | None, *, days: int = 30, kind: str = "hourly") -> MetaSyncRun:
    """Run a full hydration pass. Returns the MetaSyncRun log row."""
    run = MetaSyncRun.objects.create(ad_account=ad_account, kind=kind, status="running")
    attempted_at = CampaignPlatformIntegrationService.begin_sync(ad_account=ad_account)
    counts: dict[str, int] = {}
    error_message = ""
    sync_error = None
    try:
        connection = ad_account.connection
        if (not access_token or not connection.is_active
                or (connection.token_expires_at and connection.token_expires_at <= timezone.now())):
            raise MetaApiError("Meta authorization expired or missing", status_code=401)
        _set_phase(run.id, "campaigns", "Fetching campaigns")
        counts["campaigns"] = sync_campaigns(ad_account, access_token)
        _set_phase(
            run.id,
            "adsets",
            f"Fetching ad sets (after {counts['campaigns']} campaigns)",
        )
        counts["adsets"] = sync_adsets(ad_account, access_token)
        _set_phase(
            run.id,
            "creatives",
            f"Fetching creatives (after {counts['adsets']} ad sets)",
        )
        counts["creatives"] = sync_creatives(ad_account, access_token)
        _set_phase(
            run.id,
            "ads",
            f"Fetching ads (after {counts['creatives']} creatives)",
        )
        counts["ads"] = sync_ads(ad_account, access_token)
        _set_phase(
            run.id,
            "creative_fk_backfill",
            f"Linking creatives to {counts['ads']} ads",
        )
        counts["creative_fk_backfilled"] = backfill_missing_creative_fks(
            ad_account, access_token
        )
        _set_phase(
            run.id,
            "insights",
            f"Streaming insights for {days}d window across {counts['ads']} ads",
        )
        counts["insights_rows"] = sync_insights(ad_account, access_token, days=days)
        status = "ok"
    except MetaApiError as err:
        sync_error = err
        logger.warning("sync_ad_account %s failed: %s", ad_account.meta_account_id, err)
        error_message = str(err)[:2000]
        status = "error"
    except Exception as err:  # pragma: no cover - defensive
        sync_error = err
        logger.exception("sync_ad_account %s exploded", ad_account.meta_account_id)
        error_message = f"unhandled: {err}"[:2000]
        status = "error"
    MetaSyncRun.objects.filter(pk=run.id).update(
        status=status,
        level_counts=counts,
        error_message=error_message,
        finished_at=timezone.now(),
        current_phase="",
        current_progress="",
        updated_at=timezone.now(),
    )
    run.refresh_from_db()
    CampaignPlatformIntegrationService.finish_sync(
        ad_account=ad_account, attempted_at=attempted_at, error=sync_error,
    )
    return run


# Per-level syncers ------------------------------------------------------------


def sync_campaigns(ad_account: MetaAdAccount, access_token: str) -> int:
    count = 0
    seen_ids: set[str] = set()
    for item in graph_paged(
        f"/act_{ad_account.meta_account_id}/campaigns",
        access_token,
        params={"fields": CAMPAIGN_FIELDS, "limit": 100},
    ):
        seen_ids.add(str(item.get("id")))
        MetaCampaign.objects.update_or_create(
            ad_account=ad_account,
            meta_campaign_id=str(item.get("id")),
            defaults={
                "name": item.get("name", "") or "",
                "objective": item.get("objective", "") or "",
                "status": item.get("status", "") or "",
                "effective_status": item.get("effective_status", "") or "",
                "start_time": _parse_dt(item.get("start_time")),
                "stop_time": _parse_dt(item.get("stop_time")),
                "daily_budget_cents": _to_int(item.get("daily_budget")),
                "lifetime_budget_cents": _to_int(item.get("lifetime_budget")),
                "special_ad_categories": item.get("special_ad_categories") or [],
                "is_deleted_on_meta": False,
            },
        )
        count += 1
    _mark_missing_campaigns(ad_account, seen_ids)
    return count


def _mark_missing_campaigns(ad_account: MetaAdAccount, seen_ids: set[str]) -> None:
    if not seen_ids:
        return
    MetaCampaign.objects.filter(ad_account=ad_account).exclude(
        meta_campaign_id__in=seen_ids
    ).update(is_deleted_on_meta=True)


def sync_adsets(ad_account: MetaAdAccount, access_token: str) -> int:
    campaign_map = {c.meta_campaign_id: c for c in MetaCampaign.objects.filter(ad_account=ad_account)}
    count = 0
    for item in graph_paged(
        f"/act_{ad_account.meta_account_id}/adsets",
        access_token,
        params={"fields": ADSET_FIELDS, "limit": 100},
    ):
        campaign = campaign_map.get(str(item.get("campaign_id", "")))
        if campaign is None:
            continue
        MetaAdSet.objects.update_or_create(
            campaign=campaign,
            meta_adset_id=str(item.get("id")),
            defaults={
                "name": item.get("name", "") or "",
                "status": item.get("status", "") or "",
                "effective_status": item.get("effective_status", "") or "",
                "billing_event": item.get("billing_event", "") or "",
                "optimization_goal": item.get("optimization_goal", "") or "",
                "bid_amount_cents": _to_int(item.get("bid_amount")),
                "targeting": item.get("targeting") or {},
                "daily_budget_cents": _to_int(item.get("daily_budget")),
                "lifetime_budget_cents": _to_int(item.get("lifetime_budget")),
                "is_deleted_on_meta": False,
            },
        )
        count += 1
    return count


def sync_creatives(ad_account: MetaAdAccount, access_token: str) -> int:
    count = 0
    for item in graph_paged(
        f"/act_{ad_account.meta_account_id}/adcreatives",
        access_token,
        params={"fields": CREATIVE_FIELDS, "limit": 50},
    ):
        MetaAdCreative.objects.update_or_create(
            ad_account=ad_account,
            meta_creative_id=str(item.get("id")),
            defaults={
                "name": item.get("name", "") or "",
                "title": item.get("title", "") or "",
                "body": item.get("body", "") or "",
                "image_url": item.get("image_url", "") or "",
                "video_id": item.get("video_id", "") or "",
                "thumbnail_url": item.get("thumbnail_url", "") or "",
                "object_type": item.get("object_type", "") or "",
                "call_to_action_type": item.get("call_to_action_type", "") or "",
                "asset_feed_spec": item.get("asset_feed_spec") or {},
            },
        )
        count += 1
    return count


def sync_ads(ad_account: MetaAdAccount, access_token: str) -> int:
    adset_map = {
        adset.meta_adset_id: adset
        for adset in MetaAdSet.objects.filter(campaign__ad_account=ad_account)
    }
    creative_map = {
        c.meta_creative_id: c
        for c in MetaAdCreative.objects.filter(ad_account=ad_account)
    }
    count = 0
    for item in graph_paged(
        f"/act_{ad_account.meta_account_id}/ads",
        access_token,
        params={"fields": AD_FIELDS, "limit": 50},
    ):
        adset = adset_map.get(str(item.get("adset_id", "")))
        if adset is None:
            continue
        creative_id = ((item.get("creative") or {}).get("id"))
        creative = creative_map.get(str(creative_id)) if creative_id else None
        MetaAd.objects.update_or_create(
            adset=adset,
            meta_ad_id=str(item.get("id")),
            defaults={
                "name": item.get("name", "") or "",
                "status": item.get("status", "") or "",
                "effective_status": item.get("effective_status", "") or "",
                "creative": creative,
                "is_deleted_on_meta": False,
            },
        )
        count += 1
    return count


def backfill_missing_creative_fks(
    ad_account: MetaAdAccount, access_token: str, *, max_ads: int = 2000
) -> int:
    """Second-pass creative linker for ads that came back without a creative id.

    Meta's batch `/act_<id>/ads?fields=creative{id}` endpoint omits the
    creative for dynamic (DCO) ads. A per-ad GET does return it. We walk
    over every unlinked ad for this account and patch the FK. Creatives
    referenced by the results that we haven't synced yet are pulled in
    with a minimal upsert so the FK never dangles.
    """
    unlinked = MetaAd.objects.filter(
        adset__campaign__ad_account=ad_account,
        creative__isnull=True,
    ).select_related("adset__campaign")[:max_ads]
    if not unlinked:
        return 0

    creative_cache: dict[str, MetaAdCreative] = {
        c.meta_creative_id: c
        for c in MetaAdCreative.objects.filter(ad_account=ad_account)
    }

    fixed = 0
    for ad in unlinked:
        try:
            payload = graph_get(
                f"/{ad.meta_ad_id}",
                access_token,
                params={"fields": "creative{id,name}"},
            )
        except MetaApiError as err:
            logger.debug("creative FK backfill skip ad=%s err=%s", ad.meta_ad_id, err)
            continue
        creative_ref = (payload or {}).get("creative") or {}
        meta_creative_id = str(creative_ref.get("id") or "").strip()
        if not meta_creative_id:
            continue
        creative = creative_cache.get(meta_creative_id)
        if creative is None:
            try:
                full = graph_get(
                    f"/{meta_creative_id}",
                    access_token,
                    params={"fields": CREATIVE_FIELDS},
                )
            except MetaApiError as err:
                logger.debug(
                    "creative FK backfill creative pull failed id=%s err=%s",
                    meta_creative_id,
                    err,
                )
                continue
            creative, _ = MetaAdCreative.objects.update_or_create(
                ad_account=ad_account,
                meta_creative_id=meta_creative_id,
                defaults={
                    "name": full.get("name", "") or creative_ref.get("name", "") or "",
                    "title": full.get("title", "") or "",
                    "body": full.get("body", "") or "",
                    "image_url": full.get("image_url", "") or "",
                    "video_id": full.get("video_id", "") or "",
                    "thumbnail_url": full.get("thumbnail_url", "") or "",
                    "object_type": full.get("object_type", "") or "",
                    "call_to_action_type": full.get("call_to_action_type", "") or "",
                    "asset_feed_spec": full.get("asset_feed_spec") or {},
                },
            )
            creative_cache[meta_creative_id] = creative
        MetaAd.objects.filter(pk=ad.pk).update(creative=creative)
        fixed += 1
    return fixed


def sync_insights(ad_account: MetaAdAccount, access_token: str, *, days: int = 30) -> int:
    """Pull per-ad per-day insights for the trailing `days` window."""
    until = timezone.now().date()
    since = until - _dt.timedelta(days=days)
    time_range = f'{{"since":"{since.isoformat()}","until":"{until.isoformat()}"}}'
    params = {
        "fields": INSIGHT_FIELDS,
        "level": "ad",
        "time_increment": 1,
        "time_range": time_range,
        "action_attribution_windows": "['7d_click','1d_view']",
        "limit": 500,
    }

    ad_map = {
        ad.meta_ad_id: ad
        for ad in MetaAd.objects.filter(adset__campaign__ad_account=ad_account)
    }
    count = 0
    with transaction.atomic():
        for row in graph_paged(
            f"/act_{ad_account.meta_account_id}/insights",
            access_token,
            params=params,
        ):
            ad = ad_map.get(str(row.get("ad_id", "")))
            if ad is None:
                continue
            date_str = row.get("date_start")
            if not date_str:
                continue
            actions = row.get("actions") or []
            action_counts = _parse_actions(actions)
            revenue = _parse_revenue(row.get("action_values") or [])
            video = _parse_video(row)
            lpv_count = _parse_landing_page_views(actions)
            video_3sec_count = _parse_video_3sec(actions)
            comment_count = _parse_comments(actions)
            MetaInsightDaily.objects.update_or_create(
                ad=ad,
                date=_dt.date.fromisoformat(date_str),
                defaults={
                    "spend": _to_decimal(row.get("spend")),
                    "impressions": _to_int(row.get("impressions")) or 0,
                    "reach": _to_int(row.get("reach")) or 0,
                    "clicks": _to_int(row.get("clicks")) or 0,
                    "frequency": _to_decimal(row.get("frequency")),
                    "ctr": _to_decimal(row.get("ctr")),
                    "cpc": _to_decimal(row.get("cpc")),
                    "cpm": _to_decimal(row.get("cpm")),
                    "leads": action_counts.get("leads", 0),
                    "calls": action_counts.get("calls", 0),
                    "purchases": action_counts.get("purchases", 0),
                    "messages": action_counts.get("messages", 0),
                    "revenue": revenue,
                    "video_p25": video["p25"],
                    "video_p50": video["p50"],
                    "video_p75": video["p75"],
                    "video_p100": video["p100"],
                    "video_avg_watch_seconds": video["avg_seconds"],
                    "lpv_count": lpv_count,
                    "video_3sec_count": video_3sec_count,
                    "comment_count": comment_count,
                    "raw": row,
                },
            )
            count += 1
    return count


# Parsing helpers --------------------------------------------------------------


def _pick_by_priority(entries: list[dict[str, Any]], priority: list[str], key: str) -> Any:
    """Return the `key` field from the first entry whose action_type matches
    the highest-priority option present in `entries`. Returns None if no match.
    """
    by_type = {e.get("action_type", ""): e for e in entries}
    for action_type in priority:
        if action_type in by_type:
            return by_type[action_type].get(key)
    return None


def _parse_actions(actions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "leads": _to_int(_pick_by_priority(actions, LEAD_PRIORITY, "value")) or 0,
        "calls": _to_int(_pick_by_priority(actions, CALL_PRIORITY, "value")) or 0,
        "purchases": _to_int(_pick_by_priority(actions, PURCHASE_PRIORITY, "value")) or 0,
        "messages": _to_int(_pick_by_priority(actions, MESSAGE_PRIORITY, "value")) or 0,
    }


def _parse_revenue(action_values: list[dict[str, Any]]) -> Decimal:
    value = _pick_by_priority(action_values, PURCHASE_PRIORITY, "value")
    return _to_decimal(value)


def _parse_landing_page_views(actions: list[dict[str, Any]]) -> int:
    return _to_int(_pick_by_priority(actions, LANDING_PAGE_VIEW_PRIORITY, "value")) or 0


def _parse_video_3sec(actions: list[dict[str, Any]]) -> int:
    return _to_int(_pick_by_priority(actions, VIDEO_3SEC_PRIORITY, "value")) or 0


def _parse_comments(actions: list[dict[str, Any]]) -> int:
    return _to_int(_pick_by_priority(actions, COMMENT_PRIORITY, "value")) or 0


def _parse_video(row: dict[str, Any]) -> dict[str, Any]:
    def _sum(key: str) -> int:
        total = 0
        for entry in row.get(key) or []:
            total += _to_int(entry.get("value")) or 0
        return total

    avg_entries = row.get("video_avg_time_watched_actions") or []
    avg_seconds = Decimal("0")
    if avg_entries:
        total = Decimal("0")
        n = 0
        for entry in avg_entries:
            total += _to_decimal(entry.get("value"))
            n += 1
        avg_seconds = total / n if n else Decimal("0")

    return {
        "p25": _sum("video_p25_watched_actions"),
        "p50": _sum("video_p50_watched_actions"),
        "p75": _sum("video_p75_watched_actions"),
        "p100": _sum("video_p100_watched_actions"),
        "avg_seconds": avg_seconds,
    }


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(Decimal(str(value)))
    except Exception:
        return None


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _parse_dt(value: Any):
    if not value:
        return None
    try:
        from django.utils.dateparse import parse_datetime

        return parse_datetime(str(value))
    except Exception:
        return None
