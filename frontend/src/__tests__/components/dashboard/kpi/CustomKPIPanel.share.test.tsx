import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import CustomKPIPanel from '@/components/dashboard/kpi/CustomKPIPanel';
import ReportAPI from '@/lib/api/reportApi';

jest.mock('react-hot-toast', () => ({
  toast: { success: jest.fn(), error: jest.fn() },
}));

jest.mock('@/lib/api/reportApi', () => ({
  __esModule: true,
  default: {
    listKPIs: jest.fn(),
    listKPIMetrics: jest.fn(),
    getShareLink: jest.fn(),
    createShareLink: jest.fn(),
    revokeShareLink: jest.fn(),
  },
}));

const api = ReportAPI as jest.Mocked<typeof ReportAPI>;

function renderPanel() {
  api.listKPIs.mockResolvedValue({ data: [] } as never);
  api.listKPIMetrics.mockResolvedValue({ data: { metrics: [] } } as never);
  return render(<CustomKPIPanel projectSlug="med-246" />);
}

beforeEach(() => {
  jest.clearAllMocks();
});

test('opens the share dialog without a link and creates one', async () => {
  api.getShareLink.mockResolvedValue({ data: { link: null } } as never);
  api.createShareLink.mockResolvedValue({
    data: {
      id: 1,
      token: 'tok-1',
      expires_at: new Date(Date.now() + 7 * 86400000).toISOString(),
      project: 'med-246',
      reused: false,
    },
  } as never);
  renderPanel();

  fireEvent.click(screen.getByTestId('share-kpi-button'));

  expect(await screen.findByTestId('create-share-link')).toBeInTheDocument();
  expect(api.getShareLink).toHaveBeenCalledWith('med-246');

  fireEvent.click(screen.getByTestId('create-share-link'));

  await waitFor(() => {
    expect(api.createShareLink).toHaveBeenCalledWith({ project: 'med-246', days: 7 });
  });
  expect(await screen.findByLabelText('Share link')).toHaveValue(
    `${window.location.origin}/share/kpis/tok-1`
  );
});

test('shows copy and revoke for an unexpired link', async () => {
  api.getShareLink.mockResolvedValue({
    data: {
      link: {
        id: 2,
        token: 'live-tok',
        expires_at: new Date(Date.now() + 3 * 86400000).toISOString(),
        project: 'med-246',
        days_left: 3,
      },
    },
  } as never);
  renderPanel();

  fireEvent.click(screen.getByTestId('share-kpi-button'));

  expect(await screen.findByTestId('share-kpi-days-left')).toHaveTextContent('Expires in 3 days.');
  expect(screen.getByTestId('create-share-link')).toBeDisabled();
  expect(screen.getByTestId('revoke-share-link')).toBeInTheDocument();
});
