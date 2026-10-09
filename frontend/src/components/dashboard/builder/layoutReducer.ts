import type { CSSProperties } from 'react';
import type { DashboardLayoutConfiguration, DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';

/** Pure browser interaction math. Persistence and final validation belong to Django. */
export function createLayoutReducer(configuration: DashboardLayoutConfiguration) {
  const GRID_COLUMNS = configuration.columns;
  const ROW_HEIGHT = configuration.row_height;
  const GAP = configuration.gap;
  const RESIZE_STEP = configuration.resize_step;
  const POSITION_EPSILON = configuration.position_epsilon;
  const minimumResizeHeight = Object.fromEntries(configuration.widgets.map((widget) => [widget.id, widget.min_resize_height]));
  function widgetStyle(widget: Widget): CSSProperties {
    const rowStep = ROW_HEIGHT + GAP;
    return {
      position: 'absolute',
      left: `calc(${(widget.x / GRID_COLUMNS) * 100}% + ${(widget.x * GAP) / GRID_COLUMNS}px)`,
      top: widget.y * rowStep,
      width: `calc(${(widget.w / GRID_COLUMNS) * 100}% + ${(widget.w * GAP) / GRID_COLUMNS - GAP}px)`,
      height: widget.h * rowStep - GAP,
    };
  }

  /** Convert a configured pixel step to the responsive layout's column/row units. */
  function resizeDelta(dx: number, dy: number, columnStep: number): { dw: number; dh: number } {
    return {
      dw: Math.round(dx / RESIZE_STEP) * RESIZE_STEP / columnStep,
      dh: Math.round(dy / RESIZE_STEP) * RESIZE_STEP / (ROW_HEIGHT + GAP),
    };
  }
  function overlaps(a: Widget, b: Widget): boolean {
    return a.x < b.x + b.w - POSITION_EPSILON && b.x < a.x + a.w - POSITION_EPSILON && a.y < b.y + b.h - POSITION_EPSILON && b.y < a.y + a.h - POSITION_EPSILON;
  }

  /** Follow the preceding card's bottom edge without changing horizontal positions or sizes.
   */
  function compactWidgets(widgets: Widget[]): Widget[] {
    const result: Widget[] = [];
    for (const widget of [...widgets].sort((a, b) => a.y - b.y || a.x - b.x)) {
      const above = result.filter((other) => (widget.x < other.x + other.w - POSITION_EPSILON && other.x < widget.x + widget.w - POSITION_EPSILON));
      const y = Math.max(0, ...above.map((other) => other.y + other.h));
      result.push({ ...widget, y });
    }
    return result.sort((a, b) => a.y - b.y || a.x - b.x);
  }

  /** Keep the edited widget at the snapped target and pack every other card upward. */
  function placeWidget(widgets: Widget[], edited: Widget): Widget[] {
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

  function moveWidget(widgets: Widget[], id: string, dx: number, dy: number): Widget[] {
    const widget = widgets.find((item) => item.id === id);
    if (!widget || (!dx && !dy)) return widgets;
    const moved = {
      ...widget,
      x: Math.max(0, Math.min(GRID_COLUMNS - widget.w, widget.x + dx)),
      y: Math.max(0, Math.min(configuration.max_y, widget.y + dy)),
    };
    // Small adjustments stop at neighboring edges instead of rearranging the row.
    // Larger drags keep the existing reposition behavior.
    if (Math.abs(dx) < widget.w / 2 && Math.abs(dy) < widget.h / 2) {
      for (const other of widgets) {
        if (other.id === id || !overlaps(moved, other)) continue;
        if (dx < 0 && widget.x >= other.x + other.w) moved.x = Math.max(moved.x, other.x + other.w);
        else if (dx > 0 && widget.x + widget.w <= other.x) moved.x = Math.min(moved.x, other.x - widget.w);
        else if (dy < 0 && widget.y >= other.y + other.h) moved.y = Math.max(moved.y, other.y + other.h);
        else if (dy > 0 && widget.y + widget.h <= other.y) moved.y = Math.min(moved.y, other.y - widget.h);
      }
      if (widgets.some((other) => other.id !== id && overlaps(moved, other))) return widgets;
    }
    if (widgets.every((other) => other.id === id || !overlaps(moved, other))) {
      return compactWidgets(widgets.map((other) => other.id === id ? moved : other));
    }
    return compactWidgets(placeWidget(widgets, moved));
  }

  function resizeWidget(widgets: Widget[], id: string, dw: number, dh: number): Widget[] {
    const widget = widgets.find((item) => item.id === id);
    if (!widget || (!dw && !dh)) return widgets;
    const resized = {
      ...widget,
      w: Math.max(configuration.min_width, Math.min(GRID_COLUMNS - widget.x, widget.w + dw)),
      h: Math.max(minimumResizeHeight[id] ?? configuration.min_height, Math.min(configuration.max_height, widget.h + dh)),
    };
    if (widgets.every((other) => other.id === id || !overlaps(resized, other))) {
      return compactWidgets(widgets.map((other) => other.id === id ? resized : other));
    }
    return compactWidgets(placeWidget(widgets, resized));
  }

  function addWidget(widgets: Widget[], definition: Widget): Widget[] {
    if (widgets.length >= configuration.max_widgets || widgets.some((widget) => widget.id === definition.id)) return widgets;
    const y = widgets.reduce((bottom, widget) => Math.max(bottom, widget.y + widget.h), 0);
    return compactWidgets([...widgets, { ...definition, y }]);
  }

  /** Preview and commit a new widget at the same cell, keeping its preset size. */
  function dropWidget(widgets: Widget[], definition: Widget, x: number, y: number): Widget[] {
    if (widgets.length >= configuration.max_widgets || widgets.some((widget) => widget.id === definition.id)) return widgets;
    return compactWidgets(placeWidget(widgets, {
      ...definition,
      x: Math.max(0, Math.min(GRID_COLUMNS - definition.w, x)),
      y: Math.max(0, Math.min(configuration.max_y, y)),
    }));
  }

  function removeWidget(widgets: Widget[], id: string): Widget[] {
    const remaining = widgets.filter((widget) => widget.id !== id);
    if (remaining.length === widgets.length) return widgets;
    return compactWidgets(remaining);
  }

  return { resizeDelta, widgetStyle, compactWidgets, placeWidget, moveWidget, resizeWidget, addWidget, dropWidget, removeWidget };
}

export type DashboardLayoutReducer = ReturnType<typeof createLayoutReducer>;
