import re
from typing import Dict, List, Tuple

from django.db import transaction
from django.db.models import Q

from .models import Cell, CellValueType

_CELL_REF_RE = re.compile(r'(?<![A-Z0-9_!])(\$?)([A-Z]+)(\$?)(\d+)(?![A-Z0-9_!])')
_COL_RANGE_RE = re.compile(r'(?<![A-Z0-9_!])(\$?)([A-Z]+):(\$?)([A-Z]+)(?![A-Z0-9_!])')
_PROTECTED_RE = re.compile(
    r'('
    r'"(?:[^"]|"")*"'
    r'|'
    r"(?:'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_.]*)!"
    r'(?:\$?[A-Z]+\$?\d+(?::\$?[A-Z]+\$?\d+)?|\$?[A-Z]+:\$?[A-Z]+)'
    r')'
)
_MAX_COLUMN_INDEX = 701  # last column this grid can store: ZZ
_MAX_ROW_NUMBER = 100000


def _col_label_to_index(label: str) -> int:
    index = 0
    for char in label:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _index_to_col_label(index: int) -> str:
    if index < 0:
        return ''
    result = ''
    index += 1
    while index > 0:
        index -= 1
        result = chr(ord('A') + (index % 26)) + result
        index //= 26
    return result


def _rewrite_index(index: int, op_type: str, anchor_pos: int, count: int) -> str:
    if op_type.endswith('_INSERT'):
        if index >= anchor_pos:
            return str(index + count)
        return str(index)
    if op_type.endswith('_DELETE'):
        if anchor_pos <= index <= anchor_pos + count - 1:
            return '#REF!'
        if index > anchor_pos + count - 1:
            return str(index - count)
        return str(index)
    return str(index)


def _formula_is_supported(formula: str) -> bool:
    for index, piece in enumerate(_PROTECTED_RE.split(formula)):
        if index % 2 == 1:
            continue
        if any(mark in piece for mark in ('[', ']', '!', '"', "'")):
            return False
        for match in re.finditer(r'[A-Z]+[0-9]+', piece):
            label = re.match(r'([A-Z]+)([0-9]+)', match.group(0))
            column_index = _col_label_to_index(label.group(1))
            row_number = int(label.group(2))
            if column_index > _MAX_COLUMN_INDEX or row_number < 1 or row_number > _MAX_ROW_NUMBER:
                return False
    return True


def _rewrite_unquoted(formula: str, op_type: str, anchor_pos: int, count: int) -> str:
    def rewrite_col_range(match: re.Match) -> str:
        start_dollar = match.group(1)
        start_label = match.group(2)
        end_dollar = match.group(3)
        end_label = match.group(4)
        if op_type.startswith('ROW_'):
            return match.group(0)

        start_index = _col_label_to_index(start_label)
        end_index = _col_label_to_index(end_label)
        new_start = _rewrite_index(start_index, op_type, anchor_pos, count)
        new_end = _rewrite_index(end_index, op_type, anchor_pos, count)
        start_text = '#REF!' if new_start == '#REF!' else f"{start_dollar}{_index_to_col_label(int(new_start))}"
        end_text = '#REF!' if new_end == '#REF!' else f"{end_dollar}{_index_to_col_label(int(new_end))}"
        return f"{start_text}:{end_text}"

    def rewrite_cell(match: re.Match) -> str:
        column_dollar = match.group(1)
        col_label = match.group(2)
        row_dollar = match.group(3)
        row_number = int(match.group(4))
        col_index = _col_label_to_index(col_label)
        row_index = row_number - 1

        if op_type.startswith('COL_'):
            new_col = _rewrite_index(col_index, op_type, anchor_pos, count)
            if new_col == '#REF!':
                return '#REF!'
            return f"{column_dollar}{_index_to_col_label(int(new_col))}{row_dollar}{row_number}"

        if op_type.startswith('ROW_'):
            new_row = _rewrite_index(row_index, op_type, anchor_pos, count)
            if new_row == '#REF!':
                return '#REF!'
            return f"{column_dollar}{col_label}{row_dollar}{int(new_row) + 1}"

        return match.group(0)

    rewritten = _COL_RANGE_RE.sub(rewrite_col_range, formula)
    return _CELL_REF_RE.sub(rewrite_cell, rewritten)


