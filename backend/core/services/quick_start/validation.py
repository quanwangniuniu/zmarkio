"""Input and blueprint validation for Quick Start."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from core.services.quick_start.constants import (
    MAX_COMBINED_INPUT_LENGTH,
    MAX_PROMPT_LENGTH,
    MAX_SUPPLEMENT_LENGTH,
    MIN_CALENDAR_EVENTS_WHEN_ENABLED,
    MIN_DECISIONS_WHEN_ENABLED,
    MIN_MIRO_BOARDS_WHEN_ENABLED,
    MIN_MIRO_STICKY_NOTES_PER_BOARD,
    MIN_PROMPT_LENGTH,
    MIN_TASKS_WHEN_ENABLED,
    MODULE_CALENDAR,
    MODULE_DECISIONS,
    MODULE_MIRO,
    MODULE_SPREADSHEET,
    MODULE_TASKS,
)
from core.services.quick_start.dates import (
    normalize_quick_start_calendar_events,
    normalize_quick_start_date,
    normalize_quick_start_spreadsheet_cell,
    normalize_quick_start_task_dates,
)
from core.services.quick_start.exceptions import QuickStartValidationError
from core.services.quick_start.repair import repair_blueprint

TASK_TYPES = frozenset(
    {
        'budget',
        'asset',
        'retrospective',
        'report',
        'execution',
        'scaling',
        'alert',
        'experiment',
        'optimization',
        'communication',
        'platform_policy_update',
    }
)

TASK_PRIORITIES = frozenset({'HIGHEST', 'HIGH', 'MEDIUM', 'LOW', 'LOWEST'})

# Common LLM aliases → canonical task types (avoid hard failures on stage-2 blueprint).
_TASK_TYPE_ALIASES: dict[str, str] = {
    'planning': 'execution',
    'plan': 'execution',
    'setup': 'execution',
    'launch': 'execution',
    'campaign': 'execution',
    'strategy': 'execution',
    'media': 'execution',
    'marketing': 'execution',
    'creative': 'asset',
    'content': 'asset',
    'design': 'asset',
    'reporting': 'report',
    'analysis': 'report',
    'analytics': 'report',
    'research': 'experiment',
    'test': 'experiment',
    'testing': 'experiment',
    'optimize': 'optimization',
    'optimisation': 'optimization',
    'scale': 'scaling',
    'monitoring': 'alert',
    'comms': 'communication',
    'policy': 'platform_policy_update',
}


def normalize_task_type(value: Any) -> str:
    """Map LLM task types to allowed TASK_TYPES; default to execution."""
    if value is None or value == '':
        return 'execution'
    text = str(value).strip().lower()
    if text in TASK_TYPES:
        return text
    if text in _TASK_TYPE_ALIASES:
        return _TASK_TYPE_ALIASES[text]
    if 'budget' in text:
        return 'budget'
    if 'asset' in text or 'creative' in text:
        return 'asset'
    if 'report' in text or 'readout' in text:
        return 'report'
    if 'experiment' in text or 'test' in text:
        return 'experiment'
    if 'optim' in text:
        return 'optimization'
    if 'scale' in text:
        return 'scaling'
    if 'retro' in text:
        return 'retrospective'
    return 'execution'


def normalize_task_priority(value: Any) -> str:
    if value is None or value == '':
        return 'MEDIUM'
    text = str(value).strip().upper()
    if text in TASK_PRIORITIES:
        return text
    return 'MEDIUM'

DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def validate_prompt_text(prompt: str) -> str:
    cleaned = (prompt or '').strip()
    if len(cleaned) < MIN_PROMPT_LENGTH:
        raise QuickStartValidationError(
            f'prompt must be at least {MIN_PROMPT_LENGTH} characters after trimming.'
        )
    if len(cleaned) > MAX_PROMPT_LENGTH:
        raise QuickStartValidationError(
            f'prompt must be at most {MAX_PROMPT_LENGTH} characters after trimming.'
        )
    return cleaned


def validate_supplement_text(supplement: str | None) -> str | None:
    if supplement is None:
        return None
    cleaned = supplement.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_SUPPLEMENT_LENGTH:
        raise QuickStartValidationError(
            f'supplement must be at most {MAX_SUPPLEMENT_LENGTH} characters after trimming.'
        )
    return cleaned


def validate_combined_input_length(*, prompt: str, supplement: str | None = None) -> None:
    supplement_len = len((supplement or '').strip())
    if len(prompt) + supplement_len > MAX_COMBINED_INPUT_LENGTH:
        raise QuickStartValidationError(
            f'prompt and supplement combined must be at most {MAX_COMBINED_INPUT_LENGTH} characters after trimming.'
        )


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QuickStartValidationError(f'{field} must be an object.')
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise QuickStartValidationError(f'{field} must be an array.')
    return value


def _parse_date(value: Any, field: str) -> str | None:
    if value is None or value == '':
        return None
    if isinstance(value, str) and DATE_RE.match(value.strip()):
        return value.strip()
    return None


def _parse_datetime(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuickStartValidationError(f'{field} is required.')
    text = value.strip()
    try:
        normalized = text.replace('Z', '+00:00')
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise QuickStartValidationError(f'{field} must be ISO-8601 datetime.') from exc
    return text


def validate_and_normalize_blueprint(
    raw: Mapping[str, Any],
    selected_modules: dict[str, bool],
) -> dict[str, Any]:
    """
    Validate LLM output and return a normalized blueprint dict for storage/API.
    """
    if not isinstance(raw, Mapping):
        raise QuickStartValidationError('blueprint must be a JSON object.')

    raw = repair_blueprint(dict(raw), selected_modules)

    version = raw.get('version')
    if version != 1:
        raise QuickStartValidationError('blueprint.version must be 1.')

    project_raw = _require_dict(raw.get('project'), 'project')
    name = project_raw.get('name')
    if not isinstance(name, str) or not name.strip():
        raise QuickStartValidationError('project.name is required.')
    project = {
        'name': name.strip()[:255],
        'description': _optional_str(project_raw.get('description')),
        'objectives': _optional_str_list(project_raw.get('objectives')),
        'project_type': _optional_str_list(project_raw.get('project_type')),
        'advertising_platforms': _optional_str_list(project_raw.get('advertising_platforms')),
    }

    tasks: list[dict[str, Any]] = []
    if selected_modules.get(MODULE_TASKS):
        tasks = _validate_tasks(_require_list(raw.get('tasks'), 'tasks'))
        if len(tasks) < MIN_TASKS_WHEN_ENABLED:
            raise QuickStartValidationError(
                f'At least {MIN_TASKS_WHEN_ENABLED} tasks are required when tasks module is enabled.'
            )

    spreadsheets: list[dict[str, Any]] = []
    if selected_modules.get(MODULE_SPREADSHEET):
        spreadsheets = _validate_spreadsheets(_require_list(raw.get('spreadsheets'), 'spreadsheets'))
        if not spreadsheets:
            raise QuickStartValidationError(
                'At least one spreadsheet is required when spreadsheet module is enabled.'
            )

    calendar_events: list[dict[str, Any]] = []
    if selected_modules.get(MODULE_CALENDAR):
        calendar_events = _validate_calendar_events(
            _require_list(raw.get('calendar_events'), 'calendar_events')
        )
        if len(calendar_events) < MIN_CALENDAR_EVENTS_WHEN_ENABLED:
            raise QuickStartValidationError(
                f'At least {MIN_CALENDAR_EVENTS_WHEN_ENABLED} calendar event is required when calendar is enabled.'
            )

    decisions: list[dict[str, Any]] = []
    if selected_modules.get(MODULE_DECISIONS):
        decisions = _validate_decisions(_require_list(raw.get('decisions'), 'decisions'))
        if len(decisions) < MIN_DECISIONS_WHEN_ENABLED:
            raise QuickStartValidationError(
                f'At least {MIN_DECISIONS_WHEN_ENABLED} decision is required when decisions module is enabled.'
            )

    miro_boards: list[dict[str, Any]] = []
    if selected_modules.get(MODULE_MIRO):
        miro_boards = _validate_miro_boards(_require_list(raw.get('miro_boards'), 'miro_boards'))
        if len(miro_boards) < MIN_MIRO_BOARDS_WHEN_ENABLED:
            raise QuickStartValidationError(
                f'At least {MIN_MIRO_BOARDS_WHEN_ENABLED} Miro board is required when miro module is enabled.'
            )

    return {
        'version': 1,
        'project': project,
        'tasks': tasks,
        'spreadsheets': spreadsheets,
        'calendar_events': calendar_events,
        'decisions': decisions,
        'miro_boards': miro_boards,
    }


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        text = str(value).strip()
        return text or None
    stripped = value.strip()
    return stripped or None


def _optional_str_list(value: Any) -> list[str]:
    """Accept LLM output as list, comma-separated string, or single string."""
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
        raise QuickStartValidationError('Expected array of strings.')
    normalized: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _validate_tasks(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        row = _require_dict(item, f'tasks[{index}]')
        summary = row.get('summary')
        if not isinstance(summary, str) or not summary.strip():
            raise QuickStartValidationError(f'tasks[{index}].summary is required.')
        task_type = normalize_task_type(row.get('type'))
        priority = normalize_task_priority(row.get('priority'))
        due_date = _parse_date(row.get('due_date'), f'tasks[{index}].due_date')
        planned_start_date = _parse_date(
            row.get('planned_start_date'), f'tasks[{index}].planned_start_date'
        )
        planned_start_date, due_date = normalize_quick_start_task_dates(
            planned_start_date, due_date
        )
        normalized.append(
            {
                'summary': summary.strip()[:255],
                'description': _optional_str(row.get('description')),
                'type': task_type,
                'priority': priority,
                'due_date': due_date,
                'planned_start_date': planned_start_date,
            }
        )
    return normalized


def _validate_spreadsheets(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for s_index, item in enumerate(items):
        sheet_doc = _require_dict(item, f'spreadsheets[{s_index}]')
        ss_name = sheet_doc.get('name')
        if not isinstance(ss_name, str) or not ss_name.strip():
            raise QuickStartValidationError(f'spreadsheets[{s_index}].name is required.')
        sheets_raw = _require_list(sheet_doc.get('sheets'), f'spreadsheets[{s_index}].sheets')
        if not sheets_raw:
            raise QuickStartValidationError(f'spreadsheets[{s_index}] must include at least one sheet.')
        sheets: list[dict[str, Any]] = []
        for sh_index, sh_item in enumerate(sheets_raw):
            sh = _require_dict(sh_item, f'spreadsheets[{s_index}].sheets[{sh_index}]')
            sh_name = sh.get('name')
            if not isinstance(sh_name, str) or not sh_name.strip():
                raise QuickStartValidationError(
                    f'spreadsheets[{s_index}].sheets[{sh_index}].name is required.'
                )
            columns_raw = _require_list(
                sh.get('columns'), f'spreadsheets[{s_index}].sheets[{sh_index}].columns'
            )
            if len(columns_raw) < 3:
                raise QuickStartValidationError(
                    f'spreadsheets[{s_index}].sheets[{sh_index}] needs at least 3 columns.'
                )
            columns: list[dict[str, Any]] = []
            for c_index, col in enumerate(columns_raw):
                col_doc = _require_dict(
                    col, f'spreadsheets[{s_index}].sheets[{sh_index}].columns[{c_index}]'
                )
                col_name = col_doc.get('name')
                if not isinstance(col_name, str) or not col_name.strip():
                    raise QuickStartValidationError('column name is required.')
                position = col_doc.get('position', c_index)
                if not isinstance(position, int):
                    position = c_index
                columns.append({'name': col_name.strip()[:200], 'position': position})
            rows_raw = sh.get('rows') or []
            if not isinstance(rows_raw, list):
                raise QuickStartValidationError('rows must be an array.')
            rows: list[list[str]] = []
            for r_index, row in enumerate(rows_raw):
                if not isinstance(row, list):
                    raise QuickStartValidationError(f'rows[{r_index}] must be an array.')
                rows.append(
                    [normalize_quick_start_spreadsheet_cell(str(cell)) for cell in row]
                )
            sheets.append({'name': sh_name.strip()[:200], 'columns': columns, 'rows': rows})
        normalized.append({'name': ss_name.strip()[:200], 'sheets': sheets})
    return normalized


def _validate_calendar_events(items: list[Any]) -> list[dict[str, Any]]:
    draft: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        row = _require_dict(item, f'calendar_events[{index}]')
        title = row.get('title')
        if not isinstance(title, str) or not title.strip():
            raise QuickStartValidationError(f'calendar_events[{index}].title is required.')
        _parse_datetime(row.get('start_datetime'), f'calendar_events[{index}].start_datetime')
        _parse_datetime(row.get('end_datetime'), f'calendar_events[{index}].end_datetime')
        draft.append(
            {
                'title': title.strip()[:255],
                'description': _optional_str(row.get('description')),
                'start_datetime': row.get('start_datetime'),
                'end_datetime': row.get('end_datetime'),
                'timezone': (row.get('timezone') or 'UTC'),
                'is_all_day': bool(row.get('is_all_day', False)),
            }
        )
    return normalize_quick_start_calendar_events(draft)


def _validate_decisions(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        row = _require_dict(item, f'decisions[{index}]')
        title = row.get('title')
        if not isinstance(title, str) or not title.strip():
            raise QuickStartValidationError(f'decisions[{index}].title is required.')
        normalized.append(
            {'title': title.strip()[:255], 'description': _optional_str(row.get('description'))}
        )
    return normalized


_MIRO_STICKY_LAYOUT = (
    (80.0, 80.0),
    (320.0, 80.0),
    (200.0, 280.0),
    (80.0, 420.0),
)


def _validate_miro_boards(items: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        row = _require_dict(item, f'miro_boards[{index}]')
        name = row.get('name')
        if not isinstance(name, str) or not name.strip():
            raise QuickStartValidationError(f'miro_boards[{index}].name is required.')
        sticky_notes = _validate_miro_sticky_notes(
            _require_list(row.get('sticky_notes'), f'miro_boards[{index}].sticky_notes'),
            board_index=index,
        )
        if len(sticky_notes) < MIN_MIRO_STICKY_NOTES_PER_BOARD:
            raise QuickStartValidationError(
                f'miro_boards[{index}] needs at least {MIN_MIRO_STICKY_NOTES_PER_BOARD} sticky_notes.'
            )
        normalized.append({'name': name.strip()[:255], 'sticky_notes': sticky_notes})
    return normalized


def _validate_miro_sticky_notes(
    items: list[Any],
    *,
    board_index: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for note_index, item in enumerate(items):
        row = _require_dict(item, f'miro_boards[{board_index}].sticky_notes[{note_index}]')
        content = row.get('content')
        if not isinstance(content, str) or not content.strip():
            raise QuickStartValidationError(
                f'miro_boards[{board_index}].sticky_notes[{note_index}].content is required.'
            )
        layout = _MIRO_STICKY_LAYOUT[note_index % len(_MIRO_STICKY_LAYOUT)]
        x = row.get('x', layout[0])
        y = row.get('y', layout[1])
        width = row.get('width', 200)
        height = row.get('height', 120)
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            x, y = layout
        if not isinstance(width, (int, float)) or width <= 0:
            width = 200
        if not isinstance(height, (int, float)) or height <= 0:
            height = 120
        normalized.append(
            {
                'content': content.strip()[:2000],
                'x': float(x),
                'y': float(y),
                'width': float(width),
                'height': float(height),
            }
        )
    return normalized
