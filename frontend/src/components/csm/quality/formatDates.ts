/**
 * Date formatting for the quality inspection screens.
 *
 * Follows the repo's prevailing shape — `undefined` locale so the viewer's
 * own formatting wins, with `{ year: 'numeric', month: 'short', day: 'numeric' }`
 * — as used by the admin audit log (`formatFullDateTime`) and most places that
 * pass explicit options. A bare `toLocaleDateString()` renders 9/17/2026, which
 * is ambiguous outside the US.
 */

const DATE_TIME: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
};

const DATE_ONLY: Intl.DateTimeFormatOptions = {
  year: 'numeric',
  month: 'short',
  day: 'numeric',
};

/** "Sep 17, 2026, 09:59" — for timestamps where the time carries meaning. */
export function formatDateTime(iso: string | null | undefined, fallback = '—'): string {
  if (!iso) return fallback;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? fallback : parsed.toLocaleString(undefined, DATE_TIME);
}

/** "Sep 17, 2026" — for timestamps where only the day matters. */
export function formatDate(iso: string | null | undefined, fallback = '—'): string {
  if (!iso) return fallback;
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime())
    ? fallback
    : parsed.toLocaleDateString(undefined, DATE_ONLY);
}

/**
 * "Sep 17, 2026" from a bare `YYYY-MM-DD` report bucket.
 *
 * Built from the parts rather than `new Date('2026-09-17')`, which JavaScript
 * parses as UTC midnight — that renders as the 16th for anyone west of UTC.
 */
export function formatBucket(bucket: string | null | undefined, fallback = '—'): string {
  if (!bucket) return fallback;
  const [year, month, day] = bucket.split('-').map(Number);
  if (!year || !month || !day) return bucket;
  return new Date(year, month - 1, day).toLocaleDateString(undefined, DATE_ONLY);
}
