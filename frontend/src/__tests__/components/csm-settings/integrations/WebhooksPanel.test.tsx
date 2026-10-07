import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import toast from 'react-hot-toast';
import WebhooksPanel from '@/components/csm-settings/integrations/WebhooksPanel';
import { validateWebhookUrl } from '@/components/csm-settings/integrations/WebhookEndpointDrawer';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { WebhookDelivery, WebhookEndpoint, WebhookEventType } from '@/types/csmIntegrations';

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
    redeliver: jest.fn(),
  },
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

const api = CsmIntegrationsAPI as jest.Mocked<typeof CsmIntegrationsAPI>;
const EVENTS: WebhookEventType[] = ['ticket.created', 'ticket.status_changed', 'sla.breached'];

const endpoint: WebhookEndpoint = {
  id: 5,
  url: 'https://hooks.example.com/zmarkio',
  description: 'PagerDuty',
  events: ['sla.breached'],
  is_active: true,
  created_by_name: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  last_delivery_status: 'failed',
  last_delivery_at: '2026-10-02T00:00:00Z',
};

const delivery = (overrides: Partial<WebhookDelivery> = {}): WebhookDelivery => ({
  id: 70,
  endpoint: 5,
  event_id: '0b8f6d6e-0000-4000-8000-000000000001',
  event_type: 'sla.breached',
  target_url: endpoint.url,
  attempt: 4,
  status: 'failed',
  response_code: 503,
  error: 'HTTP 503',
  duration_ms: 120,
  next_retry_at: null,
  created_at: '2026-10-02T00:00:00Z',
  payload: { type: 'sla.breached' },
  ...overrides,
});

beforeEach(() => {
  jest.clearAllMocks();
  api.listWebhooks.mockResolvedValue([endpoint]);
  api.listDeliveries.mockResolvedValue({ count: 1, next: null, previous: null, results: [delivery()] });
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
    expect(api.listDeliveries).toHaveBeenCalledWith(1, { page: 1 });
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
    await waitFor(() => expect(api.listDeliveries).toHaveBeenLastCalledWith(1, { page: 1, endpoint: 5 }));
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

  it('filters the log and redelivers a failed attempt', async () => {
    api.redeliver.mockResolvedValue({ data: { event_id: 'x' } } as never);
    await renderPanel();

    fireEvent.change(screen.getByLabelText('Filter by status'), { target: { value: 'failed' } });
    await waitFor(() => expect(api.listDeliveries).toHaveBeenLastCalledWith(1, { page: 1, status: 'failed' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Redeliver delivery 70' }));
    await waitFor(() => expect(api.redeliver).toHaveBeenCalledWith(1, 70));
  });

  it('pages through a long log', async () => {
    api.listDeliveries.mockResolvedValue({
      count: 45, next: 'n', previous: null, results: [delivery({ status: 'succeeded', response_code: 200 })],
    });
    await renderPanel();

    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(api.listDeliveries).toHaveBeenLastCalledWith(1, { page: 2 }));
  });

  it('expands a row to show the error and payload', async () => {
    await renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'Details of delivery 70' }));
    expect(screen.getByText('HTTP 503')).toBeInTheDocument();
    expect(screen.getByText(delivery().event_id)).toBeInTheDocument();
  });
});
