import type { DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';

export const GRID_COLUMNS = 12;
export const ROW_HEIGHT = 48;
export const MIN_WIDGET_ROWS: Record<string, number> = {
  'project-team': 6,
  meetings: 4,
  activity: 4,
  audit: 4,
};

function overlaps(a: Widget, b: Widget): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/** Keep the edited widget at the snapped target and pack every other card upward. */
export function placeWidget(widgets: Widget[], edited: Widget): Widget[] {
  const result = [edited];
  const remaining = widgets.filter((widget) => widget.id !== edited.id)
    .sort((a, b) => a.y - b.y || a.x - b.x);
  for (const original of remaining) {
    const widget = { ...original, y: 0 };
    while (true) {
      const blocker = result.find((placed) => overlaps(placed, widget));
      if (!blocker) break;
      widget.y = blocker.y + blocker.h;
    }
    result.push(widget);
  }
  return result.sort((a, b) => a.y - b.y || a.x - b.x);
}

export function moveWidget(widgets: Widget[], id: string, dx: number, dy: number): Widget[] {
  const widget = widgets.find((item) => item.id === id);
  if (!widget || (!dx && !dy)) return widgets;
  return placeWidget(widgets, {
    ...widget,
    x: Math.max(0, Math.min(GRID_COLUMNS - widget.w, widget.x + dx)),
    y: Math.max(0, Math.min(999, widget.y + dy)),
  });
}

export function resizeWidget(widgets: Widget[], id: string, dw: number, dh: number): Widget[] {
  const widget = widgets.find((item) => item.id === id);
  if (!widget || (!dw && !dh)) return widgets;
  return placeWidget(widgets, {
    ...widget,
    w: Math.max(1, Math.min(GRID_COLUMNS - widget.x, widget.w + dw)),
    h: Math.max(MIN_WIDGET_ROWS[id] ?? 3, Math.min(30, widget.h + dh)),
  });
}

export function addWidget(widgets: Widget[], definition: Widget): Widget[] {
  if (widgets.some((widget) => widget.id === definition.id)) return widgets;
  const y = widgets.reduce((bottom, widget) => Math.max(bottom, widget.y + widget.h), 0);
  return [...widgets, { ...definition, y }];
}

export function removeWidget(widgets: Widget[], id: string): Widget[] {
  const remaining = widgets.filter((widget) => widget.id !== id);
  if (remaining.length === widgets.length) return widgets;
  const compacted: Widget[] = [];
  for (const original of remaining.sort((a, b) => a.y - b.y || a.x - b.x)) {
    const widget = { ...original, y: 0 };
    while (true) {
      const blocker = compacted.find((placed) => overlaps(placed, widget));
      if (!blocker) break;
      widget.y = blocker.y + blocker.h;
    }
    compacted.push(widget);
  }
  return compacted.sort((a, b) => a.y - b.y || a.x - b.x);
}

/** Expand saved all-in-one Workspace layouts, including while an older API is still running. */
export function expandLegacyWorkspace(widgets: Widget[], replacements: Widget[]): Widget[] {
  const legacy = widgets.find((widget) => widget.id === 'workspace');
  if (!legacy) return widgets;
  const others = widgets.filter((widget) => widget.id !== 'workspace');
  const existingIds = new Set(others.map((widget) => widget.id));
  const placed = replacements
    .filter((widget) => !existingIds.has(widget.id))
    .map((widget) => ({ ...widget, y: legacy.y + widget.y }));
  const oldBottom = legacy.y + legacy.h;
  const expandedBottom = legacy.y + Math.max(...replacements.map((widget) => widget.y + widget.h));
  for (const original of [...others].sort((a, b) => a.y - b.y || a.x - b.x)) {
    const widget = { ...original };
    if (widget.y >= oldBottom) widget.y += expandedBottom - oldBottom;
    else if (widget.y + widget.h > legacy.y) widget.y = expandedBottom;
    while (true) {
      const blocker = placed.find((item) => overlaps(item, widget));
      if (!blocker) break;
      widget.y = blocker.y + blocker.h;
    }
    placed.push(widget);
  }
  return placed;
}
