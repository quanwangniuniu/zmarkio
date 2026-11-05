import { MODULE_KEYS, ModuleKey, SURFACE_TYPE_KEYS, type SurfaceKey } from '../keys';
import type {
  EffectiveConfig,
  ModuleConfig,
  Registry,
  SurfaceConfig,
  SurfaceSpec,
} from '@/types/orgCustomization';

// TODO: temporary mock data, replace with the real API once the config endpoints are ready.
const MODULE_LABELS: Record<ModuleKey, string> = {
  [ModuleKey.Overview]: 'Overview',
  [ModuleKey.Tasks]: 'Tasks',
  [ModuleKey.Campaigns]: 'Campaigns',
  [ModuleKey.MetaAds]: 'Meta Ads',
  [ModuleKey.Decisions]: 'Decisions',
  [ModuleKey.BudgetPools]: 'Budget Pools',
  [ModuleKey.Spreadsheets]: 'Spreadsheets',
  [ModuleKey.VariationsStudio]: 'Variations Studio',
  [ModuleKey.AdsDraft]: 'Ads Draft',
  [ModuleKey.EmailDraft]: 'Email Draft',
  [ModuleKey.Notion]: 'Notion',
  [ModuleKey.Meetings]: 'Meetings',
  [ModuleKey.Calendar]: 'Calendar',
  [ModuleKey.Messages]: 'Messages',
  [ModuleKey.Miro]: 'Miro',
  [ModuleKey.Workflows]: 'Workflows',
  [ModuleKey.Timeline]: 'Timeline',
  [ModuleKey.Csm]: 'Customer Service',
  [ModuleKey.Admin]: 'Administration',
  [ModuleKey.Integrations]: 'Integrations',
  [ModuleKey.Agent]: 'AI Agent',
  [ModuleKey.Notifications]: 'Notifications',
  [ModuleKey.Shell]: 'App Shell',
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
  surface_types: SURFACE_TYPE_KEYS.map((key) => ({ key, label: key[0].toUpperCase() + key.slice(1) })),
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
  {
    [ModuleKey.MetaAds]: { enabled: false },
    [ModuleKey.Miro]: { enabled: false },
    [ModuleKey.Notion]: { enabled: false },
  },
  { 'tasks.tab.gantt': { visible: false }, 'shell.item.facebook_meta': { visible: false } },
);

export const projectOverrideConfig = buildEffectiveConfig(
  3,
  {
    [ModuleKey.Miro]: { enabled: false },
    [ModuleKey.Campaigns]: { label: 'Client Campaigns', order: 0 },
    [ModuleKey.Overview]: { order: 2 },
  },
  { 'overview.card.meetings': { visible: false } },
);

export const EFFECTIVE_FIXTURES = {
  all_enabled: allEnabledConfig,
  partially_disabled: partiallyDisabledConfig,
  project_override: projectOverrideConfig,
} satisfies Record<string, EffectiveConfig>;

export type EffectiveFixtureName = keyof typeof EFFECTIVE_FIXTURES;
