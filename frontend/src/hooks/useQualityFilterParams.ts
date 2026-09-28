import { useCallback, useMemo } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

import {
  QUALITY_ARRAY_KEYS,
  QUALITY_FILTER_KEYS,
  readQualityFilters,
  writeQualityFilters,
} from '@/lib/csmQualityParams';
import { QualityFilters } from '@/types/csmQuality';

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

/** Strip every filter, and the page offset they were paging through. */
function dropFilters(params: URLSearchParams): void {
  QUALITY_FILTER_KEYS.forEach((key) => params.delete(key));
  params.delete('page');
}

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

  const filters: QualityFilters = useMemo(() => readQualityFilters(searchParams), [searchParams]);

  const tab: QualityTab = searchParams.get('tab') === 'report' ? 'report' : 'conversations';
  const page = Math.max(1, Number(searchParams.get('page')) || 1);

  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (filters.date_from) count += 1;
    if (filters.date_to) count += 1;
    if (filters.customer_search) count += 1;
    if (filters.unassigned) count += 1;
    QUALITY_ARRAY_KEYS.forEach((key) => {
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
        writeQualityFilters(params, next);
        // A changed filter invalidates the current page offset.
        params.delete('page');
      });
    },
    [push],
  );

  const setTab = useCallback(
    (nextTab: QualityTab) =>
      push((params) => {
        // Switching view starts from a clean slate rather than carrying the
        // previous tab's filters across.
        dropFilters(params);
        params.set('tab', nextTab);
      }),
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

  const clearFilters = useCallback(() => push(dropFilters), [push]);

  return { filters, tab, page, setFilters, setTab, setPage, clearFilters, activeFilterCount };
}

export default useQualityFilterParams;
