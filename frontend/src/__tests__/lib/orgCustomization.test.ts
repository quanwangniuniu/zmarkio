import {
  MODULE_KEYS,
  SURFACE_TYPE_KEYS,
  isModuleKey,
  isSurfaceKey,
  isSurfaceTypeKey,
  type SurfaceKey,
} from '@/lib/orgCustomization/keys';
import {
  EFFECTIVE_FIXTURES,
  allEnabledConfig,
  partiallyDisabledConfig,
  projectOverrideConfig,
  registryFixture,
} from '@/lib/orgCustomization/mocks/fixtures';

describe('org customization keys', () => {
  it('matches the backend surface types', () => {
    expect(SURFACE_TYPE_KEYS).toEqual(['group', 'item', 'tab', 'card', 'panel', 'provider', 'filter', 'section']);
  });

  it.each(['tasks', 'meta_ads', 'shell'])('accepts module key %s', (key) => {
    expect(isModuleKey(key)).toBe(true);
  });

  it.each(['nav', 'Tasks', 'meta-ads', '', null, undefined, 1])('rejects module key %p', (key) => {
    expect(isModuleKey(key)).toBe(false);
  });

  it.each(['tab', 'provider'])('accepts surface type %s', (key) => {
    expect(isSurfaceTypeKey(key)).toBe(true);
  });

  it.each(['usermenu', 'Tab', null])('rejects surface type %p', (key) => {
    expect(isSurfaceTypeKey(key)).toBe(false);
  });

  it.each(['tasks.tab.gantt', 'overview.card.meetings', 'integrations.provider.slack', 'shell.item.my_actions'])(
    'accepts surface key %s',
    (key) => {
      expect(isSurfaceKey(key)).toBe(true);
    },
  );

  it.each([
    'nav.item.tasks',
    'shell.usermenu.subscription',
    'nav.item.ads_draft.facebook_meta',
    'tasks.tab',
    'tasks.tab.Gantt',
    'tasks.tab.my-actions',
    'tasks.tab.',
    null,
    42,
  ])('rejects surface key %p', (key) => {
    expect(isSurfaceKey(key)).toBe(false);
  });

  it('rejects a typo in the module or type at compile time', () => {
    // @ts-expect-error
    const moduleTypo: SurfaceKey = 'taks.tab.gantt';
    // @ts-expect-error
    const typeTypo: SurfaceKey = 'tasks.tabb.gantt';
    expect(isSurfaceKey(moduleTypo)).toBe(false);
    expect(isSurfaceKey(typeTypo)).toBe(false);
  });
});

describe('org customization fixtures', () => {
  it('registry fixture lists every module and surface type in order', () => {
    expect(registryFixture.modules.map((spec) => spec.key)).toEqual([...MODULE_KEYS]);
    expect(registryFixture.surface_types.map((spec) => spec.key)).toEqual([...SURFACE_TYPE_KEYS]);
  });

  it('registry fixture surfaces are valid and their parents are registered', () => {
    const keys = registryFixture.surfaces.map((spec) => spec.key);
    expect(new Set(keys).size).toBe(keys.length);
    registryFixture.surfaces.forEach((spec) => {
      expect(isSurfaceKey(spec.key)).toBe(true);
      if (spec.parent !== null) expect(keys).toContain(spec.parent);
    });
  });

  it.each(Object.entries(EFFECTIVE_FIXTURES))('%s covers every registered module and surface', (_, config) => {
    expect(Object.keys(config.modules)).toEqual([...MODULE_KEYS]);
    expect(Object.keys(config.surfaces)).toEqual(registryFixture.surfaces.map((spec) => spec.key));
  });

  it('all enabled fixture turns everything on with registry defaults', () => {
    expect(Object.values(allEnabledConfig.modules).every((config) => config.enabled && config.label === null)).toBe(true);
    expect(Object.values(allEnabledConfig.surfaces).every((config) => config.visible)).toBe(true);
    expect(allEnabledConfig).toMatchSnapshot();
  });

  it('partially disabled fixture turns some modules and surfaces off', () => {
    expect(partiallyDisabledConfig.modules.meta_ads.enabled).toBe(false);
    expect(partiallyDisabledConfig.modules.tasks.enabled).toBe(true);
    expect(partiallyDisabledConfig.surfaces['tasks.tab.gantt'].visible).toBe(false);
  });

  it('project override fixture relabels and reorders modules', () => {
    expect(projectOverrideConfig.modules.campaigns).toEqual({ enabled: true, label: 'Client Campaigns', order: 0 });
    expect(projectOverrideConfig.modules.meta_ads.enabled).toBe(true);
    expect(projectOverrideConfig.surfaces['overview.card.meetings'].visible).toBe(false);
  });
});
