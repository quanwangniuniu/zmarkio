"""
Natural-language → PatternStep generation via Gemini.

Usage:
    from spreadsheet.nl_pattern_service import generate_pattern_steps

    steps = generate_pattern_steps(
        instruction="add column K = I+J, highlight rows where K > 1000",
        sheet_schema={
            "columns": [{"index": 0, "name": "Campaign"}, ...],
            "row_count": 500,
        },
    )
    # steps is a list of dicts ready to be serialised as TimelineItem objects.
"""
import logging
import uuid
from datetime import datetime, timezone

from core.services.gemini_client import call_gemini_json
from .nl_pattern_schema import SYSTEM_PROMPT, VALID_STEP_TYPES, VALID_HIGHLIGHT_OPERATORS

logger = logging.getLogger(__name__)

# ── public API ──────────────────────────────────────────────────────────────


def generate_pattern_steps(instruction: str, sheet_schema: dict) -> list[dict]:
    """
    Call Gemini with the user instruction + sheet column schema and return a
    validated list of PatternStep dicts.

    Raises ValueError with a human-readable message if Gemini returns output
    that cannot be parsed or fails validation.
    """
    user_prompt = _build_user_prompt(instruction, sheet_schema)
    logger.info(
        "NL pattern generation: sending to Gemini. instruction=%r schema_cols=%d",
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
        logger.error("NL pattern generation: Gemini call failed. error=%s", exc)
        raise ValueError(f"Gemini request failed: {exc}") from exc

    logger.info(
        "NL pattern generation: Gemini responded. raw_keys=%s steps_count=%s",
        list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
        len(raw.get("steps", [])) if isinstance(raw, dict) else "n/a",
    )

    if not isinstance(raw, dict) or "steps" not in raw:
        raise ValueError(
            f"Gemini response is missing the 'steps' key. Got: {str(raw)[:300]}"
        )

    steps = raw["steps"]
    if not isinstance(steps, list):
        raise ValueError("Gemini 'steps' field is not a list.")

    # Surface ERROR_REQUEST immediately — skip all further processing.
    if steps and isinstance(steps[0], dict) and steps[0].get("type") == "ERROR_REQUEST":
        params = steps[0].get("params") or {}
        if not isinstance(params.get("message"), str) or not params["message"].strip():
            raise ValueError("ERROR_REQUEST step is missing a 'message' param.")
        now = datetime.now(timezone.utc).isoformat()
        return [{
            "id": str(uuid.uuid4()),
            "type": "ERROR_REQUEST",
            "seq": 0,
            "disabled": False,
            "params": {"message": params["message"].strip()},
            "createdAt": now,
        }]

    # Collapse any per-row APPLY_FORMULA repetitions into seed + FILL_SERIES
    steps = _collapse_repeated_formulas(steps, sheet_schema)

    # Hard cap: never return more than 20 steps
    MAX_STEPS = 20
    if len(steps) > MAX_STEPS:
        logger.warning(
            "NL pattern generation: capping %d steps to %d", len(steps), MAX_STEPS
        )
        steps = steps[:MAX_STEPS]

    validated = []
    for i, step in enumerate(steps):
        validated.append(_validate_step(step, i, sheet_schema))

    return validated


# ── helpers ─────────────────────────────────────────────────────────────────


def _build_user_prompt(instruction: str, sheet_schema: dict) -> str:
    columns = sheet_schema.get("columns", [])
    row_count = sheet_schema.get("row_count", 0)
    col_summary = ", ".join(
        f"{c['name']} (col {c['index']}, has_data: {str(c.get('has_data', True)).lower()})"
        for c in columns
    ) if columns else "unknown"
    return (
        f"Sheet columns (name → 0-based index, has_data): {col_summary}\n"
        f"Total data rows (excluding header): {max(0, row_count - 1)}\n\n"
        f"User instruction: {instruction}"
    )


def _validate_step(step: dict, position: int, sheet_schema: dict) -> dict:
    """Validate one step dict and return it with a generated id/createdAt."""
    if not isinstance(step, dict):
        raise ValueError(f"Step at position {position} is not an object.")

    step_type = step.get("type")
    if step_type not in VALID_STEP_TYPES:
        raise ValueError(
            f"Step {position}: unknown type '{step_type}'. "
            f"Valid types: {sorted(VALID_STEP_TYPES)}"
        )

    if step_type == "ERROR_REQUEST":
        raise ValueError(
            "ERROR_REQUEST step found during regular validation; "
            "it should have been intercepted earlier."
        )

    if not isinstance(step.get("params"), dict):
        raise ValueError(f"Step {position} ({step_type}): 'params' must be an object.")

    params = step["params"]
    col_count = len(sheet_schema.get("columns", []))

    if step_type == "APPLY_FORMULA":
        _require_keys(params, ["target", "formula"], step_type, position)
        target = params["target"]
        if not isinstance(target, dict) or "row" not in target or "col" not in target:
            raise ValueError(f"Step {position}: APPLY_FORMULA target must have row and col.")
        _check_formula_target_column(
            target["col"], sheet_schema, step_type, position
        )
        if not isinstance(params["formula"], str) or not params["formula"].startswith("="):
            raise ValueError(f"Step {position}: formula must be a string starting with '='.")

    elif step_type in ("INSERT_COLUMN", "DELETE_COLUMN"):
        _require_keys(params, ["index"], step_type, position)
        _check_col_index(params["index"], col_count, step_type, position)

    elif step_type == "INSERT_ROW":
        _require_keys(params, ["index"], step_type, position)

    elif step_type == "FILL_SERIES":
        _require_keys(params, ["source", "range"], step_type, position)

    elif step_type == "SET_COLUMN_NAME":
        _require_keys(params, ["to_header", "column_locator"], step_type, position)
        if not isinstance(params.get("to_header"), str):
            raise ValueError(f"Step {position}: SET_COLUMN_NAME to_header must be a string.")

    elif step_type == "APPLY_HIGHLIGHT":
        _require_keys(params, ["color", "scope", "target"], step_type, position)
        scope = params.get("scope", "")
        if scope not in ("CELL", "ROW", "COLUMN", "RANGE"):
            raise ValueError(f"Step {position}: APPLY_HIGHLIGHT scope '{scope}' is invalid.")
        condition = params.get("condition")
        if condition is not None:
            _validate_highlight_condition(condition, step_type, position)

    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "type": step_type,
        "seq": int(step.get("seq", position)),
        "disabled": False,
        "params": _to_executor_convention(step_type, params),
        "createdAt": now,
    }


