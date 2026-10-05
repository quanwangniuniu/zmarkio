import type { DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import type { DashboardGroup, DashboardItem, DashboardLayoutDocument, DashboardWidget } from '@/types/dashboardLayout';

export const GRID_COLUMNS = 12;
export const ROW_HEIGHT = 48;
export const ROW_GAP = 8;
export const ROW_STEP = ROW_HEIGHT + ROW_GAP;
export const VERTICAL_SNAP_PX = 20;
export const MIN_WIDGET_ROWS: Record<string, number> = {
  'project-team': 6,
  meetings: 4,
  activity: 4,
  audit: 4,
};

function overlaps(a: Widget, b: Widget): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

const sharesColumns = (a: Widget, b: Widget) => a.x < b.x + b.w && b.x < a.x + a.w;

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

const positionBottom = (items: Widget[]) => items.reduce((bottom, item) => Math.max(bottom, item.y + item.h), 0);

/** Snap near the preceding card's bottom; leave a deliberate larger gap alone. */
export function snapNearPrevious(items: Widget[], movingId: string, x: number, w: number, rawTopPx: number): number | null {
  const candidates = [0, ...items.filter((item) => item.id !== movingId && x < item.x + item.w && item.x < x + w)
    .map((item) => item.y + item.h)];
  const nearest = candidates.reduce<{ y: number; distance: number } | null>((best, y) => {
    const distance = Math.abs(rawTopPx - y * ROW_STEP);
    return distance <= VERTICAL_SNAP_PX && (!best || distance < best.distance) ? { y, distance } : best;
  }, null);
  return nearest?.y ?? null;
}

/** Insert at the requested cell. Only cards actually collided with are pushed down. */
function insertAt<T extends Widget>(items: T[], edited: T): T[] {
  const placed: T[] = [edited];
  for (const original of [...items].sort((a, b) => a.y - b.y || a.x - b.x)) {
    const item = { ...original };
    let blocker = placed.find((candidate) => overlaps(candidate, item));
    while (blocker) {
      item.y = blocker.y + blocker.h;
      blocker = placed.find((candidate) => overlaps(candidate, item));
    }
    placed.push(item);
  }
  return placed.sort((a, b) => a.y - b.y || a.x - b.x);
}

const groupHeight = (group: DashboardGroup) => Math.max(3, 1 + positionBottom(group.children));

/**
 * Close only the space released by a card. Each card below that space can rise
 * by at most the released height. Its prior gap from the card above therefore
 * survives, while a chain of touching cards follows the one that moved.
 */
function closeVacatedSpace<T extends Widget>(items: T[], vacancy: Widget): T[] {
  if (vacancy.h <= 0) return items;
  const result = items.map((item) => ({ ...item }));
  const spaces: Widget[] = [vacancy];
  while (spaces.length) {
    const space = spaces.shift()!;
    for (const item of [...result].sort((a, b) => a.y - b.y || a.x - b.x)) {
      if (!sharesColumns(item, space) || item.y < space.y + space.h) continue;
      const oldY = item.y;
      const firstY = Math.max(space.y, oldY - space.h);
      for (let y = firstY; y < oldY; y += 1) {
        const candidate = { ...item, y };
        if (result.every((other) => other.id === item.id || !overlaps(other, candidate))) {
          item.y = y;
          break;
        }
      }
      const shifted = oldY - item.y;
      if (shifted) spaces.push({ ...item, y: oldY + item.h - shifted, h: shifted });
    }
  }
  return result.sort((a, b) => a.y - b.y || a.x - b.x);
}

/** Keep any extra height that the user gave the group beyond its children. */
function withChildren(group: DashboardGroup, children: DashboardWidget[]): DashboardGroup {
  const previousMinimum = groupHeight(group);
  const nextMinimum = Math.max(3, 1 + positionBottom(children));
  const h = nextMinimum >= previousMinimum
    ? Math.max(group.h, nextMinimum)
    : Math.max(nextMinimum, group.h - (previousMinimum - nextMinimum));
  return { ...group, children, h };
}

function replaceGroup(items: DashboardItem[], previous: DashboardGroup, updated: DashboardGroup): DashboardItem[] {
  const others = items.filter((item) => item.id !== previous.id);
  const vacancy = { ...previous, y: previous.y + updated.h, h: previous.h - updated.h };
  return insertAt(closeVacatedSpace(others, vacancy), updated);
}

export function allWidgetIds(document: DashboardLayoutDocument): Set<string> {
  return new Set(document.items.flatMap((item) => item.kind === 'widget' ? [item.id] : item.children.map((child) => child.id)));
}

/** Fit a new card across the vacant columns at its drop row, without displacing neighbors. */
export function fitWidgetToVacancy<T extends Widget>(peers: Widget[], incoming: T, cell: { x: number; y: number }): T {
  const x = Math.max(0, Math.min(GRID_COLUMNS - 1, cell.x));
  const y = Math.max(0, cell.y);
  const fallback = { ...incoming, x: Math.min(x, GRID_COLUMNS - incoming.w), y };
  const occupied = Array.from({ length: GRID_COLUMNS }, () => false);
  for (const peer of peers) {
    if (peer.id === incoming.id || peer.y >= y + incoming.h || y >= peer.y + peer.h) continue;
    for (let column = peer.x; column < peer.x + peer.w; column += 1) occupied[column] = true;
  }
  // An empty row keeps the widget's normal size. A drop on an occupied cell
  // retains the existing collision behavior so that the other card can move.
  if (!occupied.some(Boolean) || occupied[x]) return fallback;
  let start = x;
  let end = x + 1;
  while (start > 0 && !occupied[start - 1]) start -= 1;
  while (end < GRID_COLUMNS && !occupied[end]) end += 1;
  return end - start >= 2 ? { ...incoming, x: start, y, w: end - start } : fallback;
}

export function findLocation(document: DashboardLayoutDocument, id: string): { item: DashboardItem | DashboardWidget; groupId: string | null } | null {
  for (const item of document.items) {
    if (item.id === id) return { item, groupId: null };
    if (item.kind === 'group') {
      const child = item.children.find((widget) => widget.id === id);
      if (child) return { item: child, groupId: item.id };
    }
  }
  return null;
}

export function addGroup(document: DashboardLayoutDocument, id: string, title = 'New group', cell?: { x: number; y: number }): DashboardLayoutDocument {
  if (document.items.some((item) => item.id === id)) return document;
  const group: DashboardGroup = { kind: 'group', id, title, x: 0, y: Math.max(0, Math.min(999, cell?.y ?? positionBottom(document.items))), w: 12, h: 3, children: [] };
  return { ...document, items: insertAt(document.items, group) };
}

/** Add from the palette or transfer an existing card between the canvas and a group. */
export function placeWidgetInContainer(document: DashboardLayoutDocument, widget: DashboardWidget, groupId: string | null, cell?: { x: number; y: number }): DashboardLayoutDocument {
  const source = findLocation(document, widget.id);
  if (source?.item.kind === 'group' || (groupId && !document.items.some((item) => item.kind === 'group' && item.id === groupId))) return document;
  const sourceWidget = source?.item as DashboardWidget | undefined;
  let withoutSource = document.items;
  if (sourceWidget && source?.groupId) {
    const group = document.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === source.groupId)!;
    const children = closeVacatedSpace(group.children.filter((child) => child.id !== widget.id), sourceWidget);
    withoutSource = replaceGroup(document.items, group, withChildren(group, children));
  } else if (sourceWidget) {
    withoutSource = closeVacatedSpace(document.items.filter((item) => item.id !== widget.id), sourceWidget);
  }
  const destination = groupId ? withoutSource.find((item): item is DashboardGroup => item.kind === 'group' && item.id === groupId) : null;
  const peers = destination?.children ?? withoutSource;
  const incoming: DashboardWidget = {
    ...(sourceWidget ?? widget),
    x: Math.max(0, Math.min(GRID_COLUMNS - widget.w, cell?.x ?? widget.x)),
    y: Math.max(0, Math.min(999, cell?.y ?? positionBottom(peers))),
  };
  if (destination) {
    const children = insertAt(destination.children, incoming);
    const updated = withChildren(destination, children);
    return { ...document, items: replaceGroup(withoutSource, destination, updated) };
  }
  return { ...document, items: insertAt(withoutSource, incoming) };
}

