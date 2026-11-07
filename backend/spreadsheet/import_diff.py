"""Compare a frozen import baseline with the live web sheet."""

from .import_baseline import build_import_baseline
from .structure_replay import replay_baseline_structure
from .models import SheetStructureOperation

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
    structure_safe,
    requires_rebuild,
    rebuild_reasons,
    has_changes,
    cells=None,
    formats=None,
    cell_highlights=None,
    structural_operations=None,
):
    return {
        'version': 1,
        'structure_safe': structure_safe,
        'requires_rebuild': requires_rebuild,
        'rebuild_reasons': list(rebuild_reasons),
        'has_changes': has_changes,
        'cells': cells or {},
        'formats': formats or {},
        'highlights': {'cells': cell_highlights or {}},
        'structural_operations': list(structural_operations or []),
    }


def _rebuild(reason):
    return _result(
        structure_safe=False,
        requires_rebuild=True,
        rebuild_reasons=[reason],
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


def _format_at(formats, key):
    """A missing format entry is the all-default format."""
    payload = dict(DEFAULT_FORMAT)
    raw = (formats or {}).get(key)
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
        before = _format_at(baseline_formats, key)
        after = _format_at(current_formats, key)
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
    """Compare source.baseline with a fresh build_import_baseline(source.sheet)."""
    baseline = getattr(source, 'baseline', None)
    if not isinstance(baseline, dict):
        return _rebuild('missing_baseline')
    if baseline.get('version') != BASELINE_VERSION:
        return _rebuild('unsupported_baseline_version')

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

    structure_safe = row_status != 'unsafe' and column_status != 'unsafe'
    if reasons:
        return _result(
            structure_safe=structure_safe,
            requires_rebuild=True,
            rebuild_reasons=reasons,
            has_changes=True,
        )

    has_changes = bool(cell_changes or format_changes or highlight_changes)
    return _result(
        structure_safe=True,
        requires_rebuild=False,
        rebuild_reasons=[],
        has_changes=has_changes,
        cells=cell_changes,
        formats=format_changes,
        cell_highlights=highlight_changes,
        structural_operations=structural_operations,
    )