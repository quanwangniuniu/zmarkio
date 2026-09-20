"""Formula sandbox and evaluation tests for custom KPIs.

Most of these need no database: `validate_formula` and `evaluate` work on a
formula string and a dict of already-aggregated metric values. The warehouse
aggregation tests at the bottom are the only ones that touch the DB.
"""

import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django
from django.conf import settings

if not settings.configured:
    django.setup()

from report.kpi_registry import (
    KPIFormulaError,
    METRIC_REGISTRY,
    evaluate,
    list_metrics,
    resolve_metric_values,
    validate_formula,
)


METRICS = {
    "spend": Decimal("250"),
    "revenue": Decimal("1000"),
    "impressions": Decimal("20000"),
    "clicks": Decimal("500"),
    "purchases": Decimal("25"),
    "leads": Decimal("0"),
    "reach": Decimal("15000"),
    "calls": Decimal("0"),
    "messages": Decimal("0"),
    "landing_page_views": Decimal("300"),
    "video_3sec_views": Decimal("0"),
    "comments": Decimal("0"),
}


# ---------------------------------------------------------------------------
# Sandbox: what a formula is not allowed to be
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        '__import__("os").system("echo hi")',
        'os.system("echo hi")',
        "eval('1+1')",
        "open('/etc/passwd')",
        "lambda: 1",
        "1; DROP TABLE report_custom_kpi",
        "revenue.__class__",
    ],
)
def test_validate_rejects_non_formula_payloads(payload):
    """The engine has no eval/exec; these must not even validate."""
    with pytest.raises(KPIFormulaError):
        validate_formula(payload)


def test_validate_rejects_unknown_metric():
    with pytest.raises(KPIFormulaError) as exc:
        validate_formula("revenue / budget")
    assert "budget" in exc.value.message
    assert exc.value.code == "#NAME?"


def test_validate_rejects_unknown_function():
    with pytest.raises(KPIFormulaError) as exc:
        validate_formula("SQRT(revenue)")
    assert "SQRT" in exc.value.message


def test_validate_rejects_cell_reference():
    """Cell refs are meaningless without a sheet, and would reach the ORM."""
    with pytest.raises(KPIFormulaError) as exc:
        validate_formula("A1 + revenue")
    assert "A1" in exc.value.message


def test_validate_rejects_empty_formula():
    with pytest.raises(KPIFormulaError):
        validate_formula("   ")


def test_validate_rejects_overlong_formula():
    with pytest.raises(KPIFormulaError) as exc:
        validate_formula("revenue + " * 200 + "revenue")
    assert "too long" in exc.value.message or "too complex" in exc.value.message


def test_validate_rejects_deep_nesting():
    """Unbounded nesting would recurse until RecursionError inside the engine."""
    with pytest.raises(KPIFormulaError) as exc:
        validate_formula("(" * 40 + "revenue" + ")" * 40)
    assert "deeply" in exc.value.message or "too complex" in exc.value.message


def test_deeply_nested_formula_never_raises_out_of_evaluate():
    """A nest deep enough to exhaust the stack must degrade to an error code.

    `validate_formula` rejects this long before evaluation; this asserts the
    fallback, so a formula that somehow skips validation cannot take a request
    down with a RecursionError.
    """
    result = evaluate("(" * 5000 + "revenue" + ")" * 5000, METRICS)
    assert not result.ok
    assert result.error_code


# ---------------------------------------------------------------------------
# Sandbox: what a formula is allowed to be
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula",
    [
        "revenue / spend",
        "(revenue - spend) / spend",
        "clicks / impressions * 100",
        "landing_page_views / clicks",
        "ROUND(revenue / spend, 2)",
        "ABS(revenue - spend)",
        "MIN(revenue, spend)",
        "IF(spend > 0, revenue / spend, 0)",
        "spend * 1.15",
    ],
)
def test_validate_accepts_supported_formulas(formula):
    validate_formula(formula)


def test_snake_case_metric_names_are_supported():
    """The engine's tokenizer historically rejected '_' outright."""
    validate_formula("landing_page_views / clicks")
    assert evaluate("landing_page_views / clicks", METRICS).value == Decimal("0.6")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def test_evaluate_simple_ratio():
    assert evaluate("revenue / spend", METRICS).value == Decimal("4")


def test_evaluate_percentage():
    assert evaluate("clicks / impressions * 100", METRICS).value == Decimal("2.5")


def test_evaluate_round_with_metric_argument():
    assert evaluate("ROUND(revenue / spend, 1)", METRICS).value == Decimal("4.0")


def test_evaluate_conditional_takes_the_live_branch():
    assert evaluate("IF(spend > 0, revenue / spend, 0)", METRICS).value == Decimal("4")


def test_evaluate_conditional_skips_the_dead_branch():
    """The skipped branch is consumed, not resolved -- it must not error."""
    result = evaluate("IF(spend > 999999, revenue / spend, revenue)", METRICS)
    assert result.value == Decimal("1000")


def test_evaluate_division_by_zero_is_reported_not_raised():
    result = evaluate("revenue / leads", METRICS)
    assert not result.ok
    assert result.error_code == "#DIV/0!"
    assert result.error_message == "Division by zero."


def test_evaluate_unknown_name_is_reported_as_name_error():
    """Evaluation is defensive even when validation was skipped."""
    result = evaluate("revenue / unknown_metric", METRICS)
    assert not result.ok
    assert result.error_code == "#NAME?"


def test_evaluate_non_numeric_result_is_an_error():
    result = evaluate('IF(spend > 0, "high", "low")', METRICS)
    assert not result.ok
    assert "number" in result.error_message


def test_metric_catalog_matches_registry():
    catalog = list_metrics()
    assert {m["key"] for m in catalog} == set(METRIC_REGISTRY)
    assert all(m["label"] and m["unit"] for m in catalog)


# ---------------------------------------------------------------------------
# Warehouse aggregation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolve_metric_values_sums_project_rows_in_range(kpi_warehouse):
    """Only this project's linked rows, only inside the window."""
    values = resolve_metric_values(
        kpi_warehouse["project"].id,
        date.today() - timedelta(days=2),
        date.today(),
    )
    assert values["spend"] == Decimal("300.00")
    assert values["revenue"] == Decimal("1200.00")
    assert values["clicks"] == 30


@pytest.mark.django_db
def test_resolve_metric_values_excludes_other_projects(kpi_warehouse):
    values = resolve_metric_values(
        kpi_warehouse["other_project"].id,
        date.today() - timedelta(days=2),
        date.today(),
    )
    assert values["spend"] == Decimal("0")


@pytest.mark.django_db
def test_resolve_metric_values_excludes_rows_outside_the_date_range(kpi_warehouse):
    values = resolve_metric_values(
        kpi_warehouse["project"].id,
        date.today(),
        date.today(),
    )
    assert values["spend"] == Decimal("100.00")


@pytest.mark.django_db
def test_unlinked_meta_campaign_is_not_attributed_to_a_project(kpi_warehouse):
    """A Meta campaign with no mediajira_campaign has no project to belong to."""
    values = resolve_metric_values(
        kpi_warehouse["project"].id,
        date.today() - timedelta(days=30),
        date.today(),
    )
    assert values["spend"] == Decimal("300.00")  # the unlinked row is excluded