export function moveLayoutItem(document: DashboardLayoutDocument, id: string, dx: number, dy: number): DashboardLayoutDocument {
  if (!dx && !dy) return document;
  const location = findLocation(document, id);
  if (!location) return document;
  const item = location.item;
  const x = Math.max(0, Math.min(GRID_COLUMNS - item.w, item.x + dx));
  const y = Math.max(0, Math.min(999, item.y + dy));
  if (x === item.x && y === item.y) return document;
  const edited = { ...item, x, y };
  if (location.groupId) {
    const group = document.items.find((candidate): candidate is DashboardGroup => candidate.kind === 'group' && candidate.id === location.groupId)!;
    const remaining = closeVacatedSpace(group.children.filter((child) => child.id !== id), item);
    const children = insertAt(remaining, edited as DashboardWidget);
    return { ...document, items: replaceGroup(document.items, group, withChildren(group, children)) };
  }
  const remaining = closeVacatedSpace(document.items.filter((candidate) => candidate.id !== id), item);
  return { ...document, items: insertAt(remaining, edited as DashboardItem) };
}

/** Insert a whole group beside another top-level item, preserving its children. */
export function reorderLayoutGroup(document: DashboardLayoutDocument, sourceId: string, targetId: string, side: 'before' | 'after'): DashboardLayoutDocument {
  const source = document.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === sourceId);
  if (!source || sourceId === targetId || !document.items.some((item) => item.id === targetId)) return document;
  const remaining = closeVacatedSpace(document.items.filter((item) => item.id !== sourceId), source);
  const target = remaining.find((item) => item.id === targetId)!;
  if (target.kind === 'widget') {
    const index = remaining.findIndex((item) => item.id === targetId) + (side === 'after' ? 1 : 0);
    const before = remaining.slice(0, index);
    const after = remaining.slice(index);
    const precedingBottom = Math.max(0, ...before.map((item) => item.y + item.h));
    const y = Math.max(precedingBottom, after[0]?.y ?? 0);
    const shiftedBottom = y + source.h;
    const shift = Math.max(0, shiftedBottom - (after[0]?.y ?? shiftedBottom));
    return { ...document, items: [...before, { ...source, x: 0, y }, ...after.map((item) => ({ ...item, y: item.y + shift }))] };
  }
  const y = target.y + (side === 'after' ? target.h : 0);
  return { ...document, items: insertAt(remaining, { ...source, x: target.x, y }) };
}

