"""
Spreadsheet import helpers.

- Create populated Spreadsheet records from agent-uploaded CSV/Excel files.
- Snapshot an imported sheet and diff it against the live sheet for export.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import zipfile
from typing import Any

from django.db import transaction
from django.db.models import Max
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.xml.functions import fromstring
from rest_framework.exceptions import ValidationError

from core.models import Project
from spreadsheet.cell_format import CURRENCY_LOCALES, FONT_SIZES, IMPORT_FONT_FAMILIES
from spreadsheet.models import (
    Cell,
    SheetColumn,
    SheetRow,
    SheetStructureOperation,
    Spreadsheet,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)
from spreadsheet.serializers import SpreadsheetCellFormatOpSerializer
from spreadsheet.services import CellService, SheetService, SpreadsheetService
from spreadsheet.structure_replay import replay_baseline_structure

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


def _structure_operation_checkpoint(sheet) -> int:
    latest = (
        SheetStructureOperation.objects.filter(sheet=sheet)
        .aggregate(latest=Max('id'))
        .get('latest')
    )
    return int(latest or 0)


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
        'structure_operation_checkpoint': _structure_operation_checkpoint(sheet),
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
    structural_operations=None,
):
    return {
        'requires_rebuild': requires_rebuild,
        'rebuild_reasons': list(rebuild_reasons),
        'has_changes': has_changes,
        'cells': cells or {},
        'formats': formats or {},
        'highlights': {'cells': cell_highlights or {}},
        'structural_operations': list(structural_operations or []),
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


def _has_replay_operations(sheet, baseline):
    checkpoint = baseline.get('structure_operation_checkpoint')
    if isinstance(checkpoint, bool) or not isinstance(checkpoint, int) or checkpoint < 0:
        return False
    return SheetStructureOperation.objects.filter(
        sheet=sheet,
        id__gt=checkpoint,
        is_reverted=False,
    ).exists()

def diff_sheet(source):
    """Compare source.baseline with a fresh build_import_baseline(source.sheet).

    Insert and delete operations recorded after the import are replayed onto the
    baseline first, so their row/column moves are not reported as edits and the
    exporters can repeat them on the original file. A sort, a reorder, or any
    replay mismatch still forces a rebuild. Undone operations are skipped.
    """
    baseline = getattr(source, 'baseline', None)
    if not isinstance(baseline, dict):
        return _rebuild(source, ['missing_baseline'])
    if baseline.get('version') != BASELINE_VERSION:
        return _rebuild(source, ['unsupported_baseline_version'])

    current = build_import_baseline(source.sheet)
    working = baseline
    structural_operations = []
    row_status = _id_prefix_status(working.get('row_ids'), current.get('row_ids'))
    column_status = _id_prefix_status(working.get('column_ids'), current.get('column_ids'))
    structure_changed = row_status == 'unsafe' or column_status == 'unsafe'
    appended = row_status == 'append' or column_status == 'append'
    if structure_changed or appended:
        replay = replay_baseline_structure(
            source.sheet,
            baseline,
            current.get('row_ids'),
            current.get('column_ids'),
        )
        if replay.get('safe') is True:
            working = {
                'row_ids': replay['row_ids'],
                'column_ids': replay['column_ids'],
                'cells': replay['cells'],
                'formats': replay['formats'],
                'highlights': replay['highlights'],
            }
            structural_operations = list(replay.get('structural_operations') or [])
            row_status = _id_prefix_status(working['row_ids'], current.get('row_ids'))
            column_status = _id_prefix_status(working['column_ids'], current.get('column_ids'))
        elif appended and _has_replay_operations(source.sheet, baseline):
            if row_status == 'append':
                row_status = 'unsafe'
            if column_status == 'append':
                column_status = 'unsafe'

    reasons = []
    if row_status == 'unsafe':
        reasons.append('row_structure_changed')
    if column_status == 'unsafe':
        reasons.append('column_structure_changed')

    baseline_highlights = working.get('highlights') or {}
    current_highlights = current.get('highlights') or {}
    if _highlight_map_changed(baseline_highlights.get('rows'), current_highlights.get('rows')):
        reasons.append('row_highlight_changed')
    if _highlight_map_changed(baseline_highlights.get('columns'), current_highlights.get('columns')):
        reasons.append('column_highlight_changed')

    row_count = len(working.get('row_ids') or [])
    col_count = len(working.get('column_ids') or [])
    cell_changes, cell_outside = diff_cells(
        working.get('cells'), current.get('cells'), row_count, col_count,
    )
    format_changes, format_outside = diff_formats(
        working.get('formats'), current.get('formats'), row_count, col_count,
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
        structural_operations=structural_operations,
    )

# ---------------------------------------------------------------------------
# Excel import styles (server-side port of the browser's xlsx_style_import.ts)
# ---------------------------------------------------------------------------

# Excel theme colour index -> slot name in xl/theme/theme1.xml
_THEME_SLOTS = (
    'lt1', 'dk1', 'lt2', 'dk2',
    'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6',
    'hlink', 'folHlink',
)
# '[$$-409]' -> '$-409' -> 'USD', and '$' -> 'USD'
_CURRENCY_BY_LOCALE = {prefix[2:-1]: code for code, prefix in CURRENCY_LOCALES.items()}
_CURRENCY_BY_SYMBOL = {locale.split('-')[0]: code for locale, code in _CURRENCY_BY_LOCALE.items()}
_CURRENCY_SYMBOL_ORDER = ('£', '€', '¥', '$')
_BRACKET_CURRENCY = re.compile(r'\[\$([^\]]*)\]')
_LOCALE_SPLIT = re.compile(r'^(.*)-([0-9A-Fa-f]+)$')
_QUOTED = re.compile(r'"([^"]*)"')
_UNDEFINED = object()  # a JavaScript `undefined` result in the ported lookups


def _local(tag):
    return tag.rsplit('}', 1)[-1]


def _number(text):
    """JavaScript Number(text) for an XML attribute: blank -> 0, invalid -> None."""
    text = (text or '').strip()
    if not text:
        return 0.0
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _item(items, index):
    """items[index] as JavaScript reads it: a missing or fractional index is undefined."""
    if index is None or index != int(index) or not 0 <= index < len(items):
        return None
    return items[int(index)]


def _excel_theme_palette(archive):
    """Port of readThemePalette: theme colours in Excel's theme-index order."""
    try:
        root = fromstring(archive.read('xl/theme/theme1.xml'))
    except KeyError:
        return []
    scheme = next((el for el in root.iter() if _local(el.tag) == 'clrScheme'), None)
    if scheme is None:
        return []
    slots = {}
    for slot in scheme:
        value = None
        for node in slot:
            if _local(node.tag) == 'srgbClr':
                value = node.get('val')
            elif _local(node.tag) == 'sysClr':
                value = node.get('lastClr')
        slots[_local(slot.tag)] = value.upper() if value else None
    return [slots.get(name) for name in _THEME_SLOTS]


