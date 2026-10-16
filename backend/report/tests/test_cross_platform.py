from datetime import date
from decimal import Decimal

import pytest

from report.cross_platform import (
    aggregate_platform_metrics,
    build_cross_platform_report,
    calculate_kpis,
    normalize_platform_metrics,
)


@pytest.mark.parametrize(
    ("platform", "raw", "expected"),
    [
        (
            "facebook_meta",
            {
                "amount_spent": "20",
                "impressions": 2000,
                "clicks": 40,
                "purchase_value": "80",
            },
            {
                "spend": Decimal("20"),
                "impressions": Decimal("2000"),
                "clicks": Decimal("40"),
                "revenue": Decimal("80"),
            },
        ),
        (
            "google_ads",
            {
                "cost_micros": 20_000_000,
                "impressions": 2000,
                "clicks": 40,
                "conversions_value": "80",
            },
            {
                "spend": Decimal("20"),
                "impressions": Decimal("2000"),
                "clicks": Decimal("40"),
                "revenue": Decimal("80"),
            },
        ),
        (
            "tiktok",
            {
                "spend": "20",
                "impressions": 2000,
                "clicks": 40,
                "total_purchase_value": "80",
            },
            {
                "spend": Decimal("20"),
                "impressions": Decimal("2000"),
                "clicks": Decimal("40"),
                "revenue": Decimal("80"),
            },
        ),
    ],
)
def test_platform_metrics_normalize_to_canonical_values(platform, raw, expected):
    assert normalize_platform_metrics(platform, raw) == expected


def test_aggregation_calculates_kpis_from_summed_base_metrics():
    result = aggregate_platform_metrics(
        "facebook_meta",
        [
            {
                "amount_spent": 10,
                "impressions": 1000,
                "clicks": 10,
                "purchase_value": 20,
            },
            {
                "amount_spent": 30,
                "impressions": 1000,
                "clicks": 30,
                "purchase_value": 100,
            },
        ],
    )

    assert result["spend"] == Decimal("40")
    assert result["cpm"] == Decimal("20")
    assert result["cpc"] == Decimal("1")
    assert result["ctr"] == Decimal("2")
    assert result["roas"] == Decimal("3")


def test_zero_denominators_return_none():
    result = calculate_kpis(
        {
            "spend": Decimal("0"),
            "impressions": Decimal("0"),
            "clicks": Decimal("0"),
            "revenue": Decimal("0"),
        }
    )

    assert result == {"cpm": None, "cpc": None, "ctr": None, "roas": None}


@pytest.mark.django_db
def test_report_reads_all_three_ads_draft_sources():
    from core.models import Project
    from report.models import AdsDraftPerformanceDaily

    project = Project.objects.create(name="Cross-platform report")
    records = [
        (
            "facebook_meta",
            {
                "amount_spent": 20,
                "impressions": 2000,
                "clicks": 40,
                "purchase_value": 80,
            },
        ),
        (
            "google_ads",
            {
                "cost_micros": 20_000_000,
                "impressions": 2000,
                "clicks": 40,
                "conversions_value": 80,
            },
        ),
        (
            "tiktok",
            {
                "spend": 20,
                "impressions": 2000,
                "clicks": 40,
                "total_purchase_value": 80,
            },
        ),
    ]
    for platform, metrics in records:
        AdsDraftPerformanceDaily.objects.create(
            project=project,
            platform=platform,
            date="2026-10-12",
            metrics=metrics,
        )

    report = build_cross_platform_report(
        project.id,
        lookback_days=1,
        today=date(2026, 10, 12),
    )

    assert [row["platform"] for row in report["rows"]] == [
        "meta",
        "google",
        "tiktok",
    ]
    assert all(row["status"] == "ready" for row in report["rows"])
    assert all(row["roas"] == "4.0000" for row in report["rows"])
