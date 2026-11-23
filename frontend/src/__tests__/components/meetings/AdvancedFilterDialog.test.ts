import {
  EMPTY_ADVANCED_FILTER,
  advancedFilterToParams,
  countActiveAdvancedFilters,
  maskAdvancedFilter,
} from '@/components/meetings/AdvancedFilterDialog';

describe('AdvancedFilterDialog filter helpers', () => {
  test('empty filter produces no query params', () => {
    expect(advancedFilterToParams(EMPTY_ADVANCED_FILTER)).toEqual({});
  });

  test('converts date range to query params', () => {
    expect(
      advancedFilterToParams({
        ...EMPTY_ADVANCED_FILTER,
        date_from: '2026-11-01',
        date_to: '2026-11-30',
      })
    ).toEqual({
      date_from: '2026-11-01',
      date_to: '2026-11-30',
    });
  });

  test('preserves false values instead of treating them as empty', () => {
    expect(
      advancedFilterToParams({
        ...EMPTY_ADVANCED_FILTER,
        is_archived: false,
        has_generated_decisions: false,
        has_generated_tasks: true,
      })
    ).toEqual({
      is_archived: false,
      has_generated_decisions: false,
      has_generated_tasks: true,
    });
  });

  test('counts only active filters', () => {
    expect(countActiveAdvancedFilters(EMPTY_ADVANCED_FILTER)).toBe(0);

    expect(
      countActiveAdvancedFilters({
        ...EMPTY_ADVANCED_FILTER,
        date_from: '2026-11-01',
        is_archived: false,
        has_generated_tasks: true,
      })
    ).toBe(3);
  });

  test('counts each date boundary as a separate filter', () => {
    expect(
      countActiveAdvancedFilters({
        ...EMPTY_ADVANCED_FILTER,
        date_from: '2026-11-01',
        date_to: '2026-11-30',
      })
    ).toBe(2);
  });
});

describe('maskAdvancedFilter', () => {
  const full = {
    date_from: '2026-11-01',
    date_to: '2026-11-30',
    has_generated_decisions: true,
    has_generated_tasks: false,
    is_archived: false,
  };

  test('keeps everything when no visibility is given', () => {
    expect(maskAdvancedFilter(full)).toEqual(full);
  });

  test('clears hidden fields so they are not sent or counted', () => {
    const masked = maskAdvancedFilter(full, { date_to: false, is_archived: false });
    expect(masked).toEqual({ ...full, date_to: '', is_archived: null });
    expect(advancedFilterToParams(masked)).not.toHaveProperty('is_archived');
    expect(countActiveAdvancedFilters(masked)).toBe(3);
  });
});
