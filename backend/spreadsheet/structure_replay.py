from .models import SheetStructureOperation

_INSERT_TO_DELETE = {
    SheetStructureOperation.OperationType.ROW_INSERT: SheetStructureOperation.OperationType.ROW_DELETE,
    SheetStructureOperation.OperationType.COL_INSERT: SheetStructureOperation.OperationType.COL_DELETE,
}

def _cancels_previous(previous, following):
    expected_delete = _INSERT_TO_DELETE.get(previous.get('op_type'))
    if expected_delete is None or following.get('op_type') != expected_delete:
        return False
    if previous.get('anchor_position') != following.get('anchor_position'):
        return False
    if previous.get('count') != following.get('count'):
        return False
    inserted_ids = previous.get('affected_ids')
    deleted_ids = following.get('affected_ids')
    return isinstance(inserted_ids, list) and inserted_ids == deleted_ids


def _drop_cancelled_operations(operations):
    """Remove an insert immediately followed by a delete of those same ids."""
    kept = []
    index = 0
    while index < len(operations):
        if (
            index + 1 < len(operations)
            and _cancels_previous(operations[index], operations[index + 1])
        ):
            index += 2
            continue
        current = operations[index]
        kept.append({
            'op_type': current['op_type'],
            'anchor_position': current['anchor_position'],
            'count': current['count'],
        })
        index += 1
    return kept

def replay_baseline_structure(sheet, baseline, current_row_ids, current_column_ids):
    """Replay post-import insert/delete operations onto one saved baseline.

    Returns {'safe': False} unless those operations reproduce the live row
    and column ids exactly. Sort, reorder, and an old baseline with no
    checkpoint stay unsafe.
    """
    if not isinstance(baseline, dict) or 'structure_operation_checkpoint' not in baseline:
        return {'safe': False}
    checkpoint = baseline.get('structure_operation_checkpoint')
    if isinstance(checkpoint, bool) or not isinstance(checkpoint, int) or checkpoint < 0:
        return {'safe': False}

    try:
        row_ids = [int(row_id) for row_id in baseline.get('row_ids') or []]
        column_ids = [int(column_id) for column_id in baseline.get('column_ids') or []]
        live_rows = [int(row_id) for row_id in current_row_ids or []]
        live_columns = [int(column_id) for column_id in current_column_ids or []]
    except (TypeError, ValueError):
        return {'safe': False}

    cells = dict(baseline.get('cells') or {})
    formats = dict(baseline.get('formats') or {})
    highlights = {
        'cells': dict((baseline.get('highlights') or {}).get('cells') or {}),
        'rows': dict((baseline.get('highlights') or {}).get('rows') or {}),
        'columns': dict((baseline.get('highlights') or {}).get('columns') or {}),
    }
    operations = []

    recorded = (
        SheetStructureOperation.objects.filter(
            sheet=sheet,
            id__gt=checkpoint,
            is_reverted=False,
        )
        .order_by('id')
    )
    for operation in recorded:
        applied = _apply_operation(operation, row_ids, column_ids, cells, formats, highlights)
        if applied is None:
            return {'safe': False}
        row_ids, column_ids, cells, formats, highlights = applied
        operations.append({
            'op_type': operation.op_type,
            'anchor_position': operation.anchor_position,
            'count': operation.count,
            'affected_ids': _operation_ids(operation),
        })

    if live_rows[:len(row_ids)] != row_ids or live_columns[:len(column_ids)] != column_ids:
        return {'safe': False}
    return {
        'safe': True,
        'structural_operations': _drop_cancelled_operations(operations),
        'row_ids': row_ids,
        'column_ids': column_ids,
        'cells': cells,
        'formats': formats,
        'highlights': highlights,
    }


def _operation_ids(operation):
    raw_ids = operation.affected_ids or []
    if not isinstance(raw_ids, list) or len(raw_ids) != operation.count or operation.count < 1:
        return None
    try:
        parsed = [int(value) for value in raw_ids]
    except (TypeError, ValueError):
        return None
    if len(set(parsed)) != len(parsed):
        return None
    return parsed


def _apply_operation(operation, row_ids, column_ids, cells, formats, highlights):
    affected_ids = _operation_ids(operation)
    anchor = operation.anchor_position
    count = operation.count
    if affected_ids is None or isinstance(anchor, bool) or not isinstance(anchor, int):
        return None

    op_type = operation.op_type
    if op_type == SheetStructureOperation.OperationType.ROW_INSERT:
        row_ids = _insert_ids(row_ids, anchor, affected_ids)
        axis, delete = 'row', False
    elif op_type == SheetStructureOperation.OperationType.ROW_DELETE:
        row_ids = _delete_ids(row_ids, anchor, affected_ids)
        axis, delete = 'row', True
    elif op_type == SheetStructureOperation.OperationType.COL_INSERT:
        column_ids = _insert_ids(column_ids, anchor, affected_ids)
        axis, delete = 'column', False
    elif op_type == SheetStructureOperation.OperationType.COL_DELETE:
        column_ids = _delete_ids(column_ids, anchor, affected_ids)
        axis, delete = 'column', True
    else:
        return None
    if row_ids is None or column_ids is None:
        return None

    try:
        cells = _shift_cell_map(cells, axis=axis, anchor=anchor, count=count, delete=delete)
        formats = _shift_cell_map(formats, axis=axis, anchor=anchor, count=count, delete=delete)
        highlights = _shift_highlights(highlights, axis=axis, anchor=anchor, count=count, delete=delete)
    except (TypeError, ValueError):
        return None
    return row_ids, column_ids, cells, formats, highlights


def _insert_ids(ids, anchor, new_ids):
    if anchor < 0 or anchor > len(ids):
        return None
    if any(new_id in ids for new_id in new_ids):
        return None
    return ids[:anchor] + new_ids + ids[anchor:]


def _delete_ids(ids, anchor, removed_ids):
    end = anchor + len(removed_ids)
    if anchor < 0 or end > len(ids) or ids[anchor:end] != removed_ids:
        return None
    return ids[:anchor] + ids[end:]


def _shift_index(index, anchor, count, delete):
    if delete:
        if anchor <= index < anchor + count:
            return None
        if index >= anchor + count:
            return index - count
        return index
    if index >= anchor:
        return index + count
    return index


def _shift_cell_map(mapping, *, axis, anchor, count, delete):
    shifted = {}
    for key, value in mapping.items():
        row_text, column_text = str(key).split(',', 1)
        row = int(row_text)
        column = int(column_text)
        if axis == 'row':
            row = _shift_index(row, anchor, count, delete)
        else:
            column = _shift_index(column, anchor, count, delete)
        if row is None or column is None:
            continue
        shifted[f'{row},{column}'] = value
    return shifted


def _shift_index_map(mapping, anchor, count, delete):
    shifted = {}
    for key, value in mapping.items():
        index = _shift_index(int(key), anchor, count, delete)
        if index is None:
            continue
        shifted[str(index)] = value
    return shifted


def _shift_highlights(highlights, *, axis, anchor, count, delete):
    cells = _shift_cell_map(
        highlights.get('cells') or {},
        axis=axis,
        anchor=anchor,
        count=count,
        delete=delete,
    )
    if axis == 'row':
        rows = _shift_index_map(highlights.get('rows') or {}, anchor, count, delete)
        columns = dict(highlights.get('columns') or {})
    else:
        rows = dict(highlights.get('rows') or {})
        columns = _shift_index_map(highlights.get('columns') or {}, anchor, count, delete)
    return {'cells': cells, 'rows': rows, 'columns': columns}