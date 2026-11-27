import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import type { CalendarViewType } from '@/lib/api/calendarApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget, preferredOption } from '@/lib/orgCustomization/optionConfig';
import { VIEW_LABELS } from '@/components/calendar/utils';

/**
 * D13 (MED-486) per-project Calendar surfaces. Keys map to `calendar.<field>.<value>`;
 * `nav_item` is `nav.item.calendar.*`.
 *
 * Only in-app pages read this config. The public `/book/[orgSlug]/[linkSlug]` page
 * must never import it, so booking links already sent out keep working when
 * Calendar surfaces are disabled.
 */
export const CALENDAR_TAB = createOptionTarget({
  day: VIEW_LABELS.day,
  week: VIEW_LABELS.week,
  month: VIEW_LABELS.month,
  year: VIEW_LABELS.year,
  agenda: VIEW_LABELS.agenda,
});

export const CALENDAR_SECTION = createOptionTarget({
  scope_switch: 'Booking link scope switch',
  toolbar_today: 'Today button',
  toolbar_navigation: 'Previous / next buttons',
  toolbar_all_events: 'All events toggle',
  toolbar_ask_agent: 'Ask Agent button',
});

export const CALENDAR_PANEL = createOptionTarget({ sidebar: 'Calendar sidebar' });

export const CALENDAR_NAV_ITEM = createOptionTarget({ booking_links: 'Booking links' });

const calendarConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('calendar', projectId),
  (config) => applyOptionConfig(config.options, {
    tab: CALENDAR_TAB,
    section: CALENDAR_SECTION,
    panel: CALENDAR_PANEL,
    nav_item: CALENDAR_NAV_ITEM,
  }),
);

export const loadCalendarConfig = calendarConfigLoader.load;
export const storeCalendarConfig = calendarConfigLoader.store;

/** Enabled view tabs in configured order, with configured labels. */
export const calendarViewTabs = (): { view: CalendarViewType; label: string }[] =>
  CALENDAR_TAB.values.map((view) => ({ view: view as CalendarViewType, label: CALENDAR_TAB.labels[view] }));

/** Keeps the current view if it is enabled, otherwise falls back to the default (week) or first enabled view. */
export const resolveCalendarView = (current: CalendarViewType): CalendarViewType =>
  CALENDAR_TAB.values.includes(current) ? current : (preferredOption(CALENDAR_TAB, 'week') as CalendarViewType);

export type CalendarSurface =
  | 'section.scope_switch'
  | 'section.toolbar_today'
  | 'section.toolbar_navigation'
  | 'section.toolbar_all_events'
  | 'section.toolbar_ask_agent'
  | 'panel.sidebar'
  | 'nav_item.booking_links';

const SURFACE_TARGETS = { section: CALENDAR_SECTION, panel: CALENDAR_PANEL, nav_item: CALENDAR_NAV_ITEM };

export const isCalendarSurfaceEnabled = (surface: CalendarSurface): boolean => {
  const [field, value] = surface.split('.') as [keyof typeof SURFACE_TARGETS, string];
  return SURFACE_TARGETS[field].values.includes(value);
};
