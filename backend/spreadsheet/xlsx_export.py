"""Excel (.xlsx) export for a sheet.

build_sheet_workbook rebuilds a workbook from the web sheet: cell values,
formats, highlights, and a native ``openpyxl`` ``LineChart`` for every
``=SPARKLINE(range)`` cell (MED-295).

build_patched_workbook patches a copy of an imported original workbook with a
diff, so formatting the web editor does not support is kept. It does not catch
its own failures; the export view decides whether to fall back to a rebuild.
"""
import io
import re
from copy import copy
from typing import Any, List, Optional

from django.db.models import Q
from openpyxl import Workbook, load_workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.styles.colors import Color
from rest_framework.exceptions import ValidationError

from .models import (
    Cell,
    ComputedCellType,
    SheetColumn,
    SheetRow,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)
from .sparkline import is_sparkline, parse_sparkline
from .cell_format import (
    CURRENCY_LOCALES,
    FormatStrategy,
    font_name,
    number_pattern,
    percent_pattern,
)

# Excel forbids these characters in a worksheet title, and caps it at 31 chars.
_INVALID_TITLE_CHARS = re.compile(r'[\\/?*\[\]:]')


def _safe_sheet_title(name: Optional[str]) -> str:
    """A worksheet title Excel/openpyxl will accept for any user-entered name."""
    cleaned = _INVALID_TITLE_CHARS.sub(' ', (name or '').strip())
    return cleaned[:31] or 'Sheet1'


def _cell_export_value(cell: Cell) -> Optional[Any]:
    """Effective value to write for a (non-sparkline) cell."""
    if cell.computed_type == ComputedCellType.NUMBER and cell.computed_number is not None:
        return float(cell.computed_number)
    if cell.number_value is not None:
        return float(cell.number_value)
    if cell.computed_type == ComputedCellType.STRING and cell.computed_string is not None:
        return cell.computed_string
    if cell.string_value is not None:
        return cell.string_value
    if cell.boolean_value is not None:
        return cell.boolean_value
    return None