def _to_executor_convention(step_type: str, params: dict) -> dict:
    """
    Gemini (per SYSTEM_PROMPT) emits 0-based row/col/index values. The
    execution engine (WorkflowPatternService._execute_one_step in
    services.py) was built for the legacy grid-edit-recording flow, which
    always converts 0-based grid coordinates to 1-based before building a
    step (see handleFormulaCommit / header-menu insert handlers in
    page.tsx), and subtracts 1 back off internally. Convert here, once, so
    both step sources share the same executor without touching it.
    """
    p = dict(params)

    def inc(d: dict, key: str) -> None:
        if isinstance(d, dict) and isinstance(d.get(key), int):
            d[key] = d[key] + 1

    if step_type == "APPLY_FORMULA":
        target = dict(p.get("target") or {})
        inc(target, "row")
        inc(target, "col")
        p["target"] = target

    elif step_type == "FILL_SERIES":
        source = dict(p.get("source") or {})
        inc(source, "row")
        inc(source, "col")
        p["source"] = source
        fill_range = dict(p.get("range") or {})
        for key in ("start_row", "end_row", "start_col", "end_col"):
            inc(fill_range, key)
        p["range"] = fill_range

    elif step_type in ("INSERT_ROW", "INSERT_COLUMN", "DELETE_COLUMN"):
        inc(p, "index")

    elif step_type == "SET_COLUMN_NAME":
        inc(p, "header_row_index")
        column_ref = dict(p.get("column_ref") or {})
        inc(column_ref, "index")
        p["column_ref"] = column_ref

    elif step_type == "APPLY_HIGHLIGHT":
        inc(p, "header_row_index")
        target = dict(p.get("target") or {})
        fallback = dict(target.get("fallback") or {})
        for key in ("row_index", "col_index", "start_row", "end_row", "start_col", "end_col"):
            inc(fallback, key)
        target["fallback"] = fallback
        p["target"] = target

    return p


def _validate_highlight_condition(condition: dict, step_type: str, position: int) -> None:
    if not isinstance(condition, dict):
        raise ValueError(f"Step {position}: condition must be an object.")
    if "column_header" not in condition:
        raise ValueError(f"Step {position}: condition must have 'column_header'.")
    operator = condition.get("operator")
    if operator not in VALID_HIGHLIGHT_OPERATORS:
        raise ValueError(
            f"Step {position}: condition operator '{operator}' is invalid. "
            f"Valid: {sorted(VALID_HIGHLIGHT_OPERATORS)}"
        )
    if "value" not in condition:
        raise ValueError(f"Step {position}: condition must have 'value'.")


