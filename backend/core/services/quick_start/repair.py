"""
Post-LLM repair pass for Ollama CampaignPlan / ProjectBlueprint payloads.

Runs before strict validation so minor format drift does not fail preview generation.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Mapping

logger = logging.getLogger(__name__)

_DATE_ONLY_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})$')
_ISO_ish_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2})(?:[T\s](\d{2}:\d{2}(?::\d{2})?)?(?:Z|[+-]\d{2}:?\d{2})?)?$'
)

_MODULE_LIST_KEYS = (
    'tasks',
    'spreadsheets',
    'calendar_events',
    'decisions',
    'miro_boards',
)


def repair_campaign_plan(
    raw: Mapping[str, Any],
    selected_modules: dict[str, bool],
) -> dict[str, Any]:
    """Best-effort fixes for stage-1 CampaignPlan JSON."""
    plan = dict(raw)
    plan['version'] = 1

    summary = plan.get('campaign_summary') or plan.get('summary') or plan.get('description')
    if summary is not None:
        plan['campaign_summary'] = str(summary).strip()

    project = _as_dict(plan.get('project'))
    name = project.get('name') or project.get('title')
    if name is not None:
        project['name'] = str(name).strip()
    if not project.get('description') and plan.get('campaign_summary'):
        project['description'] = plan['campaign_summary']
    plan['project'] = project

    if selected_modules.get('tasks'):
        plan['tasks_plan'] = _repair_plan_task_rows(_as_list(plan.get('tasks_plan')))

    if selected_modules.get('spreadsheet'):
        sheet = _as_dict(plan.get('spreadsheet_plan'))
        sheet['columns'] = _repair_column_names(_as_list(sheet.get('columns')))
        plan['spreadsheet_plan'] = sheet

    if selected_modules.get('calendar'):
        plan['calendar_plan'] = _repair_plan_calendar_rows(_as_list(plan.get('calendar_plan')))

    if selected_modules.get('decisions'):
        plan['decisions_plan'] = _repair_plan_decision_rows(_as_list(plan.get('decisions_plan')))

    if selected_modules.get('miro'):
        miro = _as_dict(plan.get('miro_plan'))
        miro['sticky_themes'] = _repair_string_list(
            miro.get('sticky_themes') or miro.get('sticky_notes')
        )
        plan['miro_plan'] = miro

    return plan


def repair_blueprint(
    raw: Mapping[str, Any],
    selected_modules: dict[str, bool],
) -> dict[str, Any]:
    """Best-effort fixes for stage-2 ProjectBlueprint JSON."""
    blueprint = dict(raw)
    blueprint['version'] = 1

    project = _as_dict(blueprint.get('project'))
    name = project.get('name') or project.get('title')
    if name is not None:
        project['name'] = str(name).strip()
    for key in ('objectives', 'project_type', 'advertising_platforms'):
        project[key] = _repair_string_list(project.get(key))
    if project.get('description') is not None:
        project['description'] = str(project['description']).strip() or None
    blueprint['project'] = project

    for key in _MODULE_LIST_KEYS:
        if key not in blueprint or blueprint[key] is None:
            blueprint[key] = []

    if selected_modules.get('tasks'):
        blueprint['tasks'] = _repair_blueprint_tasks(_as_list(blueprint.get('tasks')))
    else:
        blueprint['tasks'] = []

    if selected_modules.get('spreadsheet'):
        blueprint['spreadsheets'] = _repair_spreadsheets(_as_list(blueprint.get('spreadsheets')))
    else:
        blueprint['spreadsheets'] = []

    if selected_modules.get('calendar'):
        blueprint['calendar_events'] = _repair_calendar_events(
            _as_list(blueprint.get('calendar_events'))
        )
    else:
        blueprint['calendar_events'] = []

    if selected_modules.get('decisions'):
        blueprint['decisions'] = _repair_decisions(_as_list(blueprint.get('decisions')))
    else:
        blueprint['decisions'] = []

    if selected_modules.get('miro'):
        blueprint['miro_boards'] = _repair_miro_boards(_as_list(blueprint.get('miro_boards')))
    else:
        blueprint['miro_boards'] = []

    return blueprint


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return []


def _repair_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if ',' in text:
            return [part.strip() for part in text.split(',') if part.strip()]
        return [text]
    if not isinstance(value, list):
        text = str(value).strip()
        return [text] if text else []
    items: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _repair_date_only(value: Any) -> str | None:
    if value is None or value == '':
        return None
    text = str(value).strip()
    match = _DATE_ONLY_RE.match(text)
    if match:
        return match.group(1)
    match = _ISO_ish_RE.match(text)
    if match:
        return match.group(1)
    return None


def repair_iso_datetime(value: Any, *, default_hour: int = 9) -> str | None:
    """Normalize common Ollama datetime shapes to ISO-8601 with Z suffix."""
    if value is None or value == '':
        return None
    text = str(value).strip()
    if not text:
        return None

    date_only = _repair_date_only(text)
    if date_only and 'T' not in text and ' ' not in text:
        return f'{date_only}T{default_hour:02d}:00:00Z'

    normalized = text.replace(' ', 'T')
    if normalized.endswith('Z'):
        pass
    elif '+' in normalized[10:] or normalized.count('-') > 2:
        # Has timezone offset — leave as-is if parseable.
        pass
    elif 'T' in normalized:
        normalized = f'{normalized}Z'

    try:
        datetime.fromisoformat(normalized.replace('Z', '+00:00'))
    except ValueError:
        if date_only:
            return f'{date_only}T{default_hour:02d}:00:00Z'
        return None
    return normalized


def _normalize_task_type(value: Any) -> str:
    from core.services.quick_start.validation import normalize_task_type

    return normalize_task_type(value)


def _normalize_task_priority(value: Any) -> str:
    from core.services.quick_start.validation import normalize_task_priority

    return normalize_task_priority(value)


def _repair_plan_task_rows(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        row = _as_dict(item)
        summary = row.get('summary') or row.get('title') or row.get('name')
        if not summary:
            continue
        row['summary'] = str(summary).strip()
        row['type'] = _normalize_task_type(row.get('type'))
        row['priority'] = _normalize_task_priority(row.get('priority'))
        if row.get('due_date') is not None:
            row['due_date'] = _repair_date_only(row.get('due_date'))
        if row.get('planned_start_date') is not None:
            row['planned_start_date'] = _repair_date_only(row.get('planned_start_date'))
        repaired.append(row)
    return repaired


def _repair_blueprint_tasks(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        row = _as_dict(item)
        summary = row.get('summary') or row.get('title') or row.get('name')
        if not summary:
            continue
        row['summary'] = str(summary).strip()
        row['type'] = _normalize_task_type(row.get('type'))
        row['priority'] = _normalize_task_priority(row.get('priority'))
        row['due_date'] = _repair_date_only(row.get('due_date'))
        row['planned_start_date'] = _repair_date_only(row.get('planned_start_date'))
        if row.get('description') is not None:
            row['description'] = str(row['description']).strip() or None
        repaired.append(row)
    return repaired


def _repair_column_names(items: list[Any]) -> list[Any]:
    repaired: list[Any] = []
    for index, item in enumerate(items):
        if isinstance(item, str) and item.strip():
            repaired.append({'name': item.strip(), 'position': index})
            continue
        if isinstance(item, dict):
            col = dict(item)
            name = col.get('name') or col.get('title') or col.get('label')
            if name:
                col['name'] = str(name).strip()
                if not isinstance(col.get('position'), int):
                    col['position'] = index
                repaired.append(col)
            continue
        text = str(item).strip()
        if text:
            repaired.append({'name': text, 'position': index})
    return repaired


def _repair_spreadsheets(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        doc = _as_dict(item)
        name = doc.get('name') or doc.get('title')
        if not name:
            continue
        doc['name'] = str(name).strip()
        sheets = _as_list(doc.get('sheets'))
        if not sheets and doc.get('columns') is not None:
            sheets = [
                {
                    'name': doc.get('sheet_name') or 'Sheet 1',
                    'columns': doc.get('columns'),
                    'rows': doc.get('rows') or [],
                }
            ]
        doc['sheets'] = [_repair_spreadsheet_sheet(s) for s in sheets if _as_dict(s)]
        if doc['sheets']:
            repaired.append(doc)
    return repaired


def _repair_spreadsheet_sheet(item: Any) -> dict[str, Any]:
    sheet = _as_dict(item)
    name = sheet.get('name') or sheet.get('title') or 'Sheet 1'
    sheet['name'] = str(name).strip()
    sheet['columns'] = _repair_column_names(_as_list(sheet.get('columns')))
    rows_raw = sheet.get('rows') or []
    if not isinstance(rows_raw, list):
        rows_raw = []
    rows: list[list[str]] = []
    for row in rows_raw:
        if isinstance(row, list):
            rows.append([str(cell) for cell in row])
        elif isinstance(row, dict):
            rows.append([str(v) for v in row.values()])
        elif row is not None:
            rows.append([str(row)])
    sheet['rows'] = rows
    return sheet


def _repair_plan_calendar_rows(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        row = _as_dict(item)
        title = row.get('title') or row.get('name') or row.get('milestone')
        if not title:
            continue
        row['title'] = str(title).strip()
        timing = row.get('timing_hint') or row.get('date') or row.get('start_date')
        if timing is not None:
            row['timing_hint'] = str(timing).strip()
        repaired.append(row)
    return repaired


def _repair_calendar_events(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        row = _as_dict(item)
        title = row.get('title') or row.get('name') or row.get('milestone')
        if not title:
            continue
        row['title'] = str(title).strip()
        start = repair_iso_datetime(row.get('start_datetime') or row.get('start') or row.get('date'))
        end = repair_iso_datetime(row.get('end_datetime') or row.get('end'))
        if start and not end:
            try:
                parsed = datetime.fromisoformat(start.replace('Z', '+00:00'))
                end_dt = parsed + timedelta(hours=1)
                end = end_dt.astimezone(dt_timezone.utc).replace(microsecond=0).isoformat().replace(
                    '+00:00', 'Z'
                )
            except ValueError:
                end = start
        if start:
            row['start_datetime'] = start
        if end:
            row['end_datetime'] = end
        row['timezone'] = str(row.get('timezone') or 'UTC')
        row['is_all_day'] = bool(row.get('is_all_day', False))
        if row.get('description') is not None:
            row['description'] = str(row['description']).strip() or None
        if row.get('start_datetime') and row.get('end_datetime'):
            repaired.append(row)
    return repaired


def _repair_plan_decision_rows(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        row = _as_dict(item)
        title = row.get('title') or row.get('question') or row.get('name')
        if not title:
            continue
        row['title'] = str(title).strip()
        if row.get('description') is not None:
            row['description'] = str(row['description']).strip() or None
        repaired.append(row)
    return repaired


def _repair_decisions(items: list[Any]) -> list[dict[str, Any]]:
    return _repair_plan_decision_rows(items)


def _repair_miro_boards(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for item in items:
        row = _as_dict(item)
        name = row.get('name') or row.get('title')
        if not name:
            continue
        row['name'] = str(name).strip()
        row['sticky_notes'] = _repair_miro_sticky_notes(_as_list(row.get('sticky_notes')))
        repaired.append(row)
    return repaired


def _repair_miro_sticky_notes(items: list[Any]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    layout = (
        {'x': 80.0, 'y': 80.0},
        {'x': 320.0, 'y': 80.0},
        {'x': 200.0, 'y': 280.0},
        {'x': 80.0, 'y': 420.0},
    )
    for index, item in enumerate(items):
        if isinstance(item, str) and item.strip():
            pos = layout[index % len(layout)]
            repaired.append(
                {
                    'content': item.strip(),
                    'x': pos['x'],
                    'y': pos['y'],
                    'width': 200,
                    'height': 120,
                }
            )
            continue
        row = _as_dict(item)
        content = row.get('content') or row.get('text') or row.get('title')
        if not content:
            continue
        pos = layout[index % len(layout)]
        repaired.append(
            {
                'content': str(content).strip(),
                'x': float(row.get('x', pos['x'])),
                'y': float(row.get('y', pos['y'])),
                'width': float(row.get('width', 200)),
                'height': float(row.get('height', 120)),
            }
        )
    return repaired
