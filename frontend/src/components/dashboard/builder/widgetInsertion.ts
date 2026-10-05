export type WidgetInsertion = { targetId: string; side: 'before' | 'after' };

type LayerRow = { id: string | null; groupId?: string; top: number; bottom: number };

/** Pick the widget edge represented by a pointer over a row or its neighboring gap. */
export function widgetInsertionAtY(rows: LayerRow[], pointerY: number, sourceId: string): WidgetInsertion | null {
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    if (pointerY >= row.top && pointerY <= row.bottom) {
      if (!row.id || row.id === sourceId) return null;
      return { targetId: row.id, side: pointerY >= (row.top + row.bottom) / 2 ? 'after' : 'before' };
    }

    const next = rows[index + 1];
    if (!next || next.top - row.bottom > 24 || pointerY <= row.bottom || pointerY >= next.top) continue;
    if (next.id && next.id !== sourceId) return { targetId: next.id, side: 'before' };
    if (row.id && row.id !== sourceId) return { targetId: row.id, side: 'after' };
    if (next.groupId) return { targetId: next.groupId, side: 'before' };
    if (row.groupId) return { targetId: row.groupId, side: 'after' };
  }
  return null;
}
