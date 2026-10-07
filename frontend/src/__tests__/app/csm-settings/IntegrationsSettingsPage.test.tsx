import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import IntegrationsSettingsPage from '@/app/(project)/admin/csm/settings/integrations/page';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';

jest.mock('@/lib/api/csmIntegrationsApi', () => ({
  __esModule: true,
  CsmIntegrationsAPI: { vocabulary: jest.fn() },
}));

jest.mock('@/components/auth/ProtectedRoute', () => ({
  __esModule: true,
  ProtectedRoute: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

jest.mock('@/components/csm-settings/useProjectIdFromUrl', () => ({
  useProjectIdFromUrl: () => ({ projectId: 1, projectValid: true }),
}));

jest.mock('@/components/csm-settings/SettingsHubLink', () => ({ __esModule: true, default: () => null }));

// Each panel has its own test; stub them so this file stays on the page.
jest.mock('@/components/csm-settings/integrations/CredentialsPanel', () => ({
  __esModule: true,
  default: ({ kind, resources }: { kind: string; resources: { value: string }[] }) => (
    <div>credentials:{kind}:{resources.map((r) => r.value).join(',')}</div>
  ),
}));
jest.mock('@/components/csm-settings/integrations/WebhooksPanel', () => ({
  __esModule: true,
  default: ({ events }: { events: { value: string }[] }) => <div>webhooks:{events.map((e) => e.value).join(',')}</div>,
}));

const vocabulary = CsmIntegrationsAPI.vocabulary as jest.Mock;

beforeEach(() => jest.clearAllMocks());

describe('IntegrationsSettingsPage', () => {
  it('switches between API keys, OAuth clients and webhooks', async () => {
    vocabulary.mockResolvedValue({
      resources: [{ value: 'tickets', label: 'Tickets' }],
      events: [{ value: 'ticket.created', label: 'Ticket created' }],
    });
    render(<IntegrationsSettingsPage />);

    expect(await screen.findByText('credentials:api-key:tickets')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'OAuth clients' }));
    expect(screen.getByText('credentials:oauth-client:tickets')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Webhooks' }));
    expect(screen.getByText('webhooks:ticket.created')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Webhooks' })).toHaveAttribute('aria-selected', 'true');
  });

  it('explains a 403 instead of showing the panels', async () => {
    vocabulary.mockRejectedValue({ response: { status: 403 } });
    render(<IntegrationsSettingsPage />);

    expect(await screen.findByText(/Only an organisation admin or a CSM admin/)).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });
});
