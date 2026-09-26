/**
 * MED-454: key constant and type guard tests. The frozen lists asserted here
 * mirror backend/org_customization/tests/test_registry.py character for
 * character — if one side changes, both must.
 */
import {
  MODULE_KEYS,
  NAV_NAMESPACE,
  NAV_OWNER_MODULE,
  SURFACE_KEYS,
  SURFACE_TYPES,
  isModuleKey,
  isSurfaceKey,
  moduleKeyForSurface,
} from '../keys';

const EXPECTED_MODULE_KEYS = [
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

const EXPECTED_SURFACE_KEYS = [
  'nav.group.manage',
  'nav.item.tasks',
  'nav.item.ads_draft.facebook_meta',
  'tasks.tab.gantt',
  'overview.card.meetings',
  'integrations.provider.slack',
  'shell.usermenu.subscription',
] as const;

describe('frozen key lists', () => {
  it('MODULE_KEYS matches the 23-module contract exactly', () => {
    expect([...MODULE_KEYS]).toEqual([...EXPECTED_MODULE_KEYS]);
    expect(MODULE_KEYS).toHaveLength(23);
  });

  it('SURFACE_KEYS matches the frozen example keys exactly', () => {
    expect([...SURFACE_KEYS]).toEqual([...EXPECTED_SURFACE_KEYS]);
  });

  it('SURFACE_TYPES matches the frozen type enum', () => {
    expect([...SURFACE_TYPES]).toEqual([
      'group',
      'item',
      'tab',
      'card',
      'panel',
      'provider',
      'filter',
      'section',
      'usermenu',
    ]);
  });

  it('every surface key follows <module>.<type>.<name>', () => {
    for (const key of SURFACE_KEYS) {
      const [moduleSegment, typeSegment, ...nameSegments] = key.split('.');
      expect(moduleSegment === NAV_NAMESPACE || EXPECTED_MODULE_KEYS.includes(moduleSegment as never)).toBe(true);
      expect([...SURFACE_TYPES]).toContain(typeSegment);
      expect(nameSegments.length).toBeGreaterThan(0);
    }
  });
});

describe('type guards', () => {
  it('isModuleKey accepts registered keys only', () => {
    expect(isModuleKey('tasks')).toBe(true);
    expect(isModuleKey('meta_ads')).toBe(true);
    expect(isModuleKey('shell')).toBe(true);
    expect(isModuleKey('nope')).toBe(false);
    expect(isModuleKey('tasks.tab.gantt')).toBe(false);
    expect(isModuleKey('Tasks')).toBe(false);
    expect(isModuleKey(undefined)).toBe(false);
    expect(isModuleKey(42)).toBe(false);
  });

  it('isSurfaceKey accepts registered keys only', () => {
    expect(isSurfaceKey('tasks.tab.gantt')).toBe(true);
    expect(isSurfaceKey('nav.item.ads_draft.facebook_meta')).toBe(true);
    expect(isSurfaceKey('tasks.tab.gant')).toBe(false); // typo
    expect(isSurfaceKey('tasks')).toBe(false);
    expect(isSurfaceKey(null)).toBe(false);
    expect(isSurfaceKey({})).toBe(false);
  });
});

describe('moduleKeyForSurface', () => {
  it('uses the module segment of the surface key', () => {
    expect(moduleKeyForSurface('tasks.tab.gantt')).toBe('tasks');
    expect(moduleKeyForSurface('overview.card.meetings')).toBe('overview');
    expect(moduleKeyForSurface('integrations.provider.slack')).toBe('integrations');
  });

  it('maps the nav namespace to shell', () => {
    expect(moduleKeyForSurface('nav.group.manage')).toBe(NAV_OWNER_MODULE);
    expect(moduleKeyForSurface('nav.item.ads_draft.facebook_meta')).toBe(NAV_OWNER_MODULE);
    expect(NAV_OWNER_MODULE).toBe('shell');
  });
});
