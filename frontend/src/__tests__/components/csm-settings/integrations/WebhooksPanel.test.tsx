import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import toast from 'react-hot-toast';
import WebhooksPanel from '@/components/csm-settings/integrations/WebhooksPanel';
import { validateWebhookUrl } from '@/components/csm-settings/integrations/WebhookEndpointDrawer';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { WebhookDelivery, WebhookEndpoint } from '@/types/csmIntegrations';

jest.mock('@/lib/api/csmIntegrationsApi', () => ({
  __esModule: true,
  CsmIntegrationsAPI: {
    listWebhooks: jest.fn(),
    createWebhook: jest.fn(),
    updateWebhook: jest.fn(),
    deleteWebhook: jest.fn(),
    rotateWebhookSecret: jest.fn(),
    sendTestEvent: jest.fn(),
    listDeliveries: jest.fn(),
  },
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

const api = CsmIntegrationsAPI as jest.Mocked<typeof CsmIntegrationsAPI>;
const EVENTS = [
  { value: 'ticket.created', label: 'Ticket created' },
  { value: 'ticket.status_changed', label: 'Ticket status changed' },
  { value: 'sla.breached', label: 'SLA breached' },
];

const endpoint: WebhookEndpoint = {
  id: 5,
  url: 'https://hooks.example.com/zmarkio',
  description: 'PagerDuty',
  events: ['sla.breached'],
  is_active: true,
  created_at: '2026-10-01T00:00:00Z',
};

const delivery = (overrides: Partial<WebhookDelivery> = {}): WebhookDelivery => ({
  id: 70,
  event_type: 'sla.breached',
  target_url: endpoint.url,
  attempt: 4,
  status: 'failed',
  response_code: 503,
  error: 'HTTP 503',
  created_at: '2026-10-02T00:00:00Z',
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  api.listWebhooks.mockResolvedValue([endpoint]);
  api.listDeliveries.mockResolvedValue([delivery()]);
});

const renderPanel = async () => {
  render(<WebhooksPanel projectId={1} events={EVENTS} />);
  await screen.findByText('PagerDuty');
};

describe('validateWebhookUrl', () => {
  it('accepts https and rejects everything else', () => {
    expect(validateWebhookUrl('https://example.com/hook')).toBeNull();
    expect(validateWebhookUrl('')).toBe('URL is required.');
    expect(validateWebhookUrl('http://example.com')).toBe('Use an https:// URL.');
    expect(validateWebhookUrl('not a url')).toBe('Enter a valid URL.');
  });
});

describe('WebhooksPanel', () => {
  it('lists endpoints and the delivery log with url, event, code, attempt and time', async () => {
    await renderPanel();

    const log = screen.getByRole('region', { name: 'Delivery log' });
    expect(within(log).getByText('503')).toBeInTheDocument();
    expect(within(log).getByText('4 / 4')).toBeInTheDocument();
    expect(within(log).getAllByText('SLA breached').length).toBeGreaterThan(0);
    expect(api.listDeliveries).toHaveBeenCalledWith(1, null);
  });

  it('registers an endpoint and reveals the signing secret once', async () => {
    api.createWebhook.mockResolvedValue({ ...endpoint, id: 6, description: '', events: ['ticket.created'], secret: 'whsec_abc' });
    await renderPanel();

    fireEvent.click(screen.getByRole('button', { name: /New webhook/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create webhook' }));
    expect(await screen.findByText('URL is required.')).toBeInTheDocument();
    expect(screen.getByText('Subscribe to at least one event.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Endpoint URL/), { target: { value: 'https://hooks.example.com/new' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /Ticket created/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create webhook' }));

    await waitFor(() => expect(api.createWebhook).toHaveBeenCalledWith(1, {
      url: 'https://hooks.example.com/new', description: '', events: ['ticket.created'], is_active: true,
    }));
    expect(await screen.findByTestId('revealed-Signing secret')).toHaveTextContent('whsec_abc');
  });

  it('shows the server URL error (e.g. a private host)', async () => {
    api.createWebhook.mockRejectedValue({ response: { data: { url: ['URL resolves to a non-public address: 10.0.0.1'] } } });
    await renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /New webhook/ }));
    fireEvent.change(screen.getByLabelText(/Endpoint URL/), { target: { value: 'https://internal.example.com' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /SLA breached/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create webhook' }));

    expect(await screen.findByText(/non-public address/)).toBeInTheDocument();
  });

  it('sends a test event and focuses the log on that endpoint', async () => {
    api.sendTestEvent.mockResolvedValue({ data: { event_id: 'x' } } as never);
    await renderPanel();

    fireEvent.click(screen.getByRole('button', { name: `Send test event to ${endpoint.url}` }));

    await waitFor(() => expect(api.sendTestEvent).toHaveBeenCalledWith(1, 5));
    await waitFor(() => expect(api.listDeliveries).toHaveBeenLastCalledWith(1, 5));
    expect(toast.success).toHaveBeenCalled();
  });

  it('rotates the secret after confirmation', async () => {
    api.rotateWebhookSecret.mockResolvedValue({ ...endpoint, secret: 'whsec_new' });
    await renderPanel();

    fireEvent.click(screen.getByRole('button', { name: `Rotate secret of ${endpoint.url}` }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Rotate' }));

    expect(await screen.findByTestId('revealed-Signing secret')).toHaveTextContent('whsec_new');
  });

  it('deletes after confirmation', async () => {
    api.deleteWebhook.mockResolvedValue({} as never);
    await renderPanel();

    fireEvent.click(screen.getByRole('button', { name: `Delete ${endpoint.url}` }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(screen.queryByText('PagerDuty')).not.toBeInTheDocument());
    expect(api.deleteWebhook).toHaveBeenCalledWith(1, 5);
  });

  it('shows the failure reason on the HTTP cell and clears the endpoint filter', async () => {
    await renderPanel();
    fireEvent.click(screen.getByRole('button', { name: `View log of ${endpoint.url}` }));
    await waitFor(() => expect(api.listDeliveries).toHaveBeenLastCalledWith(1, 5));

    expect(screen.getByTitle('HTTP 503')).toHaveTextContent('503');
    fireEvent.change(screen.getByLabelText('Filter by endpoint'), { target: { value: '' } });
    await waitFor(() => expect(api.listDeliveries).toHaveBeenLastCalledWith(1, null));
  });
});
