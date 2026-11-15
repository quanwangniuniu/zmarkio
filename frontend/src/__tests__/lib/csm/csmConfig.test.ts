import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { CSM_PRIORITY, loadCsmConfig, storeCsmConfig } from '@/lib/csm/csmConfig';
import { visibleOptions } from '@/lib/orgCustomization/optionConfig';
import { PRIORITY_LABELS } from '@/types/csm';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, ...overrides,
});

const priorities = (overrides: Record<string, Partial<OptionConfig>> = {}) =>
  ['critical', 'high', 'medium', 'low'].map((value, index) =>
    option('ticket_priority', value, { order: index, ...overrides[value] }),
  );

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

const customConfig = moduleConfig(priorities({
  critical: { enabled: false },
  low: { label: 'Minor', color: '#123456', order: 0 },
}));

const resetConfig = moduleConfig(priorities());

describe('csm config', () => {
  afterEach(() => storeCsmConfig(1, resetConfig));

  it('relabels, recolors, hides and reorders options', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(customConfig);

    await loadCsmConfig(1);

    expect(fetchOptionConfig).toHaveBeenCalledWith('csm', 1);
    expect(PRIORITY_LABELS.low).toBe('Minor');
    expect(CSM_PRIORITY.colors.low).toBe('#123456');
    expect(CSM_PRIORITY.values).toEqual(['low', 'high', 'medium']);
    expect(visibleOptions(CSM_PRIORITY, 'critical')).toEqual(['low', 'high', 'medium', 'critical']);
  });

  it('restores defaults when overrides are cleared', () => {
    storeCsmConfig(1, customConfig);
    storeCsmConfig(1, resetConfig);

    expect(PRIORITY_LABELS.low).toBe('Low');
    expect(CSM_PRIORITY.colors.low).toBeUndefined();
    expect(CSM_PRIORITY.values).toEqual(['critical', 'high', 'medium', 'low']);
  });
});