def _require_keys(params: dict, keys: list[str], step_type: str, position: int) -> None:
    for key in keys:
        if key not in params:
            raise ValueError(f"Step {position} ({step_type}): missing required param '{key}'.")


def _check_formula_target_column(
    col_index, sheet_schema: dict, step_type: str, position: int
) -> None:
    """Verify an APPLY_FORMULA target column resolves to a real column.

    The instruction may reference a column that Gemini hallucinated (e.g.
    "column D" on a sheet that only has A–C). Rather than a vague "index out of
    range", name the column and list what is actually available. One brand-new
    column immediately after the last one is allowed (``=I+J`` into a fresh
    "column K").
    """
    schema_cols = sheet_schema.get("columns", []) or []
    if not isinstance(col_index, int):
        raise ValueError(
            f"Step {position} ({step_type}): target 'col' must be an integer."
        )

    # No schema to check against — fall back to the permissive bound check.
    if not schema_cols:
        return

    known_indexes = {
        c.get("index") for c in schema_cols if isinstance(c.get("index"), int)
    }
    max_index = max(known_indexes) if known_indexes else len(schema_cols) - 1
    available = [str(c.get("name")) for c in schema_cols if c.get("name")]

    if col_index < 0 or col_index > max_index + 1:
        referenced = next(
            (c.get("name") for c in schema_cols if c.get("index") == col_index),
            f"column index {col_index}",
        )
        raise ValueError(
            f"Step {position}: APPLY_FORMULA references '{referenced}', which "
            f"does not exist in the sheet. Available columns: "
            f"{available or 'none'}."
        )


def _check_col_index(index, col_count: int, step_type: str, position: int) -> None:
    if not isinstance(index, int):
        raise ValueError(f"Step {position} ({step_type}): 'index' must be an integer.")
    if col_count > 0 and not (0 <= index < col_count + 10):
        raise ValueError(
            f"Step {position} ({step_type}): column index {index} is out of range "
            f"(sheet has {col_count} columns)."
        )


def _collapse_repeated_formulas(steps: list, sheet_schema: dict) -> list:
    """
    Detect when Gemini emitted one APPLY_FORMULA per row for the same column
    (ignoring the rule) and collapse them into one seed APPLY_FORMULA at row 1
    followed by a FILL_SERIES that covers the full range.
    """
    row_count = max(1, sheet_schema.get("row_count", 1) - 1)  # data rows

    # Group consecutive APPLY_FORMULA steps that target the same column.
    result: list = []
    i = 0
    while i < len(steps):
        step = steps[i]
        if step.get("type") != "APPLY_FORMULA":
            result.append(step)
            i += 1
            continue

        params = step.get("params") or {}
        target = params.get("target") or {}
        col = target.get("col")

        # Collect a run of APPLY_FORMULA steps on the same column
        run = [step]
        j = i + 1
        while j < len(steps):
            nxt = steps[j]
            if nxt.get("type") != "APPLY_FORMULA":
                break
            nxt_target = (nxt.get("params") or {}).get("target") or {}
            if nxt_target.get("col") != col:
                break
            run.append(nxt)
            j += 1

        if len(run) <= 2:
            # Not worth collapsing — pass through as-is
            result.extend(run)
            i = j
            continue

        # Collapse: keep only the first step (seed at row 1) + add FILL_SERIES
        seed = run[0]
        seed_params = seed.get("params") or {}
        seed_target = seed_params.get("target") or {}
        seed_col = seed_target.get("col", 0)

        last_params = (run[-1].get("params") or {})
        last_target = last_params.get("target") or {}
        end_row = max(int(last_target.get("row", row_count)), row_count)

        logger.info(
            "NL pattern: collapsing %d APPLY_FORMULA steps on col %d into seed+FILL_SERIES",
            len(run), seed_col,
        )

        result.append(seed)
        result.append({
            "type": "FILL_SERIES",
            "seq": seed.get("seq", i) + 1,
            "disabled": False,
            "params": {
                "source": {"row": int(seed_target.get("row", 1)), "col": seed_col},
                "range": {
                    "start_row": int(seed_target.get("row", 1)),
                    "end_row": end_row,
                    "start_col": seed_col,
                    "end_col": seed_col,
                },
            },
        })
        i = j

    # Re-number seq to be consecutive
    for idx, s in enumerate(result):
        s["seq"] = idx

    return result
