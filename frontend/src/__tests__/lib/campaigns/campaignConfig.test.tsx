import { act, render, screen } from '@testing-library/react';
import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import CampaignStatusPill from '@/components/campaigns/pills/CampaignStatusPill';
import {
  CAMPAIGN_PLATFORM,
  CAMPAIGN_STATUS,
  CHECKIN_SENTIMENT,
  loadCampaignConfig,
  storeCampaignConfig,
} from '@/lib/campaigns/campaignConfig';
import { optionChoices, optionLabel } from '@/lib/orgCustomization/optionConfig';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, editable: [], ...overrides,
});

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

const customConfig = moduleConfig([
  option('platform', 'PINTEREST', { enabled: false, order: 0 }),
  option('platform', 'TIKTOK', { label: 'TikTok Ads', order: 1 }),
  option('status', 'SCALING', { label: 'Growing', color: '#16A34A' }),
  option('checkin_sentiment', 'NEGATIVE', { label: 'At risk' }),
]);

describe('campaign config', () => {
  afterEach(() => act(() => storeCampaignConfig(1, moduleConfig([]))));

  it('applies project labels, colors and hidden platforms', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(customConfig);

    await loadCampaignConfig(1);

    expect(fetchOptionConfig).toHaveBeenCalledWith('campaigns', 1);
    expect(optionLabel(CAMPAIGN_PLATFORM, 'TIKTOK')).toBe('TikTok Ads');
    expect(CAMPAIGN_PLATFORM.values).not.toContain('PINTEREST');
    expect(optionChoices(CAMPAIGN_PLATFORM, ['META', 'PINTEREST']).map((choice) => choice.value)).toContain('PINTEREST');
    expect(CAMPAIGN_STATUS.colors.SCALING).toBe('#16A34A');
    expect(CHECKIN_SENTIMENT.labels.NEGATIVE).toBe('At risk');
  });

  it('rerenders the status pill when the project config changes', () => {
    render(<CampaignStatusPill status="SCALING" />);
    expect(screen.getByText('Scaling')).toBeInTheDocument();

    act(() => storeCampaignConfig(1, customConfig));

    expect(screen.getByText('Growing')).toHaveStyle({ color: '#16A34A' });
  });
});
