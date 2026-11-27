import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import {
  advancedFilterVisibility,
  configureMeetingTypeOptions,
  isMeetingSurfaceEnabled,
  loadMeetingsConfig,
  storeMeetingsConfig,
} from '@/lib/meetings/meetingsConfig';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, editable: [], ...overrides,
});

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

const typeOptions = [
  { slug: 'client-meeting', label: 'Client Meeting' },
  { slug: 'planning', label: 'Planning' },
  { slug: 'stand-up', label: 'Stand-up' },
  { slug: 'offsite', label: 'Offsite' },
];

describe('meetings config', () => {
  it('shows every surface when nothing is configured', () => {
    expect(advancedFilterVisibility()).toEqual({
      date_from: true,
      date_to: true,
      has_generated_decisions: true,
      has_generated_tasks: true,
      is_archived: true,
    });
    expect(configureMeetingTypeOptions(typeOptions).map((o) => o.slug)).toEqual([
      'planning', 'client-meeting', 'stand-up', 'offsite',
    ]);
    expect(isMeetingSurfaceEnabled('section.action_items')).toBe(true);
    expect(isMeetingSurfaceEnabled('panel.knowledge_nav')).toBe(true);
  });

  it('loads the meetings module and hides disabled surfaces', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(moduleConfig([
      option('filter', 'is_archived', { enabled: false, order: 4 }),
      option('section', 'action_items', { enabled: false }),
      option('panel', 'knowledge_nav', { enabled: false }),
    ]));

    await loadMeetingsConfig(3);

    expect(fetchOptionConfig).toHaveBeenCalledWith('meetings', 3);
    expect(advancedFilterVisibility().is_archived).toBe(false);
    expect(advancedFilterVisibility().date_from).toBe(true);
    expect(isMeetingSurfaceEnabled('section.action_items')).toBe(false);
    expect(isMeetingSurfaceEnabled('panel.knowledge_nav')).toBe(false);

    storeMeetingsConfig(3, moduleConfig([]));
    expect(isMeetingSurfaceEnabled('panel.knowledge_nav')).toBe(true);
  });

  it('renames, reorders and hides known meeting types but keeps custom ones', () => {
    storeMeetingsConfig(3, moduleConfig([
      option('filter_type', 'stand-up', { label: 'Daily', order: 0 }),
      option('filter_type', 'planning', { order: 1 }),
      option('filter_type', 'client-meeting', { enabled: false, order: 2 }),
    ]));

    expect(configureMeetingTypeOptions(typeOptions)).toEqual([
      { slug: 'stand-up', label: 'Daily' },
      { slug: 'planning', label: 'Planning' },
      { slug: 'offsite', label: 'Offsite' },
    ]);
  });
});
