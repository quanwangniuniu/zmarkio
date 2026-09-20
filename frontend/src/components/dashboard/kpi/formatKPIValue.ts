import type { KPIDisplayFormat } from '@/types/report';

/** Render a KPI value for display.
 *
 * Values arrive as Decimal strings, so they are formatted rather than parsed
 * into something lossy: a value that is not finite is shown verbatim instead
 * of becoming "NaN".
 */
export function formatKPIValue(
  value: string | null,
  format: KPIDisplayFormat
): string {
  if (value === null || value === '') return '—';

  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;

  const formatted = numeric.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });

  if (format === 'currency') return `$${formatted}`;
  if (format === 'percent') return `${formatted}%`;
  return formatted;
}
