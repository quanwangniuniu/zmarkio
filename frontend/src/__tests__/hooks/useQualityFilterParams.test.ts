import { renderHook, act } from '@testing-library/react';

import useQualityFilterParams from '@/hooks/useQualityFilterParams';

const replace = jest.fn();
let currentParams = new URLSearchParams();

jest.mock('next/navigation', () => ({
  useRouter: () => ({ replace: (...args: unknown[]) => replace(...args) }),
  usePathname: () => '/csm/quality',
  useSearchParams: () => currentParams,
}));

function setUrl(query: string) {
  currentParams = new URLSearchParams(query);
}

/** The query string the hook pushed on its last call. */
function lastQuery(): URLSearchParams {
  const url = replace.mock.calls[replace.mock.calls.length - 1][0] as string;
  return new URLSearchParams(url.split('?')[1] ?? '');
}

beforeEach(() => {
  replace.mockClear();
  setUrl('');
});

describe('useQualityFilterParams — reading the URL', () => {
  it('parses repeated keys into arrays', () => {
    setUrl('agent=9&agent=12&tag=vip&tag=refund&queue=3');
    const { result } = renderHook(() => useQualityFilterParams());

    expect(result.current.filters.agent).toEqual([9, 12]);
    expect(result.current.filters.tag).toEqual(['vip', 'refund']);
    expect(result.current.filters.queue).toEqual([3]);
  });

  it('keeps the unassigned sentinel alongside numeric agent ids', () => {
    setUrl('agent=unassigned&agent=9');
    const { result } = renderHook(() => useQualityFilterParams());

    expect(result.current.filters.agent).toEqual(['unassigned', 9]);
  });

  it('defaults to the conversations tab and page 1', () => {
    const { result } = renderHook(() => useQualityFilterParams());

    expect(result.current.tab).toBe('conversations');
    expect(result.current.page).toBe(1);
  });

  it('reads the report tab and a page number', () => {
    setUrl('tab=report&page=3');
    const { result } = renderHook(() => useQualityFilterParams());

    expect(result.current.tab).toBe('report');
    expect(result.current.page).toBe(3);
  });

  it('counts each applied filter value', () => {
    setUrl('date_from=2026-03-01&agent=9&agent=12&tag=vip');
    const { result } = renderHook(() => useQualityFilterParams());

    expect(result.current.activeFilterCount).toBe(4);
  });
});

describe('useQualityFilterParams — writing the URL', () => {
  it('writes an array as repeated keys', () => {
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.setFilters({ agent: [9, 'unassigned'] }));

    expect(lastQuery().getAll('agent')).toEqual(['9', 'unassigned']);
  });

  it('resets the page when a filter changes', () => {
    setUrl('page=4');
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.setFilters({ channel: ['email'] }));

    expect(lastQuery().get('page')).toBeNull();
  });

  it('drops a key when its value is cleared', () => {
    setUrl('channel=email');
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.setFilters({ channel: [] }));

    expect(lastQuery().getAll('channel')).toEqual([]);
  });

  it('keeps the tab when filters change, and vice versa', () => {
    setUrl('tab=report');
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.setFilters({ channel: ['email'] }));

    expect(lastQuery().get('tab')).toBe('report');
  });

  it('clears the filters when the tab changes', () => {
    setUrl('tab=conversations&channel=email&agent=9&date_from=2026-03-01&bucket=day&page=3');
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.setTab('report'));

    const query = lastQuery();
    expect(query.get('tab')).toBe('report');
    expect(query.getAll('channel')).toEqual([]);
    expect(query.getAll('agent')).toEqual([]);
    expect(query.get('date_from')).toBeNull();
    expect(query.get('bucket')).toBeNull();
    expect(query.get('page')).toBeNull();
  });

  it('omits page=1 rather than writing it', () => {
    setUrl('page=3');
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.setPage(1));

    expect(lastQuery().get('page')).toBeNull();
  });

  it('clears every filter but leaves the tab alone', () => {
    setUrl('tab=report&date_from=2026-03-01&agent=9&tag=vip&page=2');
    const { result } = renderHook(() => useQualityFilterParams());

    act(() => result.current.clearFilters());

    const query = lastQuery();
    expect(query.get('date_from')).toBeNull();
    expect(query.getAll('agent')).toEqual([]);
    expect(query.getAll('tag')).toEqual([]);
    expect(query.get('page')).toBeNull();
    expect(query.get('tab')).toBe('report');
  });
});
