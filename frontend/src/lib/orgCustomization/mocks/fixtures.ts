import { MODULE_KEYS, type ModuleKey, type SurfaceKey } from '../keys';
import type {
  EffectiveConfig,
  ModuleConfig,
  Registry,
  SurfaceConfig,
  SurfaceSpec,
} from '@/types/orgCustomization';

const MODULE_LABELS: Record<ModuleKey, string> = {
  overview: 'Overview',
  tasks: 'Tasks',
  campaigns: 'Campaigns',
  meta_ads: 'Meta Ads',
  decisions: 'Decisions',
  budget_pools: 'Budget Pools',
  spreadsheets: 'Spreadsheets',
  variations_studio: 'Variations Studio',
  ads_draft: 'Ads Draft',
  email_draft: 'Email Draft',
  notion: 'Notion',
  meetings: 'Meetings',
  calendar: 'Calendar',
  messages: 'Messages',
  miro: 'Miro',
  workflows: 'Workflows',
  timeline: 'Timeline',
  csm: 'Customer Service',
  admin: 'Administration',
  integrations: 'Integrations',
  agent: 'AI Agent',
  notifications: 'Notifications',
  shell: 'App Shell',
};

const surface = (
  key: SurfaceKey,
  label: string,
  defaultOrder: number,
  parent: SurfaceKey | null = null,
): SurfaceSpec => ({
  key,
  label,
  default_visible: true,
  default_order: defaultOrder,
  route_prefixes: [],
  parent,
});

export const registryFixture: Registry = {
  modules: MODULE_KEYS.map((key, index) => ({
    key,
    label: MODULE_LABELS[key],
    default_visible: true,
    default_order: index,
    route_prefixes: [],
  })),
  surface_types: [
    { key: 'group', label: 'Group' },
    { key: 'item', label: 'Item' },
    { key: 'tab', label: 'Tab' },
    { key: 'card', label: 'Card' },
    { key: 'panel', label: 'Panel' },
    { key: 'provider', label: 'Provider' },
    { key: 'filter', label: 'Filter' },
    { key: 'section', label: 'Section' },
  ],
  surfaces: [
    surface('shell.group.manage', 'Manage', 0),
    surface('shell.item.ads_draft', 'Ads Draft', 1),
    surface('shell.item.facebook_meta', 'Facebook Meta', 2, 'shell.item.ads_draft'),
    surface('tasks.tab.gantt', 'Gantt', 0),
    surface('overview.card.meetings', 'Meetings', 0),
    surface('integrations.provider.slack', 'Slack', 0),
  ],
};

const buildEffectiveConfig = (
  version: number,
  moduleOverrides: Partial<Record<ModuleKey, Partial<ModuleConfig>>> = {},
  surfaceOverrides: Partial<Record<SurfaceKey, Partial<SurfaceConfig>>> = {},
): EffectiveConfig => ({
  version,
  modules: Object.fromEntries(
    registryFixture.modules.map((spec) => [
      spec.key,
      { enabled: spec.default_visible, label: null, order: spec.default_order, ...moduleOverrides[spec.key] },
    ]),
  ) as Record<ModuleKey, ModuleConfig>,
  surfaces: Object.fromEntries(
    registryFixture.surfaces.map((spec) => [
      spec.key,
      { visible: spec.default_visible, label: null, order: spec.default_order, ...surfaceOverrides[spec.key] },
    ]),
  ) as Record<SurfaceKey, SurfaceConfig>,
});

export const allEnabledConfig = buildEffectiveConfig(1);

export const partiallyDisabledConfig = buildEffectiveConfig(
  2,
  { meta_ads: { enabled: false }, miro: { enabled: false }, notion: { enabled: false } },
  { 'tasks.tab.gantt': { visible: false }, 'shell.item.facebook_meta': { visible: false } },
);

export const projectOverrideConfig = buildEffectiveConfig(
  3,
  { miro: { enabled: false }, campaigns: { label: 'Client Campaigns', order: 0 }, overview: { order: 2 } },
  { 'overview.card.meetings': { visible: false } },
);

export const EFFECTIVE_FIXTURES = {
  all_enabled: allEnabledConfig,
  partially_disabled: partiallyDisabledConfig,
  project_override: projectOverrideConfig,
} satisfies Record<string, EffectiveConfig>;

export type EffectiveFixtureName = keyof typeof EFFECTIVE_FIXTURES;
