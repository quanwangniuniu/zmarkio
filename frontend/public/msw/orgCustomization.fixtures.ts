/**
 * MED-454: MSW fixtures for the org-customization API mocks.
 *
 * Three EffectiveConfig scenarios (all enabled / partially disabled /
 * project override) plus the /registry/ projection. Keys are derived from
 * @/lib/orgCustomization/keys, so the fixtures can never drift from the
 * frozen key lists; `Record<ModuleKey, ...>` additionally makes a missing
 * key a compile error.
 */
import { MODULE_KEYS, SURFACE_KEYS } from '@/lib/orgCustomization/keys';
import type { ModuleKey, SurfaceKey } from '@/lib/orgCustomization/keys';
import type {
  EffectiveConfig,
  ModuleConfig,
  RegistryEntry,
  RegistryResponse,
  SurfaceConfig,
} from '@/types/orgCustomization';

function buildModules(
  defaults: (key: ModuleKey, index: number) => ModuleConfig
): Record<ModuleKey, ModuleConfig> {
  return MODULE_KEYS.reduce((acc, key, index) => {
    acc[key] = defaults(key, index);
    return acc;
  }, {} as Record<ModuleKey, ModuleConfig>);
}

function buildSurfaces(
  defaults: (key: SurfaceKey, index: number) => SurfaceConfig
): Record<SurfaceKey, SurfaceConfig> {
  return SURFACE_KEYS.reduce((acc, key, index) => {
    acc[key] = defaults(key, index);
    return acc;
  }, {} as Record<SurfaceKey, SurfaceConfig>);
}

const defaultModule = (index: number): ModuleConfig => ({
  enabled: true,
  label: null,
  order: (index + 1) * 10,
});

// Matches the seed SurfaceSpec default_order values in the A1 registry.
const SURFACE_DEFAULT_ORDER: Record<SurfaceKey, number> = {
  'nav.group.manage': 0,
  'nav.item.tasks': 10,
  'nav.item.ads_draft.facebook_meta': 20,
  'tasks.tab.gantt': 30,
  'overview.card.meetings': 40,
  'integrations.provider.slack': 10,
  'shell.usermenu.subscription': 10,
};

const defaultSurface = (key: SurfaceKey): SurfaceConfig => ({
  visible: true,
  label: null,
  order: SURFACE_DEFAULT_ORDER[key],
});

/** Scenario 1: every module enabled, every surface visible, registry defaults. */
export const allEnabledFixture: EffectiveConfig = {
  version: 1,
  modules: buildModules((_key, index) => defaultModule(index)),
  surfaces: buildSurfaces((key) => defaultSurface(key)),
};

/** Scenario 2: a few modules off and some surfaces hidden or relabeled. */
export const partiallyDisabledFixture: EffectiveConfig = {
  version: 1,
  modules: buildModules((key, index) => {
    const config = defaultModule(index);
    if (key === 'miro' || key === 'csm' || key === 'timeline') {
      return { ...config, enabled: false };
    }
    if (key === 'tasks') {
      return { ...config, label: 'Work Items' };
    }
    return config;
  }),
  surfaces: buildSurfaces((key) => {
    const config = defaultSurface(key);
    if (key === 'tasks.tab.gantt') {
      return { ...config, visible: false };
    }
    if (key === 'integrations.provider.slack') {
      return { ...config, label: 'Slack Connect' };
    }
    return config;
  }),
};

/** Scenario 3: project-level overrides layered on top (version bumped). */
export const projectOverrideFixture: EffectiveConfig = {
  version: 2,
  modules: buildModules((key, index) => {
    const config = defaultModule(index);
    if (key === 'overview') {
      return { ...config, label: 'Project Home', order: 5 };
    }
    if (key === 'agent') {
      return { ...config, enabled: false };
    }
    return config;
  }),
  surfaces: buildSurfaces((key) => {
    const config = defaultSurface(key);
    if (key === 'overview.card.meetings') {
      return { ...config, visible: false, order: 5 };
    }
    if (key === 'shell.usermenu.subscription') {
      return { ...config, label: 'Plan' };
    }
    return config;
  }),
};

// Labels mirror the A1 registry (backend/org_customization/registry/__init__.py).
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
  csm: 'CSM',
  admin: 'Admin',
  integrations: 'Integrations',
  agent: 'Agent',
  notifications: 'Notifications',
  shell: 'Shell',
};

const SURFACE_LABELS: Record<SurfaceKey, string> = {
  'nav.group.manage': 'Manage',
  'nav.item.tasks': 'Tasks',
  'nav.item.ads_draft.facebook_meta': 'Meta Ads Draft',
  'tasks.tab.gantt': 'Gantt',
  'overview.card.meetings': 'Meetings',
  'integrations.provider.slack': 'Slack',
  'shell.usermenu.subscription': 'Subscription',
};

/** Scenario selection map for GET /api/org-customization/effective/. */
export const EFFECTIVE_FIXTURES = {
  'all-enabled': allEnabledFixture,
  'partially-disabled': partiallyDisabledFixture,
  'project-override': projectOverrideFixture,
} as const;

export type EffectiveFixtureName = keyof typeof EFFECTIVE_FIXTURES;

/** The /api/org-customization/registry/ projection (mirrors the A1 endpoint). */
export const registryFixture: RegistryResponse = {
  modules: MODULE_KEYS.map((key, index): RegistryEntry => ({
    key,
    label: MODULE_LABELS[key],
    default_visible: true,
    default_order: (index + 1) * 10,
    route_prefixes: [],
  })),
  surfaces: SURFACE_KEYS.map((key): RegistryEntry => ({
    key,
    label: SURFACE_LABELS[key],
    default_visible: true,
    default_order: SURFACE_DEFAULT_ORDER[key],
    route_prefixes: [],
  })),
};
