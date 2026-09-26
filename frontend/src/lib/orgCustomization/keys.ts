/**
 * MED-454: module/surface key constants — the frontend mirror of the A1
 * registry contract (backend/org_customization/registry/).
 *
 * The key lists below are FROZEN and must match the backend registries
 * character for character. Surface keys follow the naming spec
 * `<module>.<type>.<name>` (snake_case segments; the name may contain dots).
 * The `type` segment enum is SURFACE_TYPES. `nav` is a reserved namespace;
 * `nav.*` surfaces are owned by the `shell` module
 * (see moduleKeyForSurface below).
 *
 * Any change to the naming spec or key lists must be mirrored in
 * backend/org_customization/registry/ and agreed with its owner.
 */

/** The 23 module keys — complete frozen list (MED-453). */
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

export type ModuleKey = (typeof MODULE_KEYS)[number];

/**
 * Surface keys — the examples frozen in the naming spec. Individual module
 * surfaces are added by the M4 module issues, mirrored on both sides.
 */
export const SURFACE_KEYS = [
  'nav.group.manage',
  'nav.item.tasks',
  'nav.item.ads_draft.facebook_meta',
  'tasks.tab.gantt',
  'overview.card.meetings',
  'integrations.provider.slack',
  'shell.usermenu.subscription',
] as const;

export type SurfaceKey = (typeof SURFACE_KEYS)[number];

/**
 * The `<type>` segment enum of the naming spec. `usermenu` is not in the
 * original eight but appears in the frozen example
 * `shell.usermenu.subscription` and must stay registrable.
 */
export const SURFACE_TYPES = [
  'group',
  'item',
  'tab',
  'card',
  'panel',
  'provider',
  'filter',
  'section',
  'usermenu',
] as const;

export type SurfaceType = (typeof SURFACE_TYPES)[number];

/** Reserved surface namespace; `nav.*` surfaces belong to the `shell` module. */
export const NAV_NAMESPACE = 'nav';
export const NAV_OWNER_MODULE = 'shell';

const MODULE_KEY_SET: ReadonlySet<string> = new Set(MODULE_KEYS);
const SURFACE_KEY_SET: ReadonlySet<string> = new Set(SURFACE_KEYS);

export function isModuleKey(value: unknown): value is ModuleKey {
  return typeof value === 'string' && MODULE_KEY_SET.has(value);
}

export function isSurfaceKey(value: unknown): value is SurfaceKey {
  return typeof value === 'string' && SURFACE_KEY_SET.has(value);
}

/**
 * Owner module key for a surface key — mirrors `module_key_for_surface()` in
 * backend/org_customization/registry/base.py (nav.* rows denormalize to shell).
 */
export function moduleKeyForSurface(surfaceKey: string): string {
  const firstSegment = surfaceKey.split('.', 1)[0];
  return firstSegment === NAV_NAMESPACE ? NAV_OWNER_MODULE : firstSegment;
}
