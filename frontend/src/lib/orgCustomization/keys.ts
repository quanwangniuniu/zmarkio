export enum ModuleKey {
  Overview = 'overview',
  Tasks = 'tasks',
  Campaigns = 'campaigns',
  MetaAds = 'meta_ads',
  Decisions = 'decisions',
  BudgetPools = 'budget_pools',
  Spreadsheets = 'spreadsheets',
  VariationsStudio = 'variations_studio',
  AdsDraft = 'ads_draft',
  EmailDraft = 'email_draft',
  Notion = 'notion',
  Meetings = 'meetings',
  Calendar = 'calendar',
  Messages = 'messages',
  Miro = 'miro',
  Workflows = 'workflows',
  Timeline = 'timeline',
  Csm = 'csm',
  Admin = 'admin',
  Integrations = 'integrations',
  Agent = 'agent',
  Notifications = 'notifications',
  Shell = 'shell',
}

export enum SurfaceTypeKey {
  Group = 'group',
  Item = 'item',
  Tab = 'tab',
  Card = 'card',
  Panel = 'panel',
  Provider = 'provider',
  Filter = 'filter',
  Section = 'section',
}

export const MODULE_KEYS = Object.values(ModuleKey);
export const SURFACE_TYPE_KEYS = Object.values(SurfaceTypeKey);

export type SurfaceKey = `${ModuleKey}.${SurfaceTypeKey}.${string}`;

const NAME_PATTERN = /^[a-z][a-z0-9_]*$/;

export function isModuleKey(value: unknown): value is ModuleKey {
  return typeof value === 'string' && (MODULE_KEYS as string[]).includes(value);
}

export function isSurfaceTypeKey(value: unknown): value is SurfaceTypeKey {
  return typeof value === 'string' && (SURFACE_TYPE_KEYS as string[]).includes(value);
}

export function isSurfaceKey(value: unknown): value is SurfaceKey {
  if (typeof value !== 'string') return false;
  const parts = value.split('.');
  return (
    parts.length === 3 &&
    isModuleKey(parts[0]) &&
    isSurfaceTypeKey(parts[1]) &&
    NAME_PATTERN.test(parts[2])
  );
}
