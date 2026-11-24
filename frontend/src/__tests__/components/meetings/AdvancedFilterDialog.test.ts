import {
  EMPTY_ADVANCED_FILTER,
  advancedFilterToParams,
  countActiveAdvancedFilters,
  maskAdvancedFilter,
} from '@/components/meetings/AdvancedFilterDialog';
import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { loadMeetingsConfig, storeMeetingsConfig } from '@/lib/meetings/meetingsConfig';
import type { OptionConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const hidden = (value: string): OptionConfig => ({
  field: 'filter', value, default_label: value, label: null, color: null, enabled: false, order: 0, is_default: false, editable: [],
});

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

  test('keeps everything when nothing is configured', () => {
    expect(maskAdvancedFilter(full)).toEqual(full);
  });

  test('clears filters hidden in the project config so they are not sent or counted', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce({
      options: [hidden('date_to'), hidden('is_archived')],
      can_edit: true,
    });
    await loadMeetingsConfig(5);

    const masked = maskAdvancedFilter(full);
    expect(masked).toEqual({ ...full, date_to: '', is_archived: null });
    expect(advancedFilterToParams(masked)).not.toHaveProperty('is_archived');
    expect(countActiveAdvancedFilters(masked)).toBe(3);

    storeMeetingsConfig(5, { options: [], can_edit: true });
  });
});
