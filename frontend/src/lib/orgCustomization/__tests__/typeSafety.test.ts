/**
 * MED-454: compile-time key safety tests.
 *
 * The `@ts-expect-error` directives are verified by `tsc --noEmit`: if a
 * typo'd key ever stops being a compile error (e.g. the unions widen to
 * string), tsc reports the directive as unused and the type check fails.
 */
import type { ModuleKey, SurfaceKey } from '../keys';
import type { EffectiveConfig, ModuleConfig } from '@/types/orgCustomization';

describe('compile-time key safety', () => {
  it('rejects typo keys at compile time', () => {
    // @ts-expect-error — 'task' is not a ModuleKey
    const typoModule: ModuleKey = 'task';
    // @ts-expect-error — 'tasks.tab.gant' is not a SurfaceKey
    const typoSurface: SurfaceKey = 'tasks.tab.gant';
    // @ts-expect-error — bare strings are not assignable
    const bareString: ModuleKey = 'overview '.trim();
    expect(typoModule).toBe('task');
    expect(typoSurface).toBe('tasks.tab.gant');
    expect(bareString).toBe('overview');
  });

  it('rejects incomplete or malformed EffectiveConfig at compile time', () => {
    const valid: ModuleConfig = { enabled: true, label: null, order: 10 };
    // @ts-expect-error — modules must cover every ModuleKey
    const missingKeys: EffectiveConfig = { version: 1, modules: { tasks: valid }, surfaces: {} };
    // @ts-expect-error — 'order' must be a number
    const wrongField: ModuleConfig = { enabled: true, label: null, order: '10' };
    expect(missingKeys).toBeDefined();
    expect(wrongField).toBeDefined();
  });

  it('accepts well-formed keys and configs', () => {
    const moduleKey: ModuleKey = 'meta_ads';
    const surfaceKey: SurfaceKey = 'nav.item.ads_draft.facebook_meta';
    expect(moduleKey).toBe('meta_ads');
    expect(surfaceKey).toBe('nav.item.ads_draft.facebook_meta');
  });
});
