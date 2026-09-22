import type { KPIDisplayFormat } from '@/types/report';

/** Render a KPI value for display.
 *
 * Values arrive as Decimal strings, so they are formatted rather than parsed
 * into something lossy: a value that is not finite is shown verbatim instead
 * of becoming "NaN".
 *
 * `percent` scales by 100, the way a spreadsheet's percent format does: the
 * formula is written as the ratio (`clicks / impressions`) and the format
 * turns 0.025 into "2.5%". Appending "%" to the raw ratio instead would
 * render that same KPI as "0.03%".
 */
export function formatKPIValue(
  value: string | null,
  format: KPIDisplayFormat
): string {
  if (value === null || value === '') return '—';

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;

  const scaled = format === 'percent' ? numeric * 100 : numeric;
  const formatted = scaled.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });

  if (format === 'currency') return `$${formatted}`;
  if (format === 'percent') return `${formatted}%`;
  return formatted;
}
