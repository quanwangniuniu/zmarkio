"""Fallback draft content when the LLM omits enabled optional modules."""

from __future__ import annotations

from typing import Any

from core.services.quick_start.constants import (
    MIN_DECISIONS_WHEN_ENABLED,
    MIN_MIRO_STICKY_NOTES_PER_BOARD,
    MODULE_DECISIONS,
    MODULE_MIRO,
)

_MIRO_STICKY_LAYOUT = (
    {'x': 80.0, 'y': 80.0},
    {'x': 320.0, 'y': 80.0},
    {'x': 200.0, 'y': 280.0},
    {'x': 80.0, 'y': 420.0},
)


def ensure_quick_start_module_minimums(
    blueprint: dict[str, Any],
    selected_modules: dict[str, bool],
) -> dict[str, Any]:
    """
    Ensure enabled decisions/miro modules meet minimum counts.

    Used when the model returns empty arrays for optional modules that were enabled.
    """
    project = blueprint.get('project') or {}
    project_name = str(project.get('name') or 'Campaign').strip() or 'Campaign'

    if selected_modules.get(MODULE_DECISIONS):
        decisions = list(blueprint.get('decisions') or [])
        templates = [
            f'{project_name}: Channel budget allocation',
            f'{project_name}: Launch timing and creative testing',
            f'{project_name}: Performance optimization priorities',
        ]
        while len(decisions) < MIN_DECISIONS_WHEN_ENABLED:
            title = templates[len(decisions) % len(templates)]
            decisions.append({'title': title, 'description': None})
        blueprint['decisions'] = decisions

    if selected_modules.get(MODULE_MIRO):
        boards = list(blueprint.get('miro_boards') or [])
        if not boards:
            boards.append({'name': f'{project_name} planning board', 'sticky_notes': []})

        board = dict(boards[0])
        board_name = str(board.get('name') or '').strip() or f'{project_name} planning board'
        notes = list(board.get('sticky_notes') or [])
        note_templates = [
            f'{project_name}: Goals and KPIs',
            f'{project_name}: Audience and channel mix',
            f'{project_name}: Launch milestones',
            f'{project_name}: Risks and dependencies',
        ]
        while len(notes) < MIN_MIRO_STICKY_NOTES_PER_BOARD:
            content = note_templates[len(notes) % len(note_templates)]
            layout = _MIRO_STICKY_LAYOUT[len(notes) % len(_MIRO_STICKY_LAYOUT)]
            notes.append(
                {
                    'content': content,
                    'x': layout['x'],
                    'y': layout['y'],
                    'width': 200,
                    'height': 120,
                }
            )
        board['name'] = board_name
        board['sticky_notes'] = notes
        boards[0] = board
        blueprint['miro_boards'] = boards

    return blueprint