/** Insert a widget at the edge shown in the hierarchy, including across group boundaries. */
export function insertWidgetBeside(document: DashboardLayoutDocument, widget: DashboardWidget, targetId: string, side: 'before' | 'after'): DashboardLayoutDocument {
  const target = findLocation(document, targetId);
  if (widget.id === targetId || !target) return document;
  const source = findLocation(document, widget.id);
  if (source?.item.kind === 'group') return document;
  const withoutSource = source ? removeLayoutItem(document, widget.id) : document;
  const destination = findLocation(withoutSource, targetId);
  if (!destination) return document;
  const peers = destination.groupId
    ? (withoutSource.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === destination.groupId)?.children ?? [])
    : withoutSource.items;
  const next = side === 'after' ? peers[peers.findIndex((item) => item.id === targetId) + 1] : null;
  return placeWidgetInContainer(withoutSource, (source?.item as DashboardWidget | undefined) ?? widget, destination.groupId, {
    x: next?.x ?? (destination.item.kind === 'group' ? widget.x : destination.item.x),
    y: next?.y ?? destination.item.y + (side === 'after' ? destination.item.h : 0),
  });
}

export function resizeLayoutItem(document: DashboardLayoutDocument, id: string, dw: number, dh: number): DashboardLayoutDocument {
  if (!dw && !dh) return document;
  const location = findLocation(document, id);
  if (!location) return document;
  const item = location.item;
  const w = Math.max(1, Math.min(GRID_COLUMNS - item.x, item.w + dw));
  const minimum = item.kind === 'group' ? groupHeight(item) : MIN_WIDGET_ROWS[id] ?? 3;
  const h = Math.max(minimum, Math.min(item.kind === 'group' ? 120 : 30, item.h + dh));
  if (w === item.w && h === item.h) return document;
  const edited = { ...item, w, h };
  const vacancy = { ...item, y: item.y + h, h: item.h - h };
  if (location.groupId) {
    const group = document.items.find((candidate): candidate is DashboardGroup => candidate.kind === 'group' && candidate.id === location.groupId)!;
    const remaining = closeVacatedSpace(group.children.filter((child) => child.id !== id), vacancy);
    const children = insertAt(remaining, edited as DashboardWidget);
    return { ...document, items: replaceGroup(document.items, group, withChildren(group, children)) };
  }
  const remaining = closeVacatedSpace(document.items.filter((candidate) => candidate.id !== id), vacancy);
  return { ...document, items: insertAt(remaining, edited as DashboardItem) };
}

export function updateLayoutItem(document: DashboardLayoutDocument, id: string, patch: { title?: string; accent?: 'slate' | 'cyan' | 'lime' }): DashboardLayoutDocument {
  if (!findLocation(document, id)) return document;
  return { ...document, items: document.items.map((item) => {
    if (item.id === id) return item.kind === 'group' ? { ...item, title: patch.title ?? item.title } : { ...item, title: patch.title ?? item.title, settings: { ...item.settings, ...(patch.accent ? { accent: patch.accent } : {}) } };
    if (item.kind === 'group') return { ...item, children: item.children.map((child) => child.id === id ? { ...child, title: patch.title ?? child.title, settings: { ...child.settings, ...(patch.accent ? { accent: patch.accent } : {}) } } : child) };
    return item;
  }) };
}

/** Removing a group releases its children to the canvas, so business cards are never lost. */
export function removeLayoutItem(document: DashboardLayoutDocument, id: string): DashboardLayoutDocument {
  const location = findLocation(document, id);
  if (!location) return document;
  if (location.groupId) {
    const group = document.items.find((item): item is DashboardGroup => item.kind === 'group' && item.id === location.groupId)!;
    const children = closeVacatedSpace(group.children.filter((child) => child.id !== id), location.item);
    return { ...document, items: replaceGroup(document.items, group, withChildren(group, children)) };
  }
  if (location.item.kind === 'widget') return { ...document, items: closeVacatedSpace(document.items.filter((item) => item.id !== id), location.item) };
  let next: DashboardLayoutDocument = { ...document, items: closeVacatedSpace(document.items.filter((item) => item.id !== id), location.item) };
  for (const child of location.item.children) next = placeWidgetInContainer(next, child, null, { x: child.x, y: location.item.y + 1 + child.y });
  return next;
}

export function legacyDocument(widgets: Widget[], replacements: Widget[]): DashboardLayoutDocument {
  return { version: 2, items: expandLegacyWorkspace(widgets, replacements).map((widget) => ({ kind: 'widget', ...widget })) };
}