# converts spreadsheet colour to excel rgb
def _excel_rgb(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    hex_color = value.strip().lstrip('#').upper()
    if len(hex_color) != 6:
        return None
    return 'FF' + hex_color

# formula text for a formula cell, otherwise the cell's export value
def _excel_value(cell: Cell) -> Optional[Any]:
    raw = (cell.raw_input or '').strip()
    if raw.startswith('='):
        return raw
    return _cell_export_value(cell)


# converts spreadsheet font to excel font (including features)
def _excel_font(fmt) -> Optional[Font]:
    """Rebuild: a new Font with the web fields that are set and supported, or None."""
    fields = {
        field: getattr(fmt, field)
        for field in _ExcelFont.strategies
        if getattr(fmt, field)
    }
    if not _excel_rgb(fields.get('text_color')):
        fields.pop('text_color', None)
    font = Font()
    return font if _ExcelFont.apply(font, fields) else None

# one Excel pattern per web number-format type
_EXCEL_NUMBER_PATTERNS = {
    'NUMBER': lambda number_format, places: number_pattern(places),
    'PERCENT': lambda number_format, places: percent_pattern(places),
    'CURRENCY': lambda number_format, places: (
        (CURRENCY_LOCALES.get(number_format.get('currency_code') or 'USD') or '')
        + number_pattern(places)
    ),
}


# converts spreadsheet number format to excel number format
def _excel_number_format(number_format) -> Optional[str]:
    if not isinstance(number_format, dict):
        return None
    kind = number_format.get('type')
    if kind in (None, '', 'GENERAL'):
        return None
    places = number_format.get('decimal_places')
    places = 2 if places is None else max(0, min(10, int(places)))
    build = _EXCEL_NUMBER_PATTERNS.get(kind) if isinstance(kind, str) else None
    return build(number_format, places) if build else None


# applies a fill/highlight to a cell
def _apply_fill(cell, color: str) -> None:
    rgb = _excel_rgb(color)
    if rgb:
        cell.fill = PatternFill(fill_type='solid', fgColor=rgb)



# 0-based (row, col) cells each highlight scope fills. The order is the
# precedence order: build_sheet_workbook applies COLUMN, then ROW, then CELL.
_HIGHLIGHT_TARGETS = {
    'COLUMN': lambda h, max_row, max_col: (
        ((row, h.col_index) for row in range(max_row + 1))
        if h.col_index is not None else ()
    ),
    'ROW': lambda h, max_row, max_col: (
        ((h.row_index, col) for col in range(max_col + 1))
        if h.row_index is not None else ()
    ),
    'CELL': lambda h, max_row, max_col: (
        ((h.row_index, h.col_index),)
        if h.row_index is not None and h.col_index is not None else ()
    ),
}

# calculates the maximum row and column used in the sheet
def _used_bounds(cells, formats, highlights) -> tuple[int, int]:
    rows, cols = [], []
    for cell in cells:
        rows.append(cell.row.position)
        cols.append(cell.column.position)
    for fmt in formats:
        rows.append(fmt.row_index)
        cols.append(fmt.column_index)
    for highlight in highlights:
        if highlight.row_index is not None:
            rows.append(highlight.row_index)
        if highlight.col_index is not None:
            cols.append(highlight.col_index)
    if not rows or not cols:
        return -1, -1
    return max(rows), max(cols)


def build_sheet_workbook(sheet) -> bytes:
    """Serialize a sheet to .xlsx bytes: cell values + native charts for sparklines."""
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = _safe_sheet_title(sheet.name)

    cells = (
        Cell.objects.filter(
            sheet=sheet, is_deleted=False,
            row__is_deleted=False, column__is_deleted=False,
        )
        .select_related('row', 'column')
    )

    sparkline_cells: List[Cell] = []
    for cell in cells:
        # openpyxl is 1-based; our positions are 0-based.
        row = cell.row.position + 1
        col = cell.column.position + 1
        if is_sparkline(cell.raw_input):
            sparkline_cells.append(cell)
            continue  # the chart is added below; never write its JSON payload
            
        value = _excel_value(cell)
        if value is not None:
            worksheet.cell(row=row, column=col, value=value)
    # apply formats and highlights to the sheet
    formats = list(SpreadsheetCellFormat.objects.filter(sheet=sheet, is_deleted=False))
    highlights = list(SpreadsheetHighlight.objects.filter(sheet=sheet, is_deleted=False))
    max_row, max_col = _used_bounds(cells, formats, highlights)
    if max_col >= 0:
        for col_index in range(max_col + 1):
            worksheet.column_dimensions[get_column_letter(col_index + 1)].width = 16
    if max_row >= 0:
        for row_index in range(max_row + 1):
            worksheet.row_dimensions[row_index + 1].height = 18
    
    # COLUMN, then ROW, then CELL, so the more specific fill wins
    for scope, targets in _HIGHLIGHT_TARGETS.items():
        for highlight in highlights:
            if highlight.scope != scope:
                continue
            for row_index, col_index in targets(highlight, max_row, max_col):
                _apply_fill(
                    worksheet.cell(row=row_index + 1, column=col_index + 1),
                    highlight.color,
                )

    # apply formats to the sheet
    for fmt in formats:
        cell = worksheet.cell(row=fmt.row_index + 1, column=fmt.column_index + 1)
        font = _excel_font(fmt)
        if font is not None:
            cell.font = font
        number_format = _excel_number_format(fmt.number_format)
        if number_format:
            cell.number_format = number_format

    for cell in sparkline_cells:
        try:
            spec = parse_sparkline(cell.raw_input)
        except ValidationError:
            continue  # a bad spec exports as an empty cell, not a crash
        (r0, c0), (r1, c1) = spec.start, spec.end
        chart = LineChart()
        chart.legend = None
        data = Reference(
            worksheet,
            min_col=c0 + 1, max_col=c1 + 1,
            min_row=r0 + 1, max_row=r1 + 1,
        )
        # A single-row range lays its series out across a row.
        chart.add_data(data, from_rows=(r0 == r1))
        # Carry the spec color onto the native series line (openpyxl wants RRGGBB).
        if spec.color and chart.series:
            chart.series[0].graphicalProperties.line.solidFill = spec.color.lstrip('#')
        anchor = f'{get_column_letter(cell.column.position + 1)}{cell.row.position + 1}'
        worksheet.add_chart(chart, anchor)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()



# ---------------------------------------------------------------------------
# Patch an imported original workbook (moved unchanged from xlsx_patch_export.py)
# ---------------------------------------------------------------------------

# _excel_font omits name, size, and color when the web value is null.
# A brand-new openpyxl cell then uses these library defaults. This repo does
# not define its own Excel font name, point size, or cleared text color.
# The grid draws a null font size at 12px; the Excel exporter does not write 12.
_OPENPYXL_DEFAULT_FONT_NAME = 'Calibri'
_OPENPYXL_DEFAULT_FONT_SIZE = 11
_OPENPYXL_DEFAULT_FONT_COLOR = Color(theme=1)


class XlsxPatchExportError(Exception):
    """The original workbook cannot be patched safely."""


def build_patched_workbook(source, diff, *, workbook_bytes=None, worksheet_name=None) -> bytes:
    """Return xlsx bytes for an original workbook with diff applied to one worksheet."""
    if workbook_bytes is None:
        if not getattr(source, 'file', None):
            raise XlsxPatchExportError('The original Excel file is missing.')
        try:
            with source.file.open('rb') as handle:
                workbook = load_workbook(handle)
        except Exception as exc:
            raise XlsxPatchExportError('The original Excel file could not be opened.') from exc
    else:
        if not isinstance(workbook_bytes, (bytes, bytearray)) or not workbook_bytes:
            raise XlsxPatchExportError('The original Excel file is missing.')
        try:
            workbook = load_workbook(io.BytesIO(workbook_bytes))
        except Exception as exc:
            raise XlsxPatchExportError('The original Excel file could not be opened.') from exc

    selected_name = worksheet_name if isinstance(worksheet_name, str) and worksheet_name.strip() else getattr(source, 'worksheet_name', '')
    sheet = source.sheet
    try:
        worksheet = _worksheet(workbook, selected_name)
        _replay_structure(worksheet, diff.get('structural_operations') or [])
        cell_changes = diff.get('cells') or {}
        changed_cells = _reject_sparkline_changes(sheet, cell_changes)
        for key, change in cell_changes.items():
            _patch_value(worksheet, key, change, changed_cells)
        for key, change in (diff.get('formats') or {}).items():
            _patch_format(worksheet, key, change or {})
        highlights = diff.get('highlights') or {}
        for key, color in (highlights.get('cells') or {}).items():
            _patch_highlight(worksheet, key, color)

        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    finally:
        workbook.close()


def _worksheet(workbook, worksheet_name):
    name = (worksheet_name or '').strip()
    if not name or name not in workbook.sheetnames:
        raise XlsxPatchExportError(
            f'Worksheet {worksheet_name!r} is not in the original workbook.'
        )
    return workbook[name]

def _replay_structure(worksheet, operations):
    """Apply validated insert and delete operations before cell patches."""
    if not operations:
        return
    _reject_unshifted_formulas(worksheet)
    for operation in operations:
        if not isinstance(operation, dict):
            raise XlsxPatchExportError('Bad structural operation.')
        op_type = operation.get('op_type')
        anchor = operation.get('anchor_position')
        count = operation.get('count')
        if isinstance(anchor, bool) or not isinstance(anchor, int) or anchor < 0:
            raise XlsxPatchExportError('Bad structural anchor.')
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise XlsxPatchExportError('Bad structural count.')
        excel_index = anchor + 1
        if op_type in ('ROW_INSERT', 'ROW_DELETE'):
            axis = 'row'
        elif op_type in ('COL_INSERT', 'COL_DELETE'):
            axis = 'column'
        else:
            raise XlsxPatchExportError(f'Unsupported structural operation {op_type!r}.')

        deleting = op_type.endswith('_DELETE')
        _reject_unsupported_structure(worksheet, axis, excel_index, count, deleting)
        if deleting:
            _drop_deleted_metadata(worksheet, axis, excel_index, count)
        moved_merges = _take_merges_after_edit(worksheet, axis, excel_index, count, deleting)
        _shift_dimensions_after_edit(worksheet, axis, excel_index, count, deleting)
        if op_type == 'ROW_INSERT':
            worksheet.insert_rows(excel_index, count)
        elif op_type == 'ROW_DELETE':
            worksheet.delete_rows(excel_index, count)
        elif op_type == 'COL_INSERT':
            worksheet.insert_cols(excel_index, count)
        else:
            worksheet.delete_cols(excel_index, count)
        for ref in moved_merges:
            worksheet.merge_cells(ref)


def _reject_unshifted_formulas(worksheet):
    for row in worksheet.iter_rows():
        for cell in row:
            value = cell.value
            text = value if isinstance(value, str) else getattr(value, 'text', None)
            if isinstance(text, str) and text.startswith('=') and ('$' in text or '!' in text):
                raise XlsxPatchExportError('This formula reference cannot be shifted.')


def _reject_unsupported_structure(worksheet, axis, excel_index, count, deleting):
    """Refuse features an insert or delete would leave on the old index."""
    if worksheet.conditional_formatting._cf_rules:
        raise XlsxPatchExportError('Conditional formatting cannot be shifted.')
    if worksheet.data_validations.dataValidation:
        raise XlsxPatchExportError('Data validation cannot be shifted.')
    if worksheet.tables:
        raise XlsxPatchExportError('Tables cannot be shifted.')
    if worksheet._charts or worksheet._images:
        raise XlsxPatchExportError('Charts or images cannot be shifted.')
    if worksheet.freeze_panes:
        raise XlsxPatchExportError('Frozen panes cannot be shifted.')
    if worksheet.auto_filter.ref:
        raise XlsxPatchExportError('An auto filter cannot be shifted.')
    if _defined_name_uses_sheet(worksheet):
        raise XlsxPatchExportError('Named ranges cannot be shifted.')
    span_end = excel_index + count
    for merged in worksheet.merged_cells.ranges:
        if axis == 'row' and _feature_blocks_structure(
            merged.min_row, merged.max_row, excel_index, span_end, deleting,
        ):
            raise XlsxPatchExportError('A merged range crosses this row change.')
        if axis == 'column' and _feature_blocks_structure(
            merged.min_col, merged.max_col, excel_index, span_end, deleting,
        ):
            raise XlsxPatchExportError('A merged range crosses this column change.')
    if axis == 'row':
        for index, dimension in worksheet.row_dimensions.items():
            if isinstance(index, int) and (dimension.height is not None or dimension.hidden):
                if _feature_blocks_structure(index, index, excel_index, span_end, deleting):
                    raise XlsxPatchExportError('Row heights cannot be shifted.')
        return
    for letter, dimension in worksheet.column_dimensions.items():
        if dimension.width is None and not dimension.hidden:
            continue
        index = column_index_from_string(letter)
        if _feature_blocks_structure(index, index, excel_index, span_end, deleting):
            raise XlsxPatchExportError('Column widths cannot be shifted.')


def _defined_name_uses_sheet(worksheet):
    defined_names = getattr(worksheet.parent, 'defined_names', None)
    if not defined_names:
        return False
    title = worksheet.title
    quoted = "'" + title.replace("'", "''") + "'"
    for defined in defined_names.values():
        text = defined.attr_text or ''
        if quoted in text or f'{title}!' in text:
            return True
    return False


def _feature_blocks_structure(start, end, cut, span_end, deleting):
    """True when this feature crosses the edit and cannot be moved as a whole."""
    if end < cut:
        return False
    if deleting and start >= cut and end < span_end:
        return False
    if start >= (span_end if deleting else cut):
        return False
    return True


def _drop_deleted_metadata(worksheet, axis, excel_index, count):
    """Remove dimensions and merges that occupy only the rows or columns being deleted."""
    span_end = excel_index + count
    if axis == 'row':
        for index in list(worksheet.row_dimensions):
            if isinstance(index, int) and excel_index <= index < span_end:
                del worksheet.row_dimensions[index]
        for merged in list(worksheet.merged_cells.ranges):
            if merged.min_row >= excel_index and merged.max_row < span_end:
                worksheet.unmerge_cells(str(merged))
        return
    for letter in list(worksheet.column_dimensions):
        index = column_index_from_string(letter)
        if excel_index <= index < span_end:
            del worksheet.column_dimensions[letter]
    for merged in list(worksheet.merged_cells.ranges):
        if merged.min_col >= excel_index and merged.max_col < span_end:
            worksheet.unmerge_cells(str(merged))


def _take_merges_after_edit(worksheet, axis, cut, count, deleting):
    span_end = cut + count
    move_from = span_end if deleting else cut
    moved = []
    for merged in list(worksheet.merged_cells.ranges):
        start = merged.min_row if axis == 'row' else merged.min_col
        if start < move_from:
            continue
        worksheet.unmerge_cells(range_string=str(merged))
        shifted = CellRange(str(merged))
        delta = -count if deleting else count
        if axis == 'row':
            shifted.shift(row_shift=delta)
        else:
            shifted.shift(col_shift=delta)
        moved.append(str(shifted))
    return moved


def _shift_dimensions_after_edit(worksheet, axis, cut, count, deleting):
    move_from = cut + count if deleting else cut
    delta = -count if deleting else count
    if axis == 'row':
        pending = []
        for index in list(worksheet.row_dimensions):
            if not isinstance(index, int) or index < move_from:
                continue
            dimension = worksheet.row_dimensions[index]
            if dimension.height is None and not dimension.hidden:
                continue
            pending.append((index, dimension.height, dimension.hidden))
            del worksheet.row_dimensions[index]
        for index, height, hidden in pending:
            target = worksheet.row_dimensions[index + delta]
            target.height = height
            target.hidden = hidden
        return
    pending = []
    for letter in list(worksheet.column_dimensions):
        index = column_index_from_string(letter)
        if index < move_from:
            continue
        dimension = worksheet.column_dimensions[letter]
        if dimension.width is None and not dimension.hidden:
            continue
        pending.append((index, dimension.width, dimension.hidden))
        del worksheet.column_dimensions[letter]
    for index, width, hidden in pending:
        target = worksheet.column_dimensions[get_column_letter(index + delta)]
        target.width = width
        target.hidden = hidden

def _parse_coordinate(key):
    parts = str(key).split(',')
    if len(parts) != 2:
        raise XlsxPatchExportError(f'Bad cell coordinate {key!r}.')
    try:
        row_pos = int(parts[0])
        col_pos = int(parts[1])
    except ValueError as exc:
        raise XlsxPatchExportError(f'Bad cell coordinate {key!r}.') from exc
    if row_pos < 0 or col_pos < 0:
        raise XlsxPatchExportError(f'Bad cell coordinate {key!r}.')
    return row_pos, col_pos


def _excel_cell(worksheet, key):
    row_pos, col_pos = _parse_coordinate(key)
    return worksheet.cell(row=row_pos + 1, column=col_pos + 1)


def _load_changed_cells(sheet, needed):
    if not needed:
        return {}, {}, {}
    row_positions = {row for _key, (row, _column) in needed}
    column_positions = {column for _key, (_row, column) in needed}
    rows = {
        row.position: row
        for row in SheetRow.objects.filter(
            sheet=sheet,
            is_deleted=False,
            position__in=row_positions,
        )
    }
    columns = {
        column.position: column
        for column in SheetColumn.objects.filter(
            sheet=sheet,
            is_deleted=False,
            position__in=column_positions,
        )
    }
    pair_filter = Q()
    for _key, (row_pos, col_pos) in needed:
        row = rows.get(row_pos)
        column = columns.get(col_pos)
        if row is None or column is None:
            continue
        pair_filter |= Q(row_id=row.id, column_id=column.id)
    cells_by_position = {}

    if pair_filter:
        row_pos_by_id = {row.id: row.position for row in rows.values()}
        column_pos_by_id = {column.id: column.position for column in columns.values()}
        for cell in Cell.objects.filter(pair_filter, sheet=sheet, is_deleted=False):
            cells_by_position[
                (row_pos_by_id[cell.row_id], column_pos_by_id[cell.column_id])
            ] = cell
    return rows, columns, cells_by_position


def _reject_sparkline_changes(sheet, cell_changes):
    needed = []
    for key, change in cell_changes.items():
        raw = (change or {}).get('raw_input')
        if raw is None:
            continue
        if is_sparkline(str(raw)):
            raise XlsxPatchExportError(f'Cell {key} is a sparkline and cannot be patched.')
        needed.append((key, _parse_coordinate(key)))

    rows, columns, cells_by_position = _load_changed_cells(sheet, needed)
    for key, (row_pos, col_pos) in needed:
        if row_pos not in rows:
            raise XlsxPatchExportError(f'No active row at position {row_pos}.')
        if col_pos not in columns:
            raise XlsxPatchExportError(f'No active column at position {col_pos}.')
        cell = cells_by_position.get((row_pos, col_pos))
        if cell is not None and is_sparkline(cell.raw_input):
            raise XlsxPatchExportError(f'Cell {key} is a sparkline and cannot be patched.')
    return rows, columns, cells_by_position


def _patch_value(worksheet, key, change, changed_cells):
    _rows, _columns, cells_by_position = changed_cells
    row_pos, col_pos = _parse_coordinate(key)
    excel_cell = worksheet.cell(row=row_pos + 1, column=col_pos + 1)
    raw = (change or {}).get('raw_input')
    if raw is None:
        excel_cell.value = None
        return
    cell = cells_by_position.get((row_pos, col_pos))
    if cell is None:
        raise XlsxPatchExportError(f'No active cell at {key} to convert for export.')
    excel_cell.value = _excel_value(cell)


def _apply_font_family(font, value) -> bool:
    """Return True when the font name was changed."""
    if value is None:
        font.name = _OPENPYXL_DEFAULT_FONT_NAME
        font.scheme = None
        return True
    mapped = font_name(value)
    if not mapped:
        return False
    font.name = mapped
    font.scheme = None
    return True


# Excel font strategies, shared by rebuild and patch. Each one sets one web
# field on a Font and returns True when it changed it.
def _patch_flag(attribute):
    def strategy(font, value):
        setattr(font, attribute, bool(value))
        return True
    return strategy


def _patch_font_size(font, value):
    font.size = _OPENPYXL_DEFAULT_FONT_SIZE if value is None else value
    return True


def _patch_text_color(font, value):
    if value is None:
        font.color = _OPENPYXL_DEFAULT_FONT_COLOR
        return True
    rgb = _excel_rgb(value)
    if not rgb:
        raise XlsxPatchExportError(f'Unsupported text color {value!r}.')
    font.color = rgb
    return True


class _ExcelFont(FormatStrategy):
    """Set web font fields on an openpyxl Font.

    Rebuild (_excel_font) passes only set, supported fields on a new Font().
    Patch (_patch_font) passes the diff's fields on a copy of the original font,
    so None resets to the openpyxl default and an invalid colour raises.
    """

    strategies = {
        'bold': _patch_flag('bold'),
        'italic': _patch_flag('italic'),
        'strikethrough': _patch_flag('strike'),
        'font_family': _apply_font_family,
        'font_size': _patch_font_size,
        'text_color': _patch_text_color,
    }


def _patch_font(excel_cell, changes):
    font = copy(excel_cell.font)
    if _ExcelFont.apply(font, changes):
        excel_cell.font = font


def _patch_number_format(excel_cell, changes):
    if 'number_format' in changes:
        excel_cell.number_format = _excel_number_format(changes['number_format']) or 'General'

        
def _patch_format(worksheet, key, changes):
    excel_cell = _excel_cell(worksheet, key)
    _patch_font(excel_cell, changes)
    _patch_number_format(excel_cell, changes)


def _patch_highlight(worksheet, key, color):
    excel_cell = _excel_cell(worksheet, key)
    if color is None:
        excel_cell.fill = PatternFill(fill_type=None)
        return
    if not _excel_rgb(color):
        raise XlsxPatchExportError(f'Unsupported highlight color {color!r}.')
    _apply_fill(excel_cell, color)