def _excel_default_font_size(archive):
    """Port of readWorkbookDefaultFontSize: the Normal style's font size, or None."""
    try:
        root = fromstring(archive.read('xl/styles.xml'))
    except KeyError:
        return None
    elements = list(root.iter())
    fonts = next((el for el in elements if _local(el.tag) == 'fonts'), None)
    font_elements = [el for el in fonts.iter() if _local(el.tag) == 'font'] if fonts is not None else []
    if not font_elements:
        return None
    xf_id = next(
        (
            _number(el.get('xfId') or '0')
            for el in elements
            if _local(el.tag) == 'cellStyle' and el.get('name') == 'Normal'
        ),
        0.0,
    )
    style_xfs = next((el for el in elements if _local(el.tag) == 'cellStyleXfs'), None)
    xfs = [el for el in style_xfs.iter() if _local(el.tag) == 'xf'] if style_xfs is not None else []
    xf = _item(xfs, xf_id)
    font_id = _number(xf.get('fontId') or '0') if xf is not None else 0.0

    def sz_value(font):
        sz = next((el for el in font.iter() if _local(el.tag) == 'sz'), None) if font is not None else None
        return _UNDEFINED if sz is None else sz.get('val')  # None is JavaScript null

    value = sz_value(_item(font_elements, font_id))
    if value is _UNDEFINED or value is None:
        value = sz_value(font_elements[0])
    if value is _UNDEFINED:
        return None
    return 0.0 if value is None else _number(value)


