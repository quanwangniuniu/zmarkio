import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import {
  calendarViewTabs,
  isCalendarSurfaceEnabled,
  loadCalendarConfig,
  resolveCalendarView,
  storeCalendarConfig,
} from '@/lib/calendar/calendarConfig';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, editable: [], ...overrides,
});

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

describe('calendar config', () => {
  it('shows every surface when nothing is configured', () => {
    expect(calendarViewTabs()).toEqual([
      { view: 'day', label: 'Day' },
      { view: 'week', label: 'Week' },
      { view: 'month', label: 'Month' },
      { view: 'year', label: 'Year' },
      { view: 'agenda', label: 'Agenda' },
    ]);
    expect(isCalendarSurfaceEnabled('section.scope_switch')).toBe(true);
    expect(isCalendarSurfaceEnabled('section.toolbar_today')).toBe(true);
    expect(isCalendarSurfaceEnabled('panel.sidebar')).toBe(true);
    expect(isCalendarSurfaceEnabled('nav_item.booking_links')).toBe(true);
    expect(resolveCalendarView('year')).toBe('year');
  });

  it('loads the calendar module and hides disabled surfaces', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(moduleConfig([
      option('section', 'scope_switch', { enabled: false }),
      option('section', 'toolbar_ask_agent', { enabled: false }),
      option('panel', 'sidebar', { enabled: false }),
      option('nav_item', 'booking_links', { enabled: false }),
    ]));

    await loadCalendarConfig(3);

    expect(fetchOptionConfig).toHaveBeenCalledWith('calendar', 3);
    expect(isCalendarSurfaceEnabled('section.scope_switch')).toBe(false);
    expect(isCalendarSurfaceEnabled('section.toolbar_ask_agent')).toBe(false);
    expect(isCalendarSurfaceEnabled('section.toolbar_today')).toBe(true);
    expect(isCalendarSurfaceEnabled('panel.sidebar')).toBe(false);
    expect(isCalendarSurfaceEnabled('nav_item.booking_links')).toBe(false);

    storeCalendarConfig(3, moduleConfig([]));
    expect(isCalendarSurfaceEnabled('panel.sidebar')).toBe(true);
  });

  it('renames, reorders and hides view tabs, and falls back from a hidden view', () => {
    storeCalendarConfig(3, moduleConfig([
      option('tab', 'agenda', { label: 'List', order: 0 }),
      option('tab', 'week', { order: 1 }),
      option('tab', 'day', { enabled: false, order: 2 }),
      option('tab', 'month', { order: 3 }),
      option('tab', 'year', { enabled: false, order: 4 }),
    ]));

    expect(calendarViewTabs()).toEqual([
      { view: 'agenda', label: 'List' },
      { view: 'week', label: 'Week' },
      { view: 'month', label: 'Month' },
    ]);
    expect(resolveCalendarView('year')).toBe('week');
    expect(resolveCalendarView('agenda')).toBe('agenda');

    storeCalendarConfig(3, moduleConfig([]));
  });
});
