import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import {
  DECISION_RISK,
  REVIEW_QUALITY,
  SIGNAL_METRIC,
  loadDecisionConfig,
  storeDecisionConfig,
} from '@/lib/decisions/decisionConfig';
import { optionChoices, optionLabel, preferredOption } from '@/lib/orgCustomization/optionConfig';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, editable: [], ...overrides,
});

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

const customConfig = moduleConfig([
  option('signal_metric', 'ROAS', { enabled: false, order: 0 }),
  option('signal_metric', 'PURCHASES', { label: 'Orders', order: 1 }),
  option('risk_level', 'HIGH', { label: 'Critical', color: '#DC2626', order: 2 }),
  option('review_quality', 'POOR', { label: 'Missed', order: 2 }),
]);

describe('decision config', () => {
  afterEach(() => storeDecisionConfig(1, moduleConfig([])));

  it('applies project labels, colors and hidden signal options', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(customConfig);

    await loadDecisionConfig(1);

    expect(fetchOptionConfig).toHaveBeenCalledWith('decisions', 1);
    expect(optionLabel(SIGNAL_METRIC, 'PURCHASES')).toBe('Orders');
    expect(SIGNAL_METRIC.values).not.toContain('ROAS');
    expect(preferredOption(SIGNAL_METRIC, 'ROAS')).toBe(SIGNAL_METRIC.values[0]);
    expect(optionChoices(SIGNAL_METRIC, 'ROAS').map((choice) => choice.value)).toContain('ROAS');
    expect(DECISION_RISK.labels.HIGH).toBe('Critical');
    expect(DECISION_RISK.colors.HIGH).toBe('#DC2626');
    expect(REVIEW_QUALITY.labels.POOR).toBe('Missed');
  });

  it('falls back to the built-in names when nothing is configured', () => {
    storeDecisionConfig(1, moduleConfig([]));

    expect(optionLabel(SIGNAL_METRIC, 'PURCHASES')).toBe('Purchases');
    expect(SIGNAL_METRIC.values[0]).toBe('ROAS');
    expect(DECISION_RISK.labels.HIGH).toBe('High');
    expect(DECISION_RISK.colors.HIGH).toBeUndefined();
  });
});
