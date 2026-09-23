"""Custom KPI formula evaluation against the metric warehouse.

Formulas are evaluated by the spreadsheet engine
(`spreadsheet.formula_engine`) rather than a second parser, so KPI authors get
the same operators, functions and error codes as the spreadsheet. The engine
knows nothing about metrics: it is handed an `identifier_resolver` that binds
bare names like `spend` to aggregated numbers for one project and date range.

The engine is a hand-written recursive-descent parser with no `eval`/`exec`, so
a formula can only ever produce a number, a string, a boolean or an error code.
`validate_formula` adds the limits the engine itself does not impose (length,
nesting depth, unknown names, cell references).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping, Optional

from spreadsheet.formula_engine import (
    ComputedCellType,
    FormulaError,
    evaluate_formula,
    tokenize_formula,
)

# How far back an evaluation looks when the caller does not pass a date range.
DEFAULT_LOOKBACK_DAYS = 30

# Guards the engine does not apply itself. Deeply nested parentheses would
# otherwise recurse until Python raises RecursionError, which `evaluate_formula`
# swallows into an opaque #VALUE!.
MAX_FORMULA_LENGTH = 500
MAX_TOKEN_COUNT = 200
MAX_NESTING_DEPTH = 20

# Function names the engine implements. Used to tell a function call apart from
# a metric name during validation; the engine remains the source of truth for
# how each one behaves.
SUPPORTED_FUNCTIONS = frozenset(
    {
        "sum", "average", "count", "min", "max",
        "abs", "round", "floor", "ceiling",
        "if", "and", "or", "not", "vlookup",
        "true", "false",
    }
)


@dataclass(frozen=True)
class MetricDefinition:
    """One additive metric a formula may reference by name."""

    key: str
    label: str
    column: str
    unit: str


# Only additive (summable) columns are exposed. Meta also returns derived rates
# (ctr, cpc, cpm, frequency) per row, but averaging a rate across rows is not
# the rate for the period -- authors write `clicks / impressions` instead, which
# is the whole point of a formula builder.
METRIC_REGISTRY: Mapping[str, MetricDefinition] = {
    definition.key: definition
    for definition in (
        MetricDefinition("spend", "Spend", "spend", "currency"),
        MetricDefinition("revenue", "Revenue", "revenue", "currency"),
        MetricDefinition("impressions", "Impressions", "impressions", "count"),
        MetricDefinition("reach", "Reach", "reach", "count"),
        MetricDefinition("clicks", "Clicks", "clicks", "count"),
        MetricDefinition("purchases", "Purchases", "purchases", "count"),
        MetricDefinition("leads", "Leads", "leads", "count"),
        MetricDefinition("calls", "Calls", "calls", "count"),
        MetricDefinition("messages", "Messages", "messages", "count"),
        MetricDefinition("landing_page_views", "Landing page views", "lpv_count", "count"),
        MetricDefinition("video_3sec_views", "3-second video views", "video_3sec_count", "count"),
        MetricDefinition("comments", "Comments", "comment_count", "count"),
    )
}


# Not a formula error: the formula is fine, the window simply has no rows to
# evaluate it against. Kept distinct so callers can present it as information
# rather than a mistake -- without it every ratio reads as "Division by zero."
# on a project that has never synced.
NO_DATA_CODE = "#NODATA"

ERROR_MESSAGES: Mapping[str, str] = {
    "#DIV/0!": "Division by zero.",
    "#NAME?": "Unknown metric name.",
    "#VALUE!": "This formula could not be evaluated to a number.",
    "#REF!": "This formula is not valid.",
    "#N/A": "No value available.",
    NO_DATA_CODE: "No data for this period.",
}


class KPIFormulaError(Exception):
    """A formula that cannot be stored: raised by `validate_formula`."""

    def __init__(self, message: str, code: str = "#REF!") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class KPIEvaluation:
    """Outcome of evaluating one formula; exactly one of value/error is set."""

    value: Optional[Decimal] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_code is None

    @property
    def is_no_data(self) -> bool:
        """True when nothing was wrong -- there was just nothing to measure."""
        return self.error_code == NO_DATA_CODE


@dataclass(frozen=True)
class MetricSnapshot:
    """Aggregated metrics plus how many warehouse rows produced them.

    `row_count` is what separates a real division by zero from a project that
    has simply never synced: with no rows every metric aggregates to 0, so
    every ratio would otherwise look like a formula mistake.
    """

    values: Mapping[str, Decimal]
    row_count: int

    @property
    def has_data(self) -> bool:
        return self.row_count > 0


def list_metrics() -> list[dict]:
    """The metric catalog, for the builder's autocomplete."""
    return [
        {"key": d.key, "label": d.label, "unit": d.unit}
        for d in METRIC_REGISTRY.values()
    ]


def default_date_range(today: Optional[date] = None) -> tuple[date, date]:
    end = today or date.today()
    return end - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1), end


