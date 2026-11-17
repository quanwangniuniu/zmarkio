"""Move cell formats and highlights with row/column inserts and deletes.

SheetService calls these inside its structural operations, so stored formats and
highlights stay attached to their cells, and an undone delete gets them back.
structure_replay.py shifts the import baseline the same way during export;
keep the two in step, or moved formatting will look like an edit.
"""

from django.db.models import F

from .models import SpreadsheetCellFormat, SpreadsheetHighlight, SpreadsheetHighlightScope


def _shift_decoration_index(base_qs, field_name, start, delta):
    offset = 1_000_000
    matched = base_qs.filter(**{f"{field_name}__gte": start})
    if not matched.exists():
        return
    matched.update(**{field_name: F(field_name) + offset})
    base_qs.filter(**{f"{field_name}__gte": start + offset}).update(
        **{field_name: F(field_name) - offset + delta}
    )


def _format_qs(sheet):
    return SpreadsheetCellFormat.objects.filter(sheet=sheet)


def _highlight_qs(sheet, scope):
    return SpreadsheetHighlight.objects.filter(sheet=sheet, scope=scope)


def shift_column_metadata_for_insert(sheet, position, count):
    _shift_decoration_index(
        _format_qs(sheet), "column_index", position, count
    )
    for scope in (SpreadsheetHighlightScope.CELL, SpreadsheetHighlightScope.COLUMN):
        _shift_decoration_index(
            _highlight_qs(sheet, scope), "col_index", position, count
        )


def shift_column_metadata_for_delete(sheet, position, count):
    end = position + count
    _format_qs(sheet).filter(
        column_index__gte=position,
        column_index__lt=end,
    ).delete()
    for scope in (SpreadsheetHighlightScope.CELL, SpreadsheetHighlightScope.COLUMN):
        _highlight_qs(sheet, scope).filter(
            col_index__gte=position,
            col_index__lt=end,
        ).delete()
    _shift_decoration_index(
        _format_qs(sheet), "column_index", end, -count
    )
    for scope in (SpreadsheetHighlightScope.CELL, SpreadsheetHighlightScope.COLUMN):
        _shift_decoration_index(
            _highlight_qs(sheet, scope), "col_index", end, -count
        )


def shift_row_metadata_for_insert(sheet, position, count):
    _shift_decoration_index(
        _format_qs(sheet), "row_index", position, count
    )
    for scope in (SpreadsheetHighlightScope.CELL, SpreadsheetHighlightScope.ROW):
        _shift_decoration_index(
            _highlight_qs(sheet, scope), "row_index", position, count
        )


def shift_row_metadata_for_delete(sheet, position, count):
    end = position + count
    _format_qs(sheet).filter(
        row_index__gte=position,
        row_index__lt=end,
    ).delete()
    for scope in (SpreadsheetHighlightScope.CELL, SpreadsheetHighlightScope.ROW):
        _highlight_qs(sheet, scope).filter(
            row_index__gte=position,
            row_index__lt=end,
        ).delete()
    _shift_decoration_index(
        _format_qs(sheet), "row_index", end, -count
    )
    for scope in (SpreadsheetHighlightScope.CELL, SpreadsheetHighlightScope.ROW):
        _shift_decoration_index(
            _highlight_qs(sheet, scope), "row_index", end, -count
        )


def snapshot_removed_decorations(sheet, *, axis, position, count):
    """Copy the format and highlight rows that the following delete will remove."""
    end = position + count
    if axis == 'row':
        formats = _format_qs(sheet).filter(
            row_index__gte=position,
            row_index__lt=end,
        )
        highlights = SpreadsheetHighlight.objects.filter(
            sheet=sheet,
            scope__in=(
                SpreadsheetHighlightScope.CELL,
                SpreadsheetHighlightScope.ROW,
            ),
            row_index__gte=position,
            row_index__lt=end,
        )
    else:
        formats = _format_qs(sheet).filter(
            column_index__gte=position,
            column_index__lt=end,
        )
        highlights = SpreadsheetHighlight.objects.filter(
            sheet=sheet,
            scope__in=(
                SpreadsheetHighlightScope.CELL,
                SpreadsheetHighlightScope.COLUMN,
            ),
            col_index__gte=position,
            col_index__lt=end,
        )
    return {
        'formats': [
            {
                'row_index': item.row_index,
                'column_index': item.column_index,
                'bold': item.bold,
                'italic': item.italic,
                'strikethrough': item.strikethrough,
                'text_color': item.text_color,
                'font_family': item.font_family,
                'font_size': item.font_size,
                'number_format': item.number_format,
            }
            for item in formats
        ],
        'highlights': [
            {
                'scope': item.scope,
                'row_index': item.row_index,
                'col_index': item.col_index,
                'color': item.color,
            }
            for item in highlights
        ],
    }


def restore_removed_decorations(sheet, payload):
    """Recreate a delete snapshot without replacing a record already at that coordinate."""
    if not isinstance(payload, dict):
        return
    for item in payload.get('formats') or []:
        if not isinstance(item, dict):
            continue
        row_index = item.get('row_index')
        column_index = item.get('column_index')
        if isinstance(row_index, bool) or not isinstance(row_index, int):
            continue
        if isinstance(column_index, bool) or not isinstance(column_index, int):
            continue
        if SpreadsheetCellFormat.objects.filter(
            sheet=sheet,
            row_index=row_index,
            column_index=column_index,
        ).exists():
            continue
        SpreadsheetCellFormat.objects.create(
            sheet=sheet,
            row_index=row_index,
            column_index=column_index,
            bold=bool(item.get('bold')),
            italic=bool(item.get('italic')),
            strikethrough=bool(item.get('strikethrough')),
            text_color=item.get('text_color'),
            font_family=item.get('font_family'),
            font_size=item.get('font_size'),
            number_format=item.get('number_format') if isinstance(item.get('number_format'), dict) else None,
        )
    for item in payload.get('highlights') or []:
        if not isinstance(item, dict):
            continue
        scope = item.get('scope')
        row_index = item.get('row_index')
        col_index = item.get('col_index')
        color = item.get('color')
        if scope not in (
            SpreadsheetHighlightScope.CELL,
            SpreadsheetHighlightScope.ROW,
            SpreadsheetHighlightScope.COLUMN,
        ):
            continue
        if isinstance(row_index, bool) or not isinstance(row_index, int):
            continue
        if isinstance(col_index, bool) or not isinstance(col_index, int):
            continue
        if not color:
            continue
        if SpreadsheetHighlight.objects.filter(
            sheet=sheet,
            scope=scope,
            row_index=row_index,
            col_index=col_index,
        ).exists():
            continue
        SpreadsheetHighlight.objects.create(
            spreadsheet_id=sheet.spreadsheet_id,
            sheet=sheet,
            scope=scope,
            row_index=row_index,
            col_index=col_index,
            color=color,
        )