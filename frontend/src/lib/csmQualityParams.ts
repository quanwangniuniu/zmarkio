import { QualityBucket, QualityDateBasis, QualityFilters } from '@/types/csmQuality';

/**
 * The one mapping between quality filters and query params.
 *
 * The URL (useQualityFilterParams) and the API requests (csmQualityApi) both
 * go through here, so the two can never disagree about how a filter is
 * written. Arrays become repeated keys (`?agent=1&agent=2`), which is what the
 * backend reads with `getlist`; blank values are dropped.
 */

export const QUALITY_ARRAY_KEYS = ['agent', 'queue', 'channel', 'customer', 'tag', 'status'] as const;

export const QUALITY_SCALAR_KEYS = [
  'date_from',
  'date_to',
  'customer_search',
  'unassigned',
  'date_basis',
  'bucket',
] as const;

export const QUALITY_FILTER_KEYS = [...QUALITY_ARRAY_KEYS, ...QUALITY_SCALAR_KEYS];

export type QualityParamValue = string | number | boolean | (string | number)[];

function isBlank(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === '' ||
    value === false ||
    (Array.isArray(value) && value.length === 0)
  );
}

/**
 * Filters (plus any *extra* params such as page) with the blanks dropped.
 *
 * Arrays stay arrays: the shared axios instance is configured with
 * `paramsSerializer: { indexes: null }`, so it writes them as repeated keys.
 */
export function toQueryParams(
  filters: Partial<QualityFilters>,
  extra: Record<string, unknown> = {},
): Record<string, QualityParamValue> {
  const params: Record<string, QualityParamValue> = {};
  Object.entries({ ...filters, ...extra }).forEach(([key, value]) => {
    if (!isBlank(value)) params[key] = value as QualityParamValue;
  });
  return params;
}

/** Replace the keys present in *next* on *params*, dropping cleared ones. */
export function writeQualityFilters(params: URLSearchParams, next: Partial<QualityFilters>): void {
  Object.keys(next).forEach((key) => params.delete(key));
  Object.entries(toQueryParams(next)).forEach(([key, value]) => {
    (Array.isArray(value) ? value : [value]).forEach((entry) => params.append(key, String(entry)));
  });
}

/** Parse the filters out of a URL query. */
export function readQualityFilters(params: Pick<URLSearchParams, 'get' | 'getAll'>): QualityFilters {
  const numbers = (key: string): number[] =>
    params.getAll(key).map(Number).filter(Number.isFinite);
  const strings = (key: string): string[] => params.getAll(key).filter(Boolean);

  return {
    date_from: params.get('date_from') || undefined,
    date_to: params.get('date_to') || undefined,
    agent: numbers('agent'),
    unassigned: params.get('unassigned') === 'true' || undefined,
    queue: numbers('queue'),
    channel: strings('channel'),
    customer: numbers('customer'),
    customer_search: params.get('customer_search') || undefined,
    tag: strings('tag'),
    status: strings('status'),
    date_basis: (params.get('date_basis') as QualityDateBasis) || undefined,
    bucket: (params.get('bucket') as QualityBucket) || undefined,
  };
}
