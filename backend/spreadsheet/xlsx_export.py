"""Build an .xlsx workbook for a sheet, embedding native charts for sparklines (MED-295).

The frontend XLSX export is value-only (SheetJS) and cannot write native Excel
charts. This backend export writes the same cell values plus a native
``openpyxl`` ``LineChart`` for every ``=SPARKLINE(range)`` cell, so the exported
file opens in Excel with a real, editable chart.
"""
import io
import re
from typing import Any, List, Optional

from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill
from rest_framework.exceptions import ValidationError

from .models import Cell, ComputedCellType, SpreadsheetCellFormat, SpreadsheetHighlight
from .sparkline import is_sparkline, parse_sparkline

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

_EXCEL_FONT_NAMES = {
    'Arial, sans-serif': 'Arial',
    'Helvetica, Arial, sans-serif': 'Helvetica',
    'Georgia, serif': 'Georgia',
    '"Times New Roman", Times, serif': 'Times New Roman',
    'monospace': 'Consolas',
}
_CURRENCY_LOCALES = {
    'USD': '[$$-409]',
    'GBP': '[$£-809]',
    'EUR': '[$€-407]',
    'JPY': '[$¥-411]',
}

# converts spreadsheet colour to excel rgb
def _excel_rgb(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    hex_color = value.strip().lstrip('#').upper()
    if len(hex_color) != 6:
        return None
    return 'FF' + hex_color

# converts spreadsheet font to excel font (including features)
def _excel_font(fmt) -> Optional[Font]:
    kwargs = {}
    if fmt.bold:
        kwargs['bold'] = True
    if fmt.italic:
        kwargs['italic'] = True
    if fmt.strikethrough:
        kwargs['strike'] = True
    name = _EXCEL_FONT_NAMES.get((fmt.font_family or '').strip())
    if name:
        kwargs['name'] = name
    if fmt.font_size:
        kwargs['size'] = fmt.font_size
    color = _excel_rgb(fmt.text_color)
    if color:
        kwargs['color'] = color
    return Font(**kwargs) if kwargs else None

# converts spreadsheet number format to excel number format
def _excel_number_format(number_format) -> Optional[str]:
    if not isinstance(number_format, dict):
        return None
    kind = number_format.get('type')
    if kind in (None, '', 'GENERAL'):
        return None
    places = number_format.get('decimal_places')
    places = 2 if places is None else max(0, min(10, int(places)))
    number = '#,##0' if places == 0 else '#,##0.' + ('0' * places)
    percent = '0%' if places == 0 else '0.' + ('0' * places) + '%'
    if kind == 'PERCENT':
        return percent
    if kind == 'NUMBER':
        return number
    if kind == 'CURRENCY':
        prefix = _CURRENCY_LOCALES.get(number_format.get('currency_code') or 'USD')
        return (prefix + number) if prefix else number
    return None

# applies a fill/highlight to a cell
def _apply_fill(cell, color: str) -> None:
    rgb = _excel_rgb(color)
    if rgb:
        cell.fill = PatternFill(fill_type='solid', fgColor=rgb)

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
            
        raw = (cell.raw_input or "").strip()
        if raw.startswith("="):
            worksheet.cell(row=row, column=col, value=raw)
            continue
        value = _cell_export_value(cell)
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
    
    for highlight in highlights:
        if highlight.scope != 'COLUMN' or highlight.col_index is None or max_row < 0:
            continue
        for row_index in range(max_row + 1):
            _apply_fill(
                worksheet.cell(row=row_index + 1, column=highlight.col_index + 1),
                highlight.color,
            )
    for highlight in highlights:
        if highlight.scope != 'ROW' or highlight.row_index is None or max_col < 0:
            continue
        for col_index in range(max_col + 1):
            _apply_fill(
                worksheet.cell(row=highlight.row_index + 1, column=col_index + 1),
                highlight.color,
            )
    for highlight in highlights:
        if highlight.scope != 'CELL' or highlight.row_index is None or highlight.col_index is None:
            continue
        _apply_fill(
            worksheet.cell(row=highlight.row_index + 1, column=highlight.col_index + 1),
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
