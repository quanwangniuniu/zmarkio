import type { DashboardWidgetPosition } from '@/types/dashboardLayout';

function overlaps(left: DashboardWidgetPosition, right: DashboardWidgetPosition): boolean {
  return left.x < right.x + right.w && right.x < left.x + left.w
    && left.y < right.y + right.h && right.y < left.y + left.h;
}

/** Exchange two equally sized cards when one is dropped on the other's slot. */
export function moveWidget(
  widgets: DashboardWidgetPosition[],
  candidate: DashboardWidgetPosition,
  columns: number,
  maxRows: number,
): DashboardWidgetPosition[] {
  if (![candidate.x, candidate.y, candidate.w, candidate.h, columns, maxRows].every(Number.isFinite)) return widgets;
  const source = widgets.find((widget) => widget.id === candidate.id);
  if (!source) return placeWidget(widgets, candidate, columns, maxRows);
  const x = Math.max(0, Math.min(columns - candidate.w, candidate.x));
  const y = Math.max(0, Math.min(maxRows - candidate.h, candidate.y));
  const tryPosition = (row: number) => {
    const positioned = { ...candidate, x, y: row };
    const target = widgets.find((widget) => widget.id !== candidate.id
      && widget.x === x && widget.y === row
      && widget.w === candidate.w && widget.h === candidate.h);

    if (target) {
      const swapped = widgets.map((widget) => {
        if (widget.id === source.id) return positioned;
        if (widget.id === target.id) return { ...target, x: source.x, y: source.y };
        return widget;
      });
      if (swapped.every((widget, index) => swapped.slice(index + 1).every((other) => !overlaps(widget, other)))) {
        return swapped;
      }
    }
    return placeWidget(widgets, positioned, columns, maxRows);
  };

  const next = tryPosition(y);
  if (next !== widgets) return next;

  // A collision near the bottom may be impossible at the pointer's exact row.
  // Keep the closest valid drop slot instead of snapping the preview home.
  const step = y > source.y ? -1 : 1;
  for (let row = y + step; row !== source.y + step; row += step) {
    const fallback = tryPosition(row);
    if (fallback !== widgets) return fallback;
  }
  return widgets;
}

/** Put the moved card where it was dropped and move overlapping cards down. */
export function placeWidget(
  widgets: DashboardWidgetPosition[],
  candidate: DashboardWidgetPosition,
  columns: number,
  maxRows: number,
): DashboardWidgetPosition[] {
  if (![candidate.x, candidate.y, candidate.w, candidate.h, columns, maxRows].every(Number.isFinite)) return widgets;
  if (candidate.w < 1 || candidate.h < 1 || candidate.w > columns || candidate.h > maxRows) return widgets;
  const x = Math.max(0, Math.min(columns - candidate.w, candidate.x));
  const y = Math.max(0, Math.min(maxRows - candidate.h, candidate.y));
  const placed = new Map<string, DashboardWidgetPosition>([[candidate.id, { ...candidate, x, y }]]);
  const others = widgets
    .filter((widget) => widget.id !== candidate.id)
    .sort((left, right) => left.y - right.y || left.x - right.x);

  for (const widget of others) {
    let next = widget;
    while (true) {
      const collisions = [...placed.values()].filter((existing) => overlaps(next, existing));
      if (collisions.length === 0) break;
      next = { ...next, y: Math.max(...collisions.map((existing) => existing.y + existing.h)) };
      if (next.y + next.h > maxRows) return widgets;
    }
    placed.set(widget.id, next);
  }

  const source = widgets.find((widget) => widget.id === candidate.id);
  const freedRows = source ? source.y + source.h - (y + candidate.h) : 0;
  if (source && freedRows > 0 && candidate.w === source.w) {
    // A card moved down or made taller may have pushed adjacent cards with it.
    // When its bottom rises again, pull only that chain into the freed rows.
    const freed = [{ x: source.x, w: source.w, bottom: source.y + source.h, rows: freedRows }];
    for (const widget of others) {
      const current = placed.get(widget.id)!;
      const available = freed.filter((space) => current.y === space.bottom
        && current.x < space.x + space.w && space.x < current.x + current.w);
      if (available.length === 0) continue;
      const earliest = Math.max(0, current.y - Math.max(...available.map((space) => space.rows)));
      for (let row = earliest; row < current.y; row += 1) {
        const lifted = { ...current, y: row };
        if ([...placed.values()].every((other) => other.id === current.id || !overlaps(lifted, other))) {
          placed.set(current.id, lifted);
          freed.push({ x: current.x, w: current.w, bottom: current.y + current.h, rows: current.y - row });
          break;
        }
      }
    }
  }

  return widgets.some((widget) => widget.id === candidate.id)
    ? widgets.map((widget) => placed.get(widget.id)!)
    : [...widgets.map((widget) => placed.get(widget.id)!), placed.get(candidate.id)!];
}
