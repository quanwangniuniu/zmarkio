import { render, screen } from '@testing-library/react';

import CampaignPacingBadge from '@/components/campaigns/CampaignPacingBadge';
import type { CampaignPacingForecast } from '@/types/campaign';

const BASE_FORECAST: CampaignPacingForecast = {
  id: 1,
  campaign: '10',
  campaign_slug: 'mixed-platform-campaign',
  status: 'on_track',
  reason: '',
  data_quality: 'complete',
  spend_platforms: ['META'],
  unsupported_platforms: [],
  budget: '1000.00',
  spend_to_date: '400.00',
  expected_spend_to_date: '400.00',
  projected_total_spend: '1000.00',
  suggested_daily_cap: '100.00',
  avg_daily_spend: '100.00',
  pace_ratio: '1.0000',
  total_days: 10,
  days_elapsed: 4,
  days_remaining: 6,
  seasonality_applied: false,
  dow_factors: {},
  computed_for_date: '2026-10-11',
  computed_at: '2026-10-11T00:00:00Z',
};

describe('CampaignPacingBadge platform coverage', () => {
  it('keeps metrics visible and warns when a mixed-platform forecast is partial', () => {
    render(
      <CampaignPacingBadge
        variant="detail"
        pacing={{
          ...BASE_FORECAST,
          data_quality: 'partial',
          unsupported_platforms: ['GOOGLE_ADS'],
        }}
      />,
    );

    expect(screen.getByText(/Partial forecast: spend from GOOGLE_ADS is not imported/)).toBeTruthy();
    expect(screen.getByTestId('pacing-suggested-cap')).toHaveTextContent('$100');
  });

  it('explains when no configured platform has an imported spend source', () => {
    render(
      <CampaignPacingBadge
        variant="detail"
        pacing={{
          ...BASE_FORECAST,
          status: 'no_data',
          reason: 'unsupported_spend_platform',
          data_quality: 'unsupported',
          spend_platforms: [],
          unsupported_platforms: ['GOOGLE_ADS'],
          pace_ratio: null,
          suggested_daily_cap: null,
        }}
      />,
    );

    expect(screen.getByTestId('pacing-prompt')).toHaveTextContent(
      "None of this campaign's platforms currently has an imported spend source.",
    );
  });
});
