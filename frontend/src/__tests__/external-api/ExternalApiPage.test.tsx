import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import ExternalApiPage from '@/app/(project)/admin/external-api/page';
import { ExternalApiAdminAPI } from '@/external-api/api';

jest.mock('@/external-api/api', () => ({
  __esModule: true,
  ExternalApiAdminAPI: { vocabulary: jest.fn() },
}));

jest.mock('@/components/auth/ProtectedRoute', () => ({
  __esModule: true,
  ProtectedRoute: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

jest.mock('@/lib/useActiveProjectForFlatRoute', () => ({
  useActiveProjectForFlatRoute: () => ({ activeProject: { id: 1 }, projectId: 1, projectContextLoading: false }),
}));

jest.mock('@/components/dashboard/DashboardLayout', () => ({
  __esModule: true,
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));


// Each panel has its own test; stub them so this file stays on the page.
jest.mock('@/external-api/components/CredentialsPanel', () => ({
  __esModule: true,
  default: ({ kind, resources }: { kind: string; resources: { value: string }[] }) => (
    <div>credentials:{kind}:{resources.map((r) => r.value).join(',')}</div>
  ),
}));
jest.mock('@/external-api/components/WebhooksPanel', () => ({
  __esModule: true,
  default: ({ events }: { events: { value: string }[] }) => <div>webhooks:{events.map((e) => e.value).join(',')}</div>,
}));

const vocabulary = ExternalApiAdminAPI.vocabulary as jest.Mock;

beforeEach(() => jest.clearAllMocks());

describe('ExternalApiPage', () => {
  it('switches between API keys, OAuth clients and webhooks', async () => {
    vocabulary.mockResolvedValue({
      resources: [{ value: 'tickets', label: 'Tickets' }],
      events: [{ value: 'ticket.created', label: 'Ticket created' }],
    });
    render(<ExternalApiPage />);

    expect(await screen.findByText('credentials:api-key:tickets')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'OAuth clients' }));
    expect(screen.getByText('credentials:oauth-client:tickets')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    expect(screen.getByText('webhooks:ticket.created')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Webhooks' })).toHaveAttribute('aria-selected', 'true');
  });

  it('explains a 403 instead of showing the panels', async () => {
    vocabulary.mockRejectedValue({ response: { status: 403 } });
    render(<ExternalApiPage />);

    expect(await screen.findByText(/Only an organisation admin or a CSM admin/)).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });
});
