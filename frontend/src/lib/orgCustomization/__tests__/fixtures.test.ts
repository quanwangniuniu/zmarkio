/**
 * MED-454: fixture tests — shape guards and snapshots for the three
 * EffectiveConfig scenarios plus the registry projection.
 */
import {
  EFFECTIVE_FIXTURES,
  allEnabledFixture,
  partiallyDisabledFixture,
  projectOverrideFixture,
  registryFixture,
} from '../../../../public/msw/orgCustomization.fixtures';
import { MODULE_KEYS, SURFACE_KEYS, isModuleKey, isSurfaceKey } from '../keys';
import { isEffectiveConfig } from '@/types/orgCustomization';

describe('effective config fixtures', () => {
  it.each([
    ['allEnabledFixture', allEnabledFixture],
    ['partiallyDisabledFixture', partiallyDisabledFixture],
    ['projectOverrideFixture', projectOverrideFixture],
  ])('%s passes the EffectiveConfig type guard', (_name, fixture) => {
    expect(isEffectiveConfig(fixture)).toBe(true);
  });

  it('every fixture covers every frozen key', () => {
    for (const fixture of [allEnabledFixture, partiallyDisabledFixture, projectOverrideFixture]) {
      expect(Object.keys(fixture.modules).sort()).toEqual([...MODULE_KEYS].sort());
      expect(Object.keys(fixture.surfaces).sort()).toEqual([...SURFACE_KEYS].sort());
    }
  });

  it('all-enabled turns nothing off', () => {
    expect(
      Object.values(allEnabledFixture.modules).every((config) => config.enabled)
    ).toBe(true);
    expect(
      Object.values(allEnabledFixture.surfaces).every((config) => config.visible)
    ).toBe(true);
  });

  it('partially-disabled actually disables modules and surfaces', () => {
    expect(partiallyDisabledFixture.modules.miro.enabled).toBe(false);
    expect(partiallyDisabledFixture.modules.csm.enabled).toBe(false);
    expect(partiallyDisabledFixture.surfaces['tasks.tab.gantt'].visible).toBe(false);
    expect(partiallyDisabledFixture.modules.tasks.label).toBe('Work Items');
  });

  it('project-override bumps the version and layers overrides', () => {
    expect(projectOverrideFixture.version).toBe(2);
    expect(projectOverrideFixture.modules.overview.label).toBe('Project Home');
    expect(projectOverrideFixture.modules.agent.enabled).toBe(false);
  });

  it('matches the stored snapshot', () => {
    expect(allEnabledFixture).toMatchSnapshot();
    expect(partiallyDisabledFixture).toMatchSnapshot();
    expect(projectOverrideFixture).toMatchSnapshot();
  });
});

describe('registry fixture', () => {
  it('covers the frozen key lists', () => {
    expect(registryFixture.modules.map((entry) => entry.key)).toEqual([...MODULE_KEYS]);
    expect(registryFixture.surfaces.map((entry) => entry.key)).toEqual([...SURFACE_KEYS]);
  });

  it('matches the stored snapshot', () => {
    expect(registryFixture).toMatchSnapshot();
  });
});

describe('fixture selection map', () => {
  it('exposes the three scenarios by name', () => {
    expect(Object.keys(EFFECTIVE_FIXTURES)).toEqual([
      'all-enabled',
      'partially-disabled',
      'project-override',
    ]);
    expect(EFFECTIVE_FIXTURES['all-enabled']).toBe(allEnabledFixture);
    expect(EFFECTIVE_FIXTURES['partially-disabled']).toBe(partiallyDisabledFixture);
    expect(EFFECTIVE_FIXTURES['project-override']).toBe(projectOverrideFixture);
  });
});

describe('key guards stay wired to the fixtures', () => {
  it('fixture keys are accepted by the type guards', () => {
    for (const key of Object.keys(allEnabledFixture.modules)) {
      expect(isModuleKey(key)).toBe(true);
    }
    for (const key of Object.keys(allEnabledFixture.surfaces)) {
      expect(isSurfaceKey(key)).toBe(true);
    }
  });
});
