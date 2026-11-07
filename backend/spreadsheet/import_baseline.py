from django.db.models import Max
from .models import (
    Cell,
    SheetColumn,
    SheetRow,
    SheetStructureOperation,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)


def _cell_key(row_position: int, column_position: int) -> str:
    return f'{row_position},{column_position}'


def _format_payload(fmt) -> dict:
    return {
        'bold': bool(fmt.bold),
        'italic': bool(fmt.italic),
        'strikethrough': bool(fmt.strikethrough),
        'text_color': fmt.text_color or None,
        'font_family': fmt.font_family or None,
        'font_size': fmt.font_size,
        'number_format': fmt.number_format if isinstance(fmt.number_format, dict) else None,
    }


def _is_default_format(payload: dict) -> bool:
    return (
        payload['bold'] is False
        and payload['italic'] is False
        and payload['strikethrough'] is False
        and payload['text_color'] is None
        and payload['font_family'] is None
        and payload['font_size'] is None
        and payload['number_format'] is None
    )

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
        payload = _format_payload(fmt)
        if _is_default_format(payload):
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
        'version': 1,
        'row_ids': row_ids,
        'column_ids': column_ids,
        'cells': cells_out,
        'formats': formats_out,
        'highlights': highlights_out,
        'structure_operation_checkpoint': _structure_operation_checkpoint(sheet),
    }