export type OverlapItem = {
  id: string;
  startMs: number;
  endMs: number;
};

export type OverlapPlacement = {
  colIndex: number;
  colCount: number;
};

/**
 * Side-by-side columns for timed events that overlap.
 *
 * Absolute cards that all use left/right: 0 stack on top of each other, so a
 * second meeting in the same hour is invisible. Columns keep every event
 * clickable. Intervals are half-open: an event ending at 10:00 does not
 * collide with one starting at 10:00.
 */
export function layoutOverlappingEvents(
  items: OverlapItem[],
): Map<string, OverlapPlacement> {
  const result = new Map<string, OverlapPlacement>();
  if (items.length === 0) {
    return result;
  }

  const sorted = [...items].sort((a, b) => {
    if (a.startMs !== b.startMs) return a.startMs - b.startMs;
    if (a.endMs !== b.endMs) return b.endMs - a.endMs;
    return a.id.localeCompare(b.id);
  });

  type Placed = { item: OverlapItem; colIndex: number };
  let cluster: Placed[] = [];
  let clusterEnd = Number.NEGATIVE_INFINITY;
  let columnEnds: number[] = [];

  const flushCluster = () => {
    const colCount = Math.max(1, columnEnds.length);
    for (const placed of cluster) {
      result.set(placed.item.id, { colIndex: placed.colIndex, colCount });
    }
    cluster = [];
    columnEnds = [];
    clusterEnd = Number.NEGATIVE_INFINITY;
  };

  for (const item of sorted) {
    if (cluster.length > 0 && item.startMs >= clusterEnd) {
      flushCluster();
    }

    let colIndex = columnEnds.findIndex((end) => end <= item.startMs);
    if (colIndex === -1) {
      colIndex = columnEnds.length;
      columnEnds.push(item.endMs);
    } else {
      columnEnds[colIndex] = item.endMs;
    }

    cluster.push({ item, colIndex });
    clusterEnd = Math.max(clusterEnd, item.endMs);
  }
  flushCluster();
  return result;
}

export function overlapColumnStyle(
  placement: OverlapPlacement | undefined,
): { left: string; width: string } {
  const colIndex = placement?.colIndex ?? 0;
  const colCount = Math.max(1, placement?.colCount ?? 1);
  const widthPct = 100 / colCount;
  const leftPct = colIndex * widthPct;
  return {
    left: `calc(${leftPct}% + 3px)`,
    width: `calc(${widthPct}% - 6px)`,
  };
}
