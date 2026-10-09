"""Dashboard configuration, persistence and legacy layout conversion.

The configuration returned by the layout API is the only production source of
widget presets and layout limits. min_resize_height is an interaction constraint;
min_height preserves the storage contract for smaller legacy cards.
"""

import json
from pathlib import Path

from .models import DashboardLayout


LAYOUT_CONFIGURATION = json.loads(Path(__file__).with_name('layout_config.json').read_text())
DEFAULT_WIDGETS = [
    {key: widget[key] for key in ('id', 'x', 'y', 'w', 'h')}
    for widget in LAYOUT_CONFIGURATION['widgets']
]
WIDGET_IDS = {widget['id'] for widget in DEFAULT_WIDGETS}
POSITION_EPSILON = LAYOUT_CONFIGURATION['position_epsilon']


def is_legacy_section_title(widget):
    """Recognize the removed title widget only for compatibility cleanup."""
    return isinstance(widget, dict) and isinstance(widget.get('id'), str) and widget['id'].startswith('section-title-')


def remove_legacy_section_titles(widgets):
    """Remove obsolete title entries without moving or resizing ordinary widgets."""
    return [widget for widget in widgets if not is_legacy_section_title(widget)]


def positions_overlap(a, b):
    return (
        a['x'] < b['x'] + b['w'] - POSITION_EPSILON
        and b['x'] < a['x'] + a['w'] - POSITION_EPSILON
        and a['y'] < b['y'] + b['h'] - POSITION_EPSILON
        and b['y'] < a['y'] + a['h'] - POSITION_EPSILON
    )


def widgets_for_response(raw):
    """Read old layouts without generating editable title widgets or changing card sizes."""
    if isinstance(raw, list):
        return remove_legacy_section_titles(raw)
    if not isinstance(raw, dict) or not isinstance(raw.get('items'), list):
        return DEFAULT_WIDGETS

    widgets = []
    seen = set()
    for item in raw['items']:
        if not isinstance(item, dict):
            continue
        is_group = item.get('kind') == 'group'
        children = item.get('children', []) if is_group else [item]
        if not isinstance(children, list):
            continue
        for child in children:
            if not isinstance(child, dict) or child.get('id') not in WIDGET_IDS or child['id'] in seen:
                continue
            try:
                x = child['x'] + (item['x'] if is_group else 0)
                y = child['y'] + (item['y'] if is_group else 0)
                w, h = child['w'], child['h']
            except (KeyError, TypeError):
                continue
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in (x, y, w, h)):
                continue
            if not (
                0 <= x < LAYOUT_CONFIGURATION['columns']
                and 0 <= y <= LAYOUT_CONFIGURATION['max_y']
                and LAYOUT_CONFIGURATION['min_width'] <= w <= LAYOUT_CONFIGURATION['columns'] - x
                and LAYOUT_CONFIGURATION['min_height'] <= h <= LAYOUT_CONFIGURATION['max_height']
            ):
                continue
            widgets.append({'id': child['id'], 'x': x, 'y': y, 'w': w, 'h': h})
            seen.add(child['id'])
    return widgets


def save_layout(project, user, widgets):
    layout, _ = DashboardLayout.objects.update_or_create(
        project=project, user=user, defaults={'widgets': widgets},
    )
    return layout
