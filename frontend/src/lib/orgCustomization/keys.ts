export const MODULE_KEYS = [
  'overview',
  'tasks',
  'campaigns',
  'meta_ads',
  'decisions',
  'budget_pools',
  'spreadsheets',
  'variations_studio',
  'ads_draft',
  'email_draft',
  'notion',
  'meetings',
  'calendar',
  'messages',
  'miro',
  'workflows',
  'timeline',
  'csm',
  'admin',
  'integrations',
  'agent',
  'notifications',
  'shell',
] as const;

export const SURFACE_TYPE_KEYS = [
  'group',
  'item',
  'tab',
  'card',
  'panel',
  'provider',
  'filter',
  'section',
] as const;

export type ModuleKey = (typeof MODULE_KEYS)[number];
export type SurfaceTypeKey = (typeof SURFACE_TYPE_KEYS)[number];
export type SurfaceKey = `${ModuleKey}.${SurfaceTypeKey}.${string}`;

const NAME_PATTERN = /^[a-z][a-z0-9_]*$/;

export function isModuleKey(value: unknown): value is ModuleKey {
  return typeof value === 'string' && (MODULE_KEYS as readonly string[]).includes(value);
}

export function isSurfaceTypeKey(value: unknown): value is SurfaceTypeKey {
  return typeof value === 'string' && (SURFACE_TYPE_KEYS as readonly string[]).includes(value);
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
