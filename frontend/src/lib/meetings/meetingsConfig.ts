import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget } from '@/lib/orgCustomization/optionConfig';
import type { AdvancedFilterField, AdvancedFilterVisibility } from '@/components/meetings/AdvancedFilterDialog';
import { MEETING_TYPE_FILTER_OPTIONS } from '@/lib/meetings/meetingTypeSlugs';

/**
 * D12 (MED-485) per-project Meetings surfaces. Keys map to `meetings.<field>.<value>`;
 * `filter_type` is `meetings.filter.type.*`. The dashboard `UpcomingMeetingsPanel`
 * belongs to D1 (`overview.panel.upcoming_meetings`) and is intentionally not here.
 */
export const MEETINGS_FILTER = createOptionTarget({
  date_from: 'Date from',
  date_to: 'Date to',
  has_generated_decisions: 'Has generated decisions',
  has_generated_tasks: 'Has generated tasks',
  is_archived: 'Archived',
});

export const MEETINGS_FILTER_TYPE = createOptionTarget(
  Object.fromEntries(MEETING_TYPE_FILTER_OPTIONS.map((option) => [option.slug, option.label])),
);

export const MEETINGS_SECTION = createOptionTarget({ action_items: 'Action items' });

export const MEETINGS_PANEL = createOptionTarget({ knowledge_nav: 'Contextual knowledge' });

const meetingsConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('meetings', projectId),
  (config) => applyOptionConfig(config.options, {
    filter: MEETINGS_FILTER,
    filter_type: MEETINGS_FILTER_TYPE,
    section: MEETINGS_SECTION,
    panel: MEETINGS_PANEL,
  }),
);

export const loadMeetingsConfig = meetingsConfigLoader.load;
export const storeMeetingsConfig = meetingsConfigLoader.store;

export const advancedFilterVisibility = (): AdvancedFilterVisibility =>
  Object.fromEntries(
    Object.keys(MEETINGS_FILTER.defaults).map((field) => [field, MEETINGS_FILTER.values.includes(field)]),
  ) as Record<AdvancedFilterField, boolean>;

/**
 * Applies label/order/visibility to known meeting types. Custom project types that the
 * config does not know about stay visible after the configured ones.
 */
export const configureMeetingTypeOptions = <T extends { slug: string; label: string }>(options: T[]): T[] => {
  const known = options.filter((option) => option.slug in MEETINGS_FILTER_TYPE.defaults);
  const custom = options.filter((option) => !(option.slug in MEETINGS_FILTER_TYPE.defaults));
  const configured = MEETINGS_FILTER_TYPE.values.flatMap((slug) =>
    known.filter((option) => option.slug === slug).map((option) => ({ ...option, label: MEETINGS_FILTER_TYPE.labels[slug] })),
  );
  return [...configured, ...custom];
};

export type MeetingSurface = 'section.action_items' | 'panel.knowledge_nav';

export const isMeetingSurfaceEnabled = (surface: MeetingSurface): boolean => {
  const [field, value] = surface.split('.');
  return (field === 'section' ? MEETINGS_SECTION : MEETINGS_PANEL).values.includes(value);
};