def validate_formula(formula: str) -> None:
    """Raise `KPIFormulaError` if `formula` cannot be stored as a KPI.

    Catches what is knowable without data: malformed syntax, unknown metric
    names, spreadsheet cell references, and formulas large enough to be a
    denial-of-service rather than a KPI.
    """
    text = (formula or "").strip()
    if not text:
        raise KPIFormulaError("Formula cannot be empty.")
    if len(text) > MAX_FORMULA_LENGTH:
        raise KPIFormulaError(
            f"Formula is too long ({len(text)} characters, limit {MAX_FORMULA_LENGTH})."
        )

    try:
        tokens = tokenize_formula(text)
    except FormulaError as exc:
        raise KPIFormulaError("This formula is not valid.", code=exc.code) from exc

    if len(tokens) > MAX_TOKEN_COUNT:
        raise KPIFormulaError(f"Formula is too complex (limit {MAX_TOKEN_COUNT} terms).")

    depth = 0
    for index, token in enumerate(tokens):
        if token.type == 'LPAREN':
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise KPIFormulaError(
                    f"Formula is nested too deeply (limit {MAX_NESTING_DEPTH} levels)."
                )
        elif token.type == 'RPAREN':
            depth -= 1
        elif token.type == 'REF':
            raise KPIFormulaError(
                f"'{token.value}' looks like a spreadsheet cell reference. "
                "KPI formulas use metric names instead.",
                code="#NAME?",
            )
        elif token.type == 'IDENT':
            name = token.value.lower()
            is_call = index + 1 < len(tokens) and tokens[index + 1].type == 'LPAREN'
            if is_call:
                if name not in SUPPORTED_FUNCTIONS:
                    raise KPIFormulaError(
                        f"Unknown function '{token.value}'.", code="#NAME?"
                    )
            elif name not in METRIC_REGISTRY and name not in ("true", "false"):
                raise KPIFormulaError(
                    f"Unknown metric '{token.value}'.", code="#NAME?"
                )

    # Token checks cannot see structural faults such as a trailing operator, so
    # parse the formula for real against a stand-in value for every metric.
    # A #DIV/0! here is ignored: with every metric at 1, `a / (b - c)` divides
    # by zero, which says nothing about the formula's structure.
    probe = evaluate(text, {key: Decimal(1) for key in METRIC_REGISTRY})
    if not probe.ok and probe.error_code != "#DIV/0!":
        raise KPIFormulaError(
            probe.error_message or "This formula is not valid.",
            code=probe.error_code or "#REF!",
        )


def resolve_metric_values(
    project_id: int,
    start_date: date,
    end_date: date,
) -> MetricSnapshot:
    """Aggregate every registered metric for one project over a date range.

    Meta insight rows reach a project through
    ad -> adset -> campaign -> mediajira_campaign -> project. A Meta campaign
    that has not been linked to a MediaJira campaign has no project to be
    attributed to and is therefore excluded.

    Every relation in that chain is many-to-one, so the row count comes back in
    the same query without inflating the sums.
    """
    from django.db.models import Count, Sum
    from meta_ads.models import MetaInsightDaily

    rows = MetaInsightDaily.objects.filter(
        ad__adset__campaign__mediajira_campaign__project_id=project_id,
        date__gte=start_date,
        date__lte=end_date,
    )
    aggregates = rows.aggregate(
        _row_count=Count("id"),
        **{key: Sum(d.column) for key, d in METRIC_REGISTRY.items()},
    )
    return MetricSnapshot(
        values={key: Decimal(aggregates.get(key) or 0) for key in METRIC_REGISTRY},
        row_count=aggregates.get("_row_count") or 0,
    )


def evaluate(formula: str, metric_values: Mapping[str, Decimal]) -> KPIEvaluation:
    """Evaluate `formula` against already-aggregated metric values."""

    def resolve(name: str) -> Optional[Decimal]:
        return metric_values.get(name.lower())

    # `sheet=None` is safe: `validate_formula` rejects cell references, so no
    # code path in the engine touches the sheet.
    result = evaluate_formula(formula, None, identifier_resolver=resolve)

    if result.computed_type == ComputedCellType.ERROR:
        code = result.error_code or "#VALUE!"
        return KPIEvaluation(
            error_code=code,
            error_message=ERROR_MESSAGES.get(code, "This formula could not be evaluated."),
        )
    if result.computed_type != ComputedCellType.NUMBER or result.computed_number is None:
        return KPIEvaluation(
            error_code="#VALUE!",
            error_message="This formula did not produce a number.",
        )
    return KPIEvaluation(value=result.computed_number)


def evaluate_snapshot(formula: str, snapshot: MetricSnapshot) -> KPIEvaluation:
    """Evaluate against a snapshot, reporting an empty window as no data.

    Checked before evaluating: with no rows every metric is 0, so `revenue /
    spend` would report a division by zero that says nothing about the formula.
    """
    if not snapshot.has_data:
        return KPIEvaluation(
            error_code=NO_DATA_CODE,
            error_message=ERROR_MESSAGES[NO_DATA_CODE],
        )
    return evaluate(formula, snapshot.values)


def evaluate_for_project(
    formula: str,
    project_id: int,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> KPIEvaluation:
    """Validate, aggregate and evaluate in one step (used by preview and read).

    A broken formula is reported ahead of an empty window: the author has to
    fix it either way, and "no data" would hide the real problem.
    """
    try:
        validate_formula(formula)
    except KPIFormulaError as exc:
        return KPIEvaluation(error_code=exc.code, error_message=exc.message)

    if start_date is None or end_date is None:
        default_start, default_end = default_date_range()
        start_date = start_date or default_start
        end_date = end_date or default_end

    return evaluate_snapshot(
        formula, resolve_metric_values(project_id, start_date, end_date)
    )
