"""
Create populated Spreadsheet records from agent-uploaded CSV/Excel files.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from django.db import transaction

from core.models import Project
from spreadsheet.models import Spreadsheet
from spreadsheet.services import CellService, SheetService, SpreadsheetService

logger = logging.getLogger(__name__)

CHUNK_SIZE = 2000


def _spreadsheet_name_from_filename(original_filename: str) -> str:
    base = os.path.splitext(os.path.basename(original_filename))[0].strip()
    return (base or "Imported spreadsheet")[:200]


def _dedupe_spreadsheet_name(project: Project, base_name: str) -> str:
    name = base_name[:200]
    if not Spreadsheet.objects.filter(project=project, name=name, is_deleted=False).exists():
        return name
    counter = 1
    while True:
        suffix = f" ({counter})"
        candidate = f"{base_name[: max(1, 200 - len(suffix))]}{suffix}"
        if not Spreadsheet.objects.filter(
            project=project, name=candidate, is_deleted=False
        ).exists():
            return candidate
        counter += 1


def _dedupe_sheet_name(spreadsheet: Spreadsheet, base_name: str) -> str:
    name = (base_name or "Sheet1")[:200]
    if not spreadsheet.sheets.filter(name=name, is_deleted=False).exists():
        return name
    counter = 1
    while True:
        suffix = f" ({counter})"
        candidate = f"{name[: max(1, 200 - len(suffix))]}{suffix}"
        if not spreadsheet.sheets.filter(name=candidate, is_deleted=False).exists():
            return candidate
        counter += 1


def _cell_to_raw(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _build_set_operation(row: int, col: int, value: Any) -> dict[str, Any]:
    return {
        "operation": "set",
        "row": row,
        "column": col,
        "raw_input": _cell_to_raw(value),
    }


def _operations_from_sheet_data(columns: list, rows: list) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    columns = list(columns or [])
    for col_idx, header in enumerate(columns):
        ops.append(_build_set_operation(0, col_idx, header))
    for row_idx, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        for col_idx, col_name in enumerate(columns):
            ops.append(_build_set_operation(row_idx + 1, col_idx, row.get(col_name, "")))
    return ops


def _finalize_sheet_import(sheet) -> None:
    try:
        CellService.recalculate_sheet_formulas(sheet)
    except Exception:
        logger.exception("Import finalize recalc failed for sheet_id=%s", sheet.id)
        raise
    try:
        from spreadsheet.pivot_service import recompute_pivots_for_source_sheet

        recompute_pivots_for_source_sheet(sheet)
    except Exception:
        logger.exception("Import finalize pivot recompute failed for sheet_id=%s", sheet.id)


def _populate_sheet(sheet, columns: list, rows: list) -> None:
    operations = _operations_from_sheet_data(columns, rows)
    if not operations:
        return

    row_count = max(1, 1 + len(rows or []))
    col_count = max(1, len(columns or []))
    SheetService.resize_sheet(sheet, row_count, col_count)

    for chunk_index in range(0, len(operations), CHUNK_SIZE):
        chunk = operations[chunk_index : chunk_index + CHUNK_SIZE]
        CellService.batch_update_cells(
            sheet,
            chunk,
            auto_expand=False,
            import_mode=True,
        )

    _finalize_sheet_import(sheet)


@transaction.atomic
def create_spreadsheet_from_upload(
    *,
    project: Project,
    parsed: dict[str, Any],
    original_filename: str,
) -> dict[str, Any]:
    """Create a populated Spreadsheet in the project from an already-parsed upload.

    ``parsed`` is the ``core.services.file_parser.parse_file_to_json`` output
    ``{"name": str, "sheets": [{"name", "columns", "rows"}], ...}``. Parsing (and
    its hard row/column/cell caps) happens in the caller, not here, so this
    module has no dependency on the agent's file-parsing layer.
    """
    sheets_data = (parsed or {}).get("sheets") or []
    if not sheets_data:
        raise ValueError("No sheet data found in uploaded file.")

    base_name = _spreadsheet_name_from_filename(original_filename)
    spreadsheet = SpreadsheetService.create_spreadsheet(
        project=project,
        name=_dedupe_spreadsheet_name(project, base_name),
    )

    first_sheet_id: int | None = None
    for idx, sheet_data in enumerate(sheets_data):
        raw_sheet_name = sheet_data.get("name") or f"Sheet{idx + 1}"
        sheet_name = _dedupe_sheet_name(spreadsheet, raw_sheet_name)
        sheet = SheetService.create_sheet(spreadsheet, sheet_name)
        _populate_sheet(
            sheet,
            sheet_data.get("columns") or [],
            sheet_data.get("rows") or [],
        )
        if first_sheet_id is None:
            first_sheet_id = sheet.id

    return {
        "spreadsheet_id": spreadsheet.id,
        "sheet_id": first_sheet_id,
        "project_id": project.id,
        "url": f"/spreadsheets/{spreadsheet.id}?project_id={project.id}",
        "name": spreadsheet.name,
    }
