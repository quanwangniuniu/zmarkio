export type GroupInsertion = { targetId: string; side: 'before' | 'after' };

type GroupRow = { id: string; top: number; bottom: number };

/** Resolve the narrow space between adjacent group rows to a visible insertion line. */
export function groupInsertionAtY(rows: GroupRow[], pointerY: number, sourceId: string): GroupInsertion | null {
  for (let index = 0; index < rows.length; index += 1) {
    const row = rows[index];
    if (pointerY >= row.top && pointerY <= row.bottom) {
      if (row.id === sourceId) return null;
      return { targetId: row.id, side: pointerY >= (row.top + row.bottom) / 2 ? 'after' : 'before' };
    }

    const next = rows[index + 1];
    if (next && next.top - row.bottom <= 24 && pointerY > row.bottom && pointerY < next.top) {
      return next.id === sourceId
        ? { targetId: row.id, side: 'after' }
        : { targetId: next.id, side: 'before' };
    }
  }
  return null;
}
