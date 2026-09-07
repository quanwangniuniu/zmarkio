export function projectIdParam(raw: unknown): string | null {
  if (typeof raw === 'number' && Number.isFinite(raw)) return String(raw);
  if (typeof raw === 'string') return raw;
  return null;
}

export function parseSelectedIds(
  raw: unknown
): { ok: true; ids: number[] } | { ok: false; error: string } {
  if (!Array.isArray(raw) || raw.length === 0) {
    return { ok: false, error: 'selected_ids must be a non-empty list' };
  }
  const ids: number[] = [];
  for (const item of raw) {
    let value: number;
    if (typeof item === 'number') {
      value = item;
    } else if (typeof item === 'string' && /^-?\d+$/.test(item.trim())) {
      value = Number(item);
    } else {
      return { ok: false, error: 'selected_ids must contain integers' };
    }
    if (!Number.isInteger(value)) {
      return { ok: false, error: 'selected_ids must contain integers' };
    }
    ids.push(value);
  }
  return { ok: true, ids: [...new Set(ids)] };
}