def _apply_tint(hex6, tint):
    """Port of the browser's colourHsl / hslToHex / applyTint, so the result is identical."""
    if not tint:
        return hex6
    red, green, blue = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    high, low = max(red, green, blue), min(red, green, blue)
    hue = sat = 0.0
    light = (high + low) / 2
    if high != low:
        delta = high - low
        sat = delta / (2 - high - low) if light > 0.5 else delta / (high + low)
        if high == red:
            hue = (green - blue) / delta + (6 if green < blue else 0)
        elif high == green:
            hue = (blue - red) / delta + 2
        else:
            hue = (red - green) / delta + 4
        hue *= 60
    light = light * (1 + tint) if tint < 0 else light * (1 - tint) + tint
    light = min(1, max(0, light))
    if sat == 0:
        channels = (light, light, light)
    else:
        q = light * (1 + sat) if light < 0.5 else light + sat - light * sat
        p = 2 * light - q

        def hue_to_rgb(t):
            if t < 0:
                t += 1
            if t > 1:
                t -= 1
            if t < 1 / 6:
                return p + (q - p) * 6 * t
            if t < 1 / 2:
                return q
            if t < 2 / 3:
                return p + (q - p) * (2 / 3 - t) * 6
            return p

        k = hue / 360
        channels = (hue_to_rgb(k + 1 / 3), hue_to_rgb(k), hue_to_rgb(k - 1 / 3))
    return ''.join(f'{math.floor(channel * 255 + 0.5):02X}' for channel in channels)


def _excel_hex(color, palette):
    """Port of resolveExcelColor + argbToHex: '#RRGGBB', or None (also for exact black/white)."""
    if color is None:
        return None
    if color.type == 'rgb':
        argb = color.rgb
    elif color.type == 'theme':
        theme = color.theme
        base = palette[theme] if isinstance(theme, int) and 0 <= theme < len(palette) else None
        if not base:
            return None
        argb = 'FF' + _apply_tint(base, color.tint or 0)
    else:
        return None
    if not isinstance(argb, str):
        return None
    rgb = argb[-6:].upper()
    return None if rgb in ('000000', 'FFFFFF') else f'#{rgb}'


def _nearest_font_size(size):
    """Port of nearestSize: snap to the nearest editor size; a tie keeps the larger size."""
    if size is None:
        return None
    best = FONT_SIZES[0]
    for candidate in FONT_SIZES[1:]:
        if not abs(best - size) < abs(candidate - size):
            best = candidate
    return best


def _import_number_format(code):
    """Port of parseNumberFormat: Excel format code -> web number_format, or None."""
    if not code or code == 'General':
        return None
    if '%' in code:
        places = code.count('0') - 1 if '.' in code else 0
        return {'type': 'PERCENT', 'currency_code': None, 'decimal_places': places}
    fraction = code.split('.')[-1] if '.' in code else ''
    places = fraction.count('0')
    bracket = _BRACKET_CURRENCY.search(code)
    if bracket:
        body = bracket.group(1)
        split = _LOCALE_SPLIT.match(body)
        symbol = (split.group(1) if split else body).strip()
        locale = split.group(2).upper() if split else None
        if locale:
            currency = _CURRENCY_BY_LOCALE.get(f'{symbol}-{locale}')
        else:
            currency = _CURRENCY_BY_SYMBOL.get(symbol) or (
                symbol.upper() if symbol.upper() in CURRENCY_LOCALES else None
            )
        if not currency:
            return None
        return {'type': 'CURRENCY', 'currency_code': currency, 'decimal_places': places}
    quoted = _QUOTED.search(code)
    if quoted and re.fullmatch(r'[A-Za-z]{3}', quoted.group(1).strip()):
        token = quoted.group(1).strip().upper()
        if token not in CURRENCY_LOCALES:
            return None
        return {'type': 'CURRENCY', 'currency_code': token, 'decimal_places': places}
    symbol = next((s for s in _CURRENCY_SYMBOL_ORDER if s in code), None)
    if symbol:
        return {'type': 'CURRENCY', 'currency_code': _CURRENCY_BY_SYMBOL[symbol], 'decimal_places': places}
    if '0' in code:
        return {'type': 'NUMBER', 'currency_code': None, 'decimal_places': places}
    return None


def _read_excel_import_styles(content, worksheet_name):
    """Convert one worksheet's styles to validated web format ops and cell fills."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            palette = _excel_theme_palette(archive)
            default_size = _nearest_font_size(_excel_default_font_size(archive))
        workbook = load_workbook(io.BytesIO(content))
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, SyntaxError, TypeError, ValueError) as exc:
        raise ValidationError({'file': 'The Excel file could not be read.'}) from exc
    if worksheet_name not in workbook.sheetnames:
        raise ValidationError({'worksheet_name': 'This worksheet is not in the Excel file.'})

    format_ops = []
    highlights = []
    for row in workbook[worksheet_name].iter_rows():
        for cell in row:
            if isinstance(cell, MergedCell):
                continue
            has_value = cell.value is not None
            styled = cell.has_style
            if not has_value and not styled:
                continue
            # ExcelJS resolves no style for a cell on style 0: it has no font, fill or
            # number format of its own, and a value cell gets the Normal size.
            font = cell.font if styled else None
            bold = bool(font is not None and font.bold)
            italic = bool(font is not None and font.italic)
            strike = bool(font is not None and font.strike)
            text_color = _excel_hex(font.color, palette) if font is not None else None
            fill = cell.fill if styled else None
            fill_color = (
                _excel_hex(getattr(fill, 'fgColor', None), palette)
                if getattr(fill, 'fill_type', None) == 'solid'
                else None
            )
            family = (
                IMPORT_FONT_FAMILIES.get((font.name or '').strip().lower())
                if font is not None
                else None
            )
            number_format = _import_number_format(cell.number_format) if styled else None
            if not has_value and not (
                bold or italic or strike or text_color or family or number_format or fill_color
            ):
                continue
            reported_size = _nearest_font_size(font.size) if font is not None else None
            size = (reported_size or default_size) if has_value else None
            if bold or italic or strike or text_color or family or size or number_format:
                format_ops.append({
                    'row': cell.row - 1,
                    'column': cell.column - 1,
                    'bold': bold,
                    'italic': italic,
                    'strikethrough': strike,
                    'text_color': text_color,
                    'font_family': family,
                    'font_size': size,
                    'number_format': number_format,
                })
            if fill_color:
                highlights.append((cell.row - 1, cell.column - 1, fill_color))

    # Same validation and stored values as the batch format endpoint.
    serializer = SpreadsheetCellFormatOpSerializer(data=format_ops, many=True)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data, highlights


def _format_row(op):
    """The stored SpreadsheetCellFormat values for one validated op."""
    return {
        'bold': op['bold'],
        'italic': op['italic'],
        'strikethrough': op['strikethrough'],
        'text_color': op.get('text_color') or None,
        'font_family': op.get('font_family') or None,
        'font_size': op.get('font_size'),
        'number_format': dict(op['number_format']) if op.get('number_format') else None,
    }

def apply_excel_import_styles(sheet, content, worksheet_name):
    """Convert one worksheet's styles from the uploaded .xlsx and write them.

    Formats and fills are upserted for the cells the workbook formats; other
    existing formats and fills are left as they are, as the browser import did.
    Raises ValidationError when the file, worksheet or a converted value is invalid.
    """
    try:
        format_ops, highlights = _read_excel_import_styles(content, worksheet_name)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise ValidationError({'file': 'The Excel formatting could not be read.'}) from exc
    for op in format_ops:
        SpreadsheetCellFormat.objects.update_or_create(
            sheet=sheet,
            row_index=op['row'],
            column_index=op['column'],
            defaults={**_format_row(op), 'is_deleted': False},
        )
    for row_index, col_index, color in highlights:
        SpreadsheetHighlight.objects.update_or_create(
            sheet=sheet,
            scope='CELL',
            row_index=row_index,
            col_index=col_index,
            defaults={'spreadsheet': sheet.spreadsheet, 'color': color},
        )