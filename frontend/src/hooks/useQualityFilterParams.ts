import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import {
  EMPTY_QUALITY_FILTERS,
  QualityBucket,
  QualityDateBasis,
  QualityFilters,
} from '@/types/csmQuality';

export type QualityTab = 'conversations' | 'report';

export interface QualityFilterState {
  filters: QualityFilters;
  tab: QualityTab;
  page: number;
  setFilters: (next: Partial<QualityFilters>) => void;
  setTab: (tab: QualityTab) => void;
  setPage: (page: number) => void;
  clearFilters: () => void;
  activeFilterCount: number;
}

const ARRAY_KEYS = ['agent', 'queue', 'channel', 'customer', 'tag', 'status'] as const;

/**
 * Keep quality inspection filters, the active tab and the page in the URL.
 *
 * Filters live in the URL rather than in component state so that switching
 * tabs, refreshing, or sharing a link all preserve what the supervisor is
 * looking at — and so the report always describes the same population as the
 * list beside it.
 */
export function useQualityFilterParams(): QualityFilterState {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const filters: QualityFilters = useMemo(() => {
    const numbers = (key: string): number[] =>
      searchParams.getAll(key).map(Number).filter(Number.isFinite);
    const strings = (key: string): string[] => searchParams.getAll(key).filter(Boolean);

    // 'unassigned' is a sentinel alongside numeric agent ids.
    const agent: (number | 'unassigned')[] = searchParams.getAll('agent').flatMap((raw) => {
      if (raw === 'unassigned') return ['unassigned' as const];
      const parsed = Number(raw);
      return Number.isFinite(parsed) ? [parsed] : [];
    });

    return {
      date_from: searchParams.get('date_from') || undefined,
      date_to: searchParams.get('date_to') || undefined,
      agent,
      queue: numbers('queue'),
      channel: strings('channel'),
      customer: numbers('customer'),
      customer_search: searchParams.get('customer_search') || undefined,
      tag: strings('tag'),
      status: strings('status'),
      date_basis: (searchParams.get('date_basis') as QualityDateBasis) || undefined,
      bucket: (searchParams.get('bucket') as QualityBucket) || undefined,
    };
  }, [searchParams]);

  const tab: QualityTab = searchParams.get('tab') === 'report' ? 'report' : 'conversations';
  const page = Math.max(1, Number(searchParams.get('page')) || 1);

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (filters.date_from) count += 1;
    if (filters.date_to) count += 1;
    if (filters.customer_search) count += 1;
    ARRAY_KEYS.forEach((key) => {
      const value = filters[key];
      if (Array.isArray(value) && value.length > 0) count += value.length;
    });
    return count;
  }, [filters]);

  const push = useCallback(
    (build: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      build(params);
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const setFilters = useCallback(
    (next: Partial<QualityFilters>) => {
      push((params) => {
        Object.entries(next).forEach(([key, value]) => {
          params.delete(key);
          if (Array.isArray(value)) {
            value.forEach((entry) => params.append(key, String(entry)));
          } else if (value !== undefined && value !== null && value !== '') {
            params.set(key, String(value));
          }
        });
        // A changed filter invalidates the current page offset.
        params.delete('page');
      });
    },
    [push],
  );

  const setTab = useCallback(
    (nextTab: QualityTab) => push((params) => params.set('tab', nextTab)),
    [push],
  );

  const setPage = useCallback(
    (nextPage: number) =>
      push((params) => {
        if (nextPage <= 1) params.delete('page');
        else params.set('page', String(nextPage));
      }),
    [push],
  );

  const clearFilters = useCallback(() => {
    push((params) => {
      Object.keys(EMPTY_QUALITY_FILTERS).forEach((key) => params.delete(key));
      ['date_from', 'date_to', 'customer_search', 'date_basis', 'bucket', 'page'].forEach((key) =>
        params.delete(key),
      );
    });
  }, [push]);

  return { filters, tab, page, setFilters, setTab, setPage, clearFilters, activeFilterCount };
}

export default useQualityFilterParams;
