"""Widget IDs, default positions, and size limits shared by the layout API.

React components remain in the frontend; only positions are persisted.
"""

GRID_COLUMNS = 12
MAX_ROWS = 200
MAX_WIDGETS = 20
MIN_WIDGET_WIDTH = 3

WIDGET_CATALOG = (
    # Progress cards
    {'id': 'overall-progress', 'label': 'Overall progress', 'x': 0, 'y': 0, 'w': 3, 'h': 5, 'min_h': 4},
    {'id': 'task-completion-rate', 'label': 'Task completion rate', 'x': 3, 'y': 0, 'w': 3, 'h': 5, 'min_h': 4},
    {'id': 'overdue-tasks', 'label': 'Overdue tasks', 'x': 6, 'y': 0, 'w': 3, 'h': 5, 'min_h': 4},
    {'id': 'needs-attention', 'label': 'Needs attention', 'x': 9, 'y': 0, 'w': 3, 'h': 5, 'min_h': 4},
    # Project summaries and charts
    {'id': 'decisions-summary', 'label': 'Decisions', 'x': 0, 'y': 5, 'w': 4, 'h': 18, 'min_h': 6},
    {'id': 'tasks-summary', 'label': 'Tasks', 'x': 4, 'y': 5, 'w': 4, 'h': 18, 'min_h': 6},
    {'id': 'operations-summary', 'label': 'Operations', 'x': 8, 'y': 5, 'w': 4, 'h': 18, 'min_h': 6},
    {'id': 'status-breakdown', 'label': 'Status breakdown', 'x': 0, 'y': 23, 'w': 6, 'h': 10, 'min_h': 5},
    {'id': 'priority-distribution', 'label': 'Priority distribution', 'x': 6, 'y': 23, 'w': 6, 'h': 10, 'min_h': 5},
    {'id': 'created-vs-completed', 'label': 'Created vs completed', 'x': 0, 'y': 33, 'w': 12, 'h': 10, 'min_h': 5},
    # Other existing dashboard cards
    {'id': 'custom-kpis', 'label': 'Custom KPIs', 'x': 0, 'y': 43, 'w': 12, 'h': 8, 'min_h': 4},
    {'id': 'meetings', 'label': 'Meetings & action items', 'x': 0, 'y': 51, 'w': 6, 'h': 8, 'min_h': 4},
    {'id': 'activity', 'label': 'Recent activity', 'x': 6, 'y': 51, 'w': 6, 'h': 8, 'min_h': 4},
    {'id': 'audit', 'label': 'Audit', 'x': 0, 'y': 59, 'w': 6, 'h': 8, 'min_h': 4},
    {'id': 'project-team', 'label': 'Project team', 'x': 6, 'y': 59, 'w': 6, 'h': 10, 'min_h': 6},
)

WIDGET_BY_ID = {widget['id']: widget for widget in WIDGET_CATALOG}
DEFAULT_WIDGETS = [
    {key: widget[key] for key in ('id', 'x', 'y', 'w', 'h')}
    for widget in WIDGET_CATALOG
]
