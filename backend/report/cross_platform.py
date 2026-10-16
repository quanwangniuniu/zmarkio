from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from django.utils import timezone

PLATFORMS = ("meta", "google", "tiktok")

PLATFORM_SOURCES = {
    "meta": "facebook_meta",
    "google": "google_ads",
    "tiktok": "tiktok",
}

PLATFORM_METRICS = {
    "facebook_meta": {
        "spend": "amount_spent",
        "impressions": "impressions",
        "clicks": "clicks",
        "revenue": "purchase_value",
    },
    "google_ads": {
        "spend": "cost_micros",
        "impressions": "impressions",
        "clicks": "clicks",
        "revenue": "conversions_value",
    },
    "tiktok": {
        "spend": "spend",
        "impressions": "impressions",
        "clicks": "clicks",
        "revenue": "total_purchase_value",
    },
}


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def normalize_platform_metrics(platform: str, raw: Mapping) -> dict:
    """Map one platform payload to the additive canonical metric registry."""
    if platform not in PLATFORM_METRICS:
        raise ValueError(f"Unsupported platform: {platform}")
    fields = PLATFORM_METRICS[platform]
    spend = _decimal(raw.get(fields["spend"]))
    if platform == "google_ads":
        spend /= Decimal("1000000")
    return {
        "spend": spend,
        "impressions": _decimal(raw.get(fields["impressions"])),
        "clicks": _decimal(raw.get(fields["clicks"])),
        "revenue": _decimal(raw.get(fields["revenue"])),
    }


def calculate_kpis(metrics: Mapping[str, Decimal]) -> dict:
    spend = _decimal(metrics.get("spend"))
    impressions = _decimal(metrics.get("impressions"))
    clicks = _decimal(metrics.get("clicks"))
    revenue = _decimal(metrics.get("revenue"))
    return {
        "cpm": spend / impressions * Decimal("1000") if impressions else None,
        "cpc": spend / clicks if clicks else None,
        "ctr": clicks / impressions * Decimal("100") if impressions else None,
        "roas": revenue / spend if spend else None,
    }


def aggregate_platform_metrics(platform: str, records: Iterable[Mapping]) -> dict:
    totals = {
        "spend": Decimal("0"),
        "impressions": Decimal("0"),
        "clicks": Decimal("0"),
        "revenue": Decimal("0"),
    }
    count = 0
    for raw in records:
        count += 1
        normalized = normalize_platform_metrics(platform, raw)
        for key in totals:
            totals[key] += normalized[key]
    return {**totals, **calculate_kpis(totals), "record_count": count}


def _number(value):
    return None if value is None else str(value.quantize(Decimal("0.0001")))


def _row(platform: str, metrics: Mapping | None) -> dict:
    if metrics is None:
        return {
            "platform": platform,
            "status": "unavailable",
            "spend": None,
            "impressions": None,
            "clicks": None,
            "revenue": None,
            "cpm": None,
            "cpc": None,
            "ctr": None,
            "roas": None,
        }
    return {
        "platform": platform,
        "status": "ready" if metrics.get("record_count") else "empty",
        "spend": _number(metrics["spend"]),
        "impressions": int(metrics["impressions"]),
        "clicks": int(metrics["clicks"]),
        "revenue": _number(metrics["revenue"]),
        "cpm": _number(metrics["cpm"]),
        "cpc": _number(metrics["cpc"]),
        "ctr": _number(metrics["ctr"]),
        "roas": _number(metrics["roas"]),
    }


def _source_metrics(
    project_id: int,
    source_platform: str,
    start_date: date,
    end_date: date,
) -> dict:
    from report.models import AdsDraftPerformanceDaily

    rows = AdsDraftPerformanceDaily.objects.filter(
        project_id=project_id,
        platform=source_platform,
        date__range=(start_date, end_date),
    )
    totals = aggregate_platform_metrics(
        source_platform,
        rows.values_list("metrics", flat=True),
    )
    return totals


def build_cross_platform_report(
    project_id: int,
    *,
    platforms: Iterable[str] = PLATFORMS,
    lookback_days: int = 30,
    today: date | None = None,
) -> dict:
    end_date = today or timezone.localdate()
    start_date = end_date - timedelta(days=max(1, lookback_days) - 1)
    rows = []
    for platform in platforms:
        source_platform = PLATFORM_SOURCES.get(platform)
        if source_platform is None:
            continue
        metrics = _source_metrics(
            project_id, source_platform, start_date, end_date
        )
        rows.append(_row(platform, metrics))
    return {
        "status": "ready",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "rows": rows,
    }
