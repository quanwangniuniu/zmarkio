import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import toast from 'react-hot-toast';
import CredentialsPanel from '@/components/csm-settings/integrations/CredentialsPanel';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { ApiKey } from '@/types/csmIntegrations';

jest.mock('@/lib/api/csmIntegrationsApi', () => ({
  __esModule: true,
  CsmIntegrationsAPI: {
    listApiKeys: jest.fn(),
    createApiKey: jest.fn(),
    revokeApiKey: jest.fn(),
    listOAuthClients: jest.fn(),
    createOAuthClient: jest.fn(),
    revokeOAuthClient: jest.fn(),
  },
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { success: jest.fn(), error: jest.fn() },
}));

const api = CsmIntegrationsAPI as jest.Mocked<typeof CsmIntegrationsAPI>;
const RESOURCES = [
  { value: 'tickets', label: 'Tickets' },
  { value: 'queues', label: 'Queues' },
];

const key: ApiKey = {
  id: 3,
  name: 'Zapier',
  display_key: 'zmk_abcd1234_…',
  scopes: ['tickets:read', 'tickets:write', 'queues:read'],
  is_active: true,
  created_by_name: 'Ada Admin',
  created_at: '2026-10-01T00:00:00Z',
  last_used_at: null,
  revoked_at: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  api.listApiKeys.mockResolvedValue([key]);
});

const renderPanel = async () => {
  render(<CredentialsPanel kind="api-key" projectId={1} resources={RESOURCES} />);
  await screen.findByText('Zapier');
};

describe('CredentialsPanel (API keys)', () => {
  it('lists keys with their masked key and a scope summary, never the secret', async () => {
    await renderPanel();

    expect(api.listApiKeys).toHaveBeenCalledWith(1);
    expect(screen.getByText('zmk_abcd1234_…')).toBeInTheDocument();
    expect(screen.getByText('Tickets (write), Queues (read)')).toBeInTheDocument();
  });

  it('requires a name and at least one permission before creating', async () => {
    await renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /New API key/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByText('Name is required.')).toBeInTheDocument();
    expect(screen.getByText('Choose at least one permission.')).toBeInTheDocument();
    expect(api.createApiKey).not.toHaveBeenCalled();
  });

  it('ticking write also grants and locks read', async () => {
    await renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /New API key/ }));

    fireEvent.click(screen.getByLabelText('Tickets write'));

    expect(screen.getByLabelText('Tickets read')).toBeChecked();
    expect(screen.getByLabelText('Tickets read')).toBeDisabled();
  });

  it('shows the new key once, then only the masked form', async () => {
    api.createApiKey.mockResolvedValue({ ...key, id: 4, name: 'CRM', key: 'zmk_ffff0000_s3cret' });
    await renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /New API key/ }));
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'CRM' } });
    fireEvent.click(screen.getByLabelText('Queues read'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByTestId('revealed-API key')).toHaveTextContent('zmk_ffff0000_s3cret');
    expect(api.createApiKey).toHaveBeenCalledWith(1, { name: 'CRM', scopes: ['queues:read'] });

    fireEvent.click(screen.getByRole('button', { name: /stored it/ }));
    await waitFor(() => expect(screen.queryByText('zmk_ffff0000_s3cret')).not.toBeInTheDocument());
    expect(screen.getByText('CRM')).toBeInTheDocument();
  });

  it('shows server field errors from a 400', async () => {
    api.createApiKey.mockRejectedValue({ response: { data: { name: ['Too long.'] } } });
    await renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /New API key/ }));
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'x' } });
    fireEvent.click(screen.getByLabelText('Tickets read'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByText('Too long.')).toBeInTheDocument();
  });

  it('revokes through the confirm dialog, not a native confirm', async () => {
    const confirmSpy = jest.spyOn(window, 'confirm');
    api.revokeApiKey.mockResolvedValue({ ...key, is_active: false, revoked_at: '2026-10-02T00:00:00Z' });
    await renderPanel();

    fireEvent.click(screen.getByRole('button', { name: 'Revoke Zapier' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Revoke' }));

    await waitFor(() => expect(api.revokeApiKey).toHaveBeenCalledWith(1, 3));
    expect(await screen.findByText('Revoked')).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith('API key revoked.');
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});

describe('CredentialsPanel (OAuth clients)', () => {
  it('reveals client id and secret on create', async () => {
    api.listOAuthClients.mockResolvedValue([]);
    api.createOAuthClient.mockResolvedValue({
      id: 9, name: 'CRM', client_id: 'cid-123', client_secret: 'sec-456', scopes: ['tickets:read'],
      is_active: true, created_by_name: null, created_at: '2026-10-01T00:00:00Z', revoked_at: null,
    });
    render(<CredentialsPanel kind="oauth-client" projectId={1} resources={RESOURCES} />);
    await screen.findByText('No OAuth clients yet.');

    fireEvent.click(screen.getByRole('button', { name: /New OAuth client/ }));
    fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'CRM' } });
    fireEvent.click(screen.getByLabelText('Tickets read'));
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByTestId('revealed-Client ID')).toHaveTextContent('cid-123');
    expect(screen.getByTestId('revealed-Client secret')).toHaveTextContent('sec-456');
  });
});
