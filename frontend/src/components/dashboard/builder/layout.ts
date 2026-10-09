import type { DashboardWidgetPosition } from '@/types/dashboardLayout';

function overlaps(left: DashboardWidgetPosition, right: DashboardWidgetPosition): boolean {
  return left.x < right.x + right.w && right.x < left.x + left.w
    && left.y < right.y + right.h && right.y < left.y + left.h;
}

/** Keep the moved card in the requested column and find the first free row. */
export function placeWidget(
  widgets: DashboardWidgetPosition[],
  candidate: DashboardWidgetPosition,
  columns: number,
  maxRows: number,
): DashboardWidgetPosition[] {
  if (candidate.w < 1 || candidate.h < 1 || candidate.w > columns || candidate.h > maxRows) return widgets;
  const others = widgets.filter((widget) => widget.id !== candidate.id);
  const x = Math.max(0, Math.min(columns - candidate.w, candidate.x));
  const requestedY = Math.max(0, Math.min(maxRows - candidate.h, candidate.y));
  const freeAt = (y: number) => {
    const placed = { ...candidate, x, y };
    return others.every((widget) => !overlaps(placed, widget));
  };
  const rows = [
    ...Array.from({ length: maxRows - candidate.h - requestedY + 1 }, (_, index) => requestedY + index),
    ...Array.from({ length: requestedY }, (_, index) => index),
  ];
  const y = rows.find(freeAt);
  if (y === undefined) return widgets;
  const placed = { ...candidate, x, y };
  return widgets.some((widget) => widget.id === candidate.id)
    ? widgets.map((widget) => widget.id === candidate.id ? placed : widget)
    : [...widgets, placed];
}
