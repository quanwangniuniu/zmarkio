"""
Spreadsheet import helpers.

- Create populated Spreadsheet records from agent-uploaded CSV/Excel files.
- Snapshot an imported sheet and diff it against the live sheet for export.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from django.db import transaction

from core.models import Project
from spreadsheet.models import (
    Cell,
    SheetColumn,
    SheetRow,
    Spreadsheet,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)
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

# ---------------------------------------------------------------------------
# Import baseline snapshot (moved unchanged from import_baseline.py)
# ---------------------------------------------------------------------------
def _cell_key(row_position: int, column_position: int) -> str:
    return f'{row_position},{column_position}'

def build_import_baseline(sheet) -> dict:
    row_ids = list(
        SheetRow.objects.filter(sheet=sheet, is_deleted=False)
        .order_by('position')
        .values_list('id', flat=True)
    )
    column_ids = list(
        SheetColumn.objects.filter(sheet=sheet, is_deleted=False)
        .order_by('position')
        .values_list('id', flat=True)
    )

    cells_out = {}
    cells = Cell.objects.filter(
        sheet=sheet,
        is_deleted=False,
        row__is_deleted=False,
        column__is_deleted=False,
    ).select_related('row', 'column')
    for cell in cells:
        raw = cell.raw_input or ''
        if raw == '':
            continue
        cells_out[_cell_key(cell.row.position, cell.column.position)] = {
            'raw_input': raw,
        }

    formats_out = {}
    for fmt in SpreadsheetCellFormat.objects.filter(sheet=sheet, is_deleted=False):
        payload = _normalize_format({field: getattr(fmt, field) for field in DEFAULT_FORMAT})
        if payload == DEFAULT_FORMAT:
            continue
        formats_out[_cell_key(fmt.row_index, fmt.column_index)] = payload

    highlights_out = {'cells': {}, 'rows': {}, 'columns': {}}
    for highlight in SpreadsheetHighlight.objects.filter(sheet=sheet, is_deleted=False):
        color = (highlight.color or '').strip()
        if not color:
            continue
        if (
            highlight.scope == 'CELL'
            and highlight.row_index is not None
            and highlight.col_index is not None
        ):
            highlights_out['cells'][_cell_key(highlight.row_index, highlight.col_index)] = color
        elif highlight.scope == 'ROW' and highlight.row_index is not None:
            highlights_out['rows'][str(highlight.row_index)] = color
        elif highlight.scope == 'COLUMN' and highlight.col_index is not None:
            highlights_out['columns'][str(highlight.col_index)] = color

    return {
        'version': BASELINE_VERSION,
        'row_ids': row_ids,
        'column_ids': column_ids,
        'cells': cells_out,
        'formats': formats_out,
        'highlights': highlights_out,
    }

# ---------------------------------------------------------------------------
# Import diff (moved unchanged from import_diff.py)
# Compare a frozen import baseline with the live web sheet.
# ---------------------------------------------------------------------------

BASELINE_VERSION = 1

DEFAULT_FORMAT = {
    'bold': False,
    'italic': False,
    'strikethrough': False,
    'text_color': None,
    'font_family': None,
    'font_size': None,
    'number_format': None,
}


def _result(
    *,
    requires_rebuild,
    rebuild_reasons,
    has_changes,
    cells=None,
    formats=None,
    cell_highlights=None,
):
    return {
        'requires_rebuild': requires_rebuild,
        'rebuild_reasons': list(rebuild_reasons),
        'has_changes': has_changes,
        'cells': cells or {},
        'formats': formats or {},
        'highlights': {'cells': cell_highlights or {}},
    }


def _rebuild(source, reasons):
    """Return a rebuild result and log why, so the fallback is never silent."""
    reasons = list(reasons)
    logger.info(
        'Import export falls back to rebuild for sheet_id=%s source_id=%s reasons=%s',
        getattr(source, 'sheet_id', None),
        getattr(source, 'id', None),
        ','.join(reasons),
    )
    return _result(
        requires_rebuild=True,
        rebuild_reasons=reasons,
        has_changes=True,
    )


def _id_prefix_status(baseline_ids, current_ids):
    """Return same, append, or unsafe for one id list."""
    baseline_ids = [int(value) for value in (baseline_ids or [])]
    current_ids = [int(value) for value in (current_ids or [])]
    if current_ids[:len(baseline_ids)] != baseline_ids:
        return 'unsafe'
    if len(current_ids) > len(baseline_ids):
        return 'append'
    return 'same'


def _in_original_range(key, row_count, col_count):
    """True when "row,col" is inside the baseline grid."""
    try:
        row_text, col_text = str(key).split(',', 1)
        row = int(row_text)
        col = int(col_text)
    except (TypeError, ValueError):
        return False
    return 0 <= row < row_count and 0 <= col < col_count


def _raw(cells, key):
    """Missing key, None, and '' are all empty."""
    entry = (cells or {}).get(key)
    if not isinstance(entry, dict):
        return ''
    raw = entry.get('raw_input')
    return '' if raw is None else str(raw)


def diff_cells(baseline_cells, current_cells, row_count, col_count):
    """Return ({key: {'raw_input': value_or_None}}, outside_original_range)."""
    changes = {}
    outside = False
    keys = set(baseline_cells or {}) | set(current_cells or {})
    for key in keys:
        before = _raw(baseline_cells, key)
        after = _raw(current_cells, key)
        if before == after:
            continue
        if not _in_original_range(key, row_count, col_count):
            outside = True
            continue
        changes[key] = {'raw_input': None if after == '' else after}
    return changes, outside


def _normalize_format_value(field, value):
    if field in ('bold', 'italic', 'strikethrough'):
        return bool(value)
    if field in ('text_color', 'font_family'):
        if value is None:
            return None
        text = str(value).strip()
        return text or None
    if field == 'font_size':
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value
    if field == 'number_format':
        return dict(value) if isinstance(value, dict) else None
    return value


def _normalize_format(raw) -> dict:
    """Return every format field, cleaned. A missing entry or field is the default."""
    payload = dict(DEFAULT_FORMAT)
    if isinstance(raw, dict):
        for field in DEFAULT_FORMAT:
            if field in raw:
                payload[field] = _normalize_format_value(field, raw[field])
    return payload


def diff_formats(baseline_formats, current_formats, row_count, col_count):
    """Return only the format fields that changed."""
    changes = {}
    outside = False
    keys = set(baseline_formats or {}) | set(current_formats or {})
    for key in keys:
        before = _normalize_format((baseline_formats or {}).get(key))
        after = _normalize_format((current_formats or {}).get(key))
        if before == after:
            continue
        if not _in_original_range(key, row_count, col_count):
            outside = True
            continue
        field_changes = {
            field: after[field]
            for field in DEFAULT_FORMAT
            if before[field] != after[field]
        }
        if field_changes:
            changes[key] = field_changes
    return changes, outside


def _highlight_color(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def diff_cell_highlights(baseline_highlights, current_highlights, row_count, col_count):
    """Cell fills only. None means clear."""
    before_cells = (baseline_highlights or {}).get('cells') or {}
    after_cells = (current_highlights or {}).get('cells') or {}
    changes = {}
    outside = False
    keys = set(before_cells) | set(after_cells)
    for key in keys:
        before = _highlight_color(before_cells.get(key))
        after = _highlight_color(after_cells.get(key))
        if before == after:
            continue
        if not _in_original_range(key, row_count, col_count):
            outside = True
            continue
        changes[key] = after
    return changes, outside


def _highlight_map_changed(before, after):
    before = before or {}
    after = after or {}
    keys = set(before) | set(after)
    for key in keys:
        if _highlight_color(before.get(key)) != _highlight_color(after.get(key)):
            return True
    return False


def diff_sheet(source):
    """Compare source.baseline with a fresh build_import_baseline(source.sheet).

    Any insert, delete, or reorder of an original row or column changes the
    id order and forces a rebuild. Undone operations restore the original ids,
    so they stay on the patch path.
    """
    baseline = getattr(source, 'baseline', None)
    if not isinstance(baseline, dict):
        return _rebuild(source, ['missing_baseline'])
    if baseline.get('version') != BASELINE_VERSION:
        return _rebuild(source, ['unsupported_baseline_version'])

    current = build_import_baseline(source.sheet)
    row_status = _id_prefix_status(baseline.get('row_ids'), current.get('row_ids'))
    column_status = _id_prefix_status(baseline.get('column_ids'), current.get('column_ids'))

    reasons = []
    if row_status == 'unsafe':
        reasons.append('row_structure_changed')
    if column_status == 'unsafe':
        reasons.append('column_structure_changed')

    baseline_highlights = baseline.get('highlights') or {}
    current_highlights = current.get('highlights') or {}
    if _highlight_map_changed(baseline_highlights.get('rows'), current_highlights.get('rows')):
        reasons.append('row_highlight_changed')
    if _highlight_map_changed(baseline_highlights.get('columns'), current_highlights.get('columns')):
        reasons.append('column_highlight_changed')

    row_count = len(baseline.get('row_ids') or [])
    col_count = len(baseline.get('column_ids') or [])
    cell_changes, cell_outside = diff_cells(
        baseline.get('cells'), current.get('cells'), row_count, col_count,
    )
    format_changes, format_outside = diff_formats(
        baseline.get('formats'), current.get('formats'), row_count, col_count,
    )
    highlight_changes, highlight_outside = diff_cell_highlights(
        baseline_highlights, current_highlights, row_count, col_count,
    )
    if cell_outside or format_outside or highlight_outside:
        reasons.append('appended_region_change')

    if reasons:
        return _rebuild(source, reasons)

    has_changes = bool(cell_changes or format_changes or highlight_changes)
    return _result(
        requires_rebuild=False,
        rebuild_reasons=[],
        has_changes=has_changes,
        cells=cell_changes,
        formats=format_changes,
        cell_highlights=highlight_changes,
    )