def rewrite_formula_text(formula: str, op_type: str, anchor_pos: int, count: int) -> str:
    if not isinstance(formula, str) or not formula:
        return formula
    if not _formula_is_supported(formula):
        return formula
    parts = []
    for index, piece in enumerate(_PROTECTED_RE.split(formula)):
        if index % 2 == 1:
            parts.append(piece)
        else:
            parts.append(_rewrite_unquoted(piece, op_type, anchor_pos, count))
    return ''.join(parts)


def _apply_formula_update(cell: Cell, op_type: str, anchor_pos: int, count: int) -> bool:
    raw_input = cell.raw_input or ''
    formula_value = cell.formula_value
    updated = False

    if raw_input.startswith('='):
        new_formula = rewrite_formula_text(raw_input, op_type, anchor_pos, count)
        if new_formula != raw_input:
            cell.raw_input = new_formula
            updated = True
        if cell.formula_value is not None or cell.value_type == CellValueType.FORMULA:
            if cell.formula_value != new_formula:
                cell.formula_value = new_formula
                updated = True
    elif formula_value:
        new_formula = rewrite_formula_text(formula_value, op_type, anchor_pos, count)
        if new_formula != formula_value:
            cell.formula_value = new_formula
            updated = True
    return updated


@transaction.atomic
def rewrite_cells_for_structure(
    sheet_id: int,
    op_type: str,
    anchor_pos: int,
    count: int,
) -> Tuple[List[Cell], Dict[str, dict]]:
    cells = Cell.objects.filter(
        sheet_id=sheet_id,
        is_deleted=False,
        row__is_deleted=False,
        column__is_deleted=False,
    ).filter(
        Q(raw_input__startswith='=') |
        Q(value_type=CellValueType.FORMULA) |
        Q(formula_value__isnull=False)
    )

    changed: List[Cell] = []
    rewrites: Dict[str, dict] = {}
    for cell in cells:
        old_raw = cell.raw_input
        old_formula = cell.formula_value
        if not _apply_formula_update(cell, op_type, anchor_pos, count):
            continue
        rewrites[str(cell.id)] = {
            'before_raw': old_raw,
            'after_raw': cell.raw_input,
            'before_formula': old_formula,
            'after_formula': cell.formula_value,
        }
        changed.append(cell)

    if changed:
        Cell.objects.bulk_update(changed, ['raw_input', 'formula_value'])
    return changed, rewrites


@transaction.atomic
def rewrite_cells_for_operation(sheet_id: int, op_type: str, anchor_pos: int, count: int) -> List[Cell]:
    changed, _rewrites = rewrite_cells_for_structure(sheet_id, op_type, anchor_pos, count)
    return changed


@transaction.atomic
def restore_formula_rewrites(sheet, payload) -> List[Cell]:
    if not isinstance(payload, dict) or not payload:
        return []

    cell_ids = []
    for key in payload:
        try:
            cell_ids.append(int(key))
        except (TypeError, ValueError):
            continue

    cells = {
        cell.id: cell
        for cell in Cell.objects.filter(
            sheet_id=sheet.id,
            id__in=cell_ids,
            is_deleted=False,
            row__is_deleted=False,
            column__is_deleted=False,
        )
    }
    changed: List[Cell] = []
    for key, snapshot in payload.items():
        try:
            cell_id = int(key)
        except (TypeError, ValueError):
            continue
        cell = cells.get(cell_id)
        if cell is None or not isinstance(snapshot, dict):
            continue
        if cell.raw_input != snapshot.get('after_raw') or cell.formula_value != snapshot.get('after_formula'):
            continue
        cell.raw_input = snapshot.get('before_raw')
        cell.formula_value = snapshot.get('before_formula')
        changed.append(cell)

    if changed:
        Cell.objects.bulk_update(changed, ['raw_input', 'formula_value'])
    return changed