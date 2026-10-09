"""Available dashboard cards and their initial positions.

The API sends this catalogue to the browser. Card content remains in the
existing React components; only placement is stored in DashboardLayout.
"""

GRID_COLUMNS = 12
MAX_ROWS = 200
MAX_WIDGETS = 20

WIDGET_CATALOG = (
    {'id': 'workspace', 'label': 'Project overview', 'x': 0, 'y': 0, 'w': 12, 'h': 40, 'min_h': 12},
    {'id': 'custom-kpis', 'label': 'Custom KPIs', 'x': 0, 'y': 40, 'w': 12, 'h': 8, 'min_h': 4},
    {'id': 'meetings', 'label': 'Meetings & action items', 'x': 0, 'y': 48, 'w': 6, 'h': 8, 'min_h': 4},
    {'id': 'activity', 'label': 'Recent activity', 'x': 6, 'y': 48, 'w': 6, 'h': 8, 'min_h': 4},
    {'id': 'audit', 'label': 'Audit', 'x': 0, 'y': 56, 'w': 6, 'h': 8, 'min_h': 4},
    {'id': 'project-team', 'label': 'Project team', 'x': 6, 'y': 56, 'w': 6, 'h': 10, 'min_h': 6},
)

WIDGET_BY_ID = {widget['id']: widget for widget in WIDGET_CATALOG}
DEFAULT_WIDGETS = [
    {key: widget[key] for key in ('id', 'x', 'y', 'w', 'h')}
    for widget in WIDGET_CATALOG
]
