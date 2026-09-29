import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import PublicKPIShareView from '@/components/dashboard/kpi/PublicKPIShare';
import { getPublicKPIShare } from '@/lib/api/reportPublicApi';

jest.mock('@/lib/api/reportPublicApi', () => ({
  getPublicKPIShare: jest.fn(),
}));

const getShare = getPublicKPIShare as jest.MockedFunction<typeof getPublicKPIShare>;

beforeEach(() => {
  jest.clearAllMocks();
});

test('renders read-only KPI cards from a public share', async () => {
  getShare.mockResolvedValue({
    data: {
      expires_at: '2026-10-06T00:00:00Z',
      kpis: [
        {
          name: 'Blended ROAS',
          formula: 'revenue / spend',
          display_format: 'number',
          value: '1.25',
          error: null,
        },
        {
          name: 'Empty',
          formula: 'leads / clicks',
          display_format: 'percent',
          value: null,
          error: { code: '#NODATA', message: 'No data for this period.' },
        },
      ],
    },
  } as never);

  render(<PublicKPIShareView token="tok-1" />);

  expect(await screen.findByTestId('public-kpi-tile-value')).toHaveTextContent('1.25');
  expect(screen.getByText('revenue / spend')).toBeInTheDocument();
  expect(screen.getByTestId('public-kpi-tile-no-data')).toHaveTextContent(
    'No data for this period.'
  );
  expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
  expect(getShare).toHaveBeenCalledWith('tok-1');
});

test('shows expiry and a missing link as distinct states', async () => {
  getShare.mockRejectedValueOnce({ response: { status: 410 } });
  const { unmount } = render(<PublicKPIShareView token="old" />);
  expect(await screen.findByTestId('public-kpi-expired')).toHaveTextContent(
    'This link has expired.'
  );
  unmount();

  getShare.mockRejectedValueOnce({ response: { status: 404 } });
  render(<PublicKPIShareView token="gone" />);
  await waitFor(() => {
    expect(screen.getByTestId('public-kpi-missing')).toHaveTextContent(
      'This link is not available.'
    );
  });
});
