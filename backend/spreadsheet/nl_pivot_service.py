"""
Natural-language → PivotConfig generation via Gemini.

Usage:
    from spreadsheet.nl_pivot_service import generate_pivot_config

    config = generate_pivot_config(
        instruction="summarize total revenue by region and month",
        sheet_schema={
            "columns": [{"index": 0, "name": "Region"}, ...],
            "row_count": 500,
        },
    )
    # config = {"rows_config": [...], "columns_config": [...],
    #           "values_config": [...], "filters_config": {},
    #           "show_grand_total_row": True}
"""
import logging

from core.services.gemini_client import call_gemini_json
from .nl_pivot_schema import SYSTEM_PROMPT, VALID_AGGREGATIONS, VALID_DISPLAY_MODES

logger = logging.getLogger(__name__)

# ── public API ──────────────────────────────────────────────────────────────


def generate_pivot_config(instruction: str, sheet_schema: dict) -> dict:
    """
    Call Gemini with the user instruction + sheet column schema and return a
    validated PivotConfig dict.

    Raises ValueError with a human-readable message if Gemini returns output
    that cannot be parsed or fails validation.
    """
    user_prompt = _build_user_prompt(instruction, sheet_schema)
    logger.info(
        "NL pivot generation: sending to Gemini. instruction=%r schema_cols=%d",
        instruction,
        len(sheet_schema.get("columns", [])),
    )
    try:
        raw = call_gemini_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.1,
        )
    except Exception as exc:
        logger.error("NL pivot generation: Gemini call failed. error=%s", exc)
        raise ValueError(f"Gemini request failed: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Gemini response is not a JSON object. Got: {str(raw)[:300]}")

    if "error" in raw:
        message = raw.get("error")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("Gemini returned an error response without a message.")
        raise ValueError(message.strip())

    if "config" not in raw or not isinstance(raw["config"], dict):
        raise ValueError(
            f"Gemini response is missing the 'config' object. Got: {str(raw)[:300]}"
        )

    return _validate_config(raw["config"], sheet_schema)


# ── helpers ─────────────────────────────────────────────────────────────────


def _build_user_prompt(instruction: str, sheet_schema: dict) -> str:
    columns = sheet_schema.get("columns", [])
    row_count = sheet_schema.get("row_count", 0)
    col_summary = ", ".join(
        f"{c['name']} (has_data: {str(c.get('has_data', True)).lower()})"
        for c in columns
    ) if columns else "unknown"
    return (
        f"Sheet columns: {col_summary}\n"
        f"Total data rows (excluding header): {max(0, row_count - 1)}\n\n"
        f"User instruction: {instruction}"
    )


def _validate_config(config: dict, sheet_schema: dict) -> dict:
    headers = {
        str(c.get("name", "")).strip()
        for c in sheet_schema.get("columns", [])
        if str(c.get("name", "")).strip()
    }

    rows_config = config.get("rows_config")
    if not isinstance(rows_config, list) or not rows_config:
        raise ValueError("PivotConfig 'rows_config' must be a non-empty list of column names.")
    for field in rows_config:
        _check_field(field, headers, "rows_config")

    columns_config = config.get("columns_config") or []
    if not isinstance(columns_config, list):
        raise ValueError("PivotConfig 'columns_config' must be a list.")
    for entry in columns_config:
        if isinstance(entry, dict):
            _check_field(entry.get("field"), headers, "columns_config")
            sort = entry.get("sort")
            if sort is not None and sort not in ("asc", "desc"):
                raise ValueError(f"PivotConfig columns_config sort '{sort}' is invalid.")
        else:
            _check_field(entry, headers, "columns_config")

    values_config = config.get("values_config")
    if not isinstance(values_config, list) or not values_config:
        raise ValueError("PivotConfig 'values_config' must be a non-empty list.")
    normalized_values = []
    for entry in values_config:
        if not isinstance(entry, dict):
            raise ValueError("PivotConfig values_config entries must be objects.")
        field = entry.get("field")
        _check_field(field, headers, "values_config")
        aggregation = entry.get("aggregation", "SUM")
        if aggregation not in VALID_AGGREGATIONS:
            raise ValueError(
                f"PivotConfig values_config aggregation '{aggregation}' is invalid. "
                f"Valid: {sorted(VALID_AGGREGATIONS)}"
            )
        display = entry.get("display", "VALUE")
        if display not in VALID_DISPLAY_MODES:
            raise ValueError(
                f"PivotConfig values_config display '{display}' is invalid. "
                f"Valid: {sorted(VALID_DISPLAY_MODES)}"
            )
        normalized_values.append({"field": field, "aggregation": aggregation, "display": display})

    show_grand_total_row = config.get("show_grand_total_row", True)
    if not isinstance(show_grand_total_row, bool):
        show_grand_total_row = True

    return {
        "rows_config": rows_config,
        "columns_config": columns_config,
        "values_config": normalized_values,
        "filters_config": {},
        "show_grand_total_row": show_grand_total_row,
    }


def _check_field(field, headers: set, source: str) -> None:
    if not isinstance(field, str) or not field.strip():
        raise ValueError(f"PivotConfig {source} contains an invalid field name.")
    if headers and field not in headers:
        raise ValueError(
            f"PivotConfig {source} references unknown column '{field}'. "
            f"Available columns: {sorted(headers)}"
        )
