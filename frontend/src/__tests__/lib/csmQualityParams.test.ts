import { readQualityFilters, toQueryParams, writeQualityFilters } from '@/lib/csmQualityParams';

describe('toQueryParams', () => {
  it('keeps arrays as arrays so axios serialises repeated keys', () => {
    const params = toQueryParams({ agent: [1, 2], tag: ['vip'] });

    expect(params.agent).toEqual([1, 2]);
    expect(params.tag).toEqual(['vip']);
  });

  it('drops empty arrays, blank strings, false and nullish values', () => {
    const params = toQueryParams({
      agent: [],
      tag: [],
      customer_search: '',
      unassigned: false,
      date_from: undefined,
      date_to: '2026-03-31',
    });

    expect(params).toEqual({ date_to: '2026-03-31' });
  });

  it('merges extra params but still drops blanks', () => {
    expect(toQueryParams({ queue: [7] }, { page: 2, page_size: undefined })).toEqual({
      queue: [7],
      page: 2,
    });
  });
});

describe('writeQualityFilters and readQualityFilters', () => {
  it('round-trips every filter through the URL', () => {
    const filters = {
      date_from: '2026-03-01',
      date_to: '2026-03-31',
      agent: [9, 12],
      unassigned: true,
      queue: [3],
      channel: ['email', 'web'],
      customer: [5],
      customer_search: 'ada',
      tag: ['vip'],
      status: ['closed'],
      date_basis: 'conversation' as const,
      bucket: 'week' as const,
    };
    const params = new URLSearchParams();

    writeQualityFilters(params, filters);

    expect(params.getAll('agent')).toEqual(['9', '12']);
    expect(params.get('unassigned')).toBe('true');
    expect(readQualityFilters(params)).toEqual(filters);
  });

  it('replaces only the keys it is given and removes cleared ones', () => {
    const params = new URLSearchParams('agent=9&channel=email&tab=report');

    writeQualityFilters(params, { channel: [], tag: ['vip'] });

    expect(params.toString()).toBe('agent=9&tab=report&tag=vip');
  });

  it('ignores a non-numeric agent id in the URL', () => {
    const filters = readQualityFilters(new URLSearchParams('agent=unassigned&agent=4'));

    expect(filters.agent).toEqual([4]);
    expect(filters.unassigned).toBeUndefined();
  });
});
