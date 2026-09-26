import { act, render, screen } from '@testing-library/react';
import { AgentRegistryStatusBanner } from '@/components/agent/AgentRegistryStatusBanner';
import { AgentAPI } from '@/lib/api/agentApi';
import { useAuthStore } from '@/lib/authStore';

jest.mock('@/lib/authStore', () => ({ useAuthStore: jest.fn() }));
jest.mock('@/lib/api/agentApi', () => ({ AgentAPI: { getConfigStatus: jest.fn() } }));

const status = AgentAPI.getConfigStatus as jest.Mock;
const auth = useAuthStore as unknown as jest.Mock;
const flush = async () => { await act(async () => { await Promise.resolve(); }); };

describe('AgentRegistryStatusBanner', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.clearAllMocks();
    auth.mockImplementation((select) => select({ user: { is_staff: true } }));
    status.mockResolvedValue({ column_registry: { ok: true } });
  });
  afterEach(() => { jest.useRealTimers(); });

  it('does not request diagnostics for non-admins', async () => {
    auth.mockImplementation((select) => select({ user: { is_staff: false } }));
    render(<AgentRegistryStatusBanner />);
    await flush();
    expect(status).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('detects a later collision and clears it after recovery', async () => {
    const { unmount } = render(<AgentRegistryStatusBanner />);
    await flush();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    status.mockResolvedValue({ column_registry: { ok: false, error: 'Duplicate sales alias' } });
    await act(async () => { jest.advanceTimersByTime(30000); });
    expect(screen.getByRole('alert')).toHaveTextContent('Duplicate sales alias');
    status.mockResolvedValue({ column_registry: { ok: true } });
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    unmount();
    const calls = status.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(60000); });
    expect(status).toHaveBeenCalledTimes(calls);
  });

  it('reports an unreachable backend without asserting a collision', async () => {
    status.mockRejectedValue(new Error('Network error'));
    render(<AgentRegistryStatusBanner />);
    await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('Agent status is unavailable');
    expect(screen.getByRole('alert')).not.toHaveTextContent('startup is blocked');
  });

  it('supports organisation admins and ignores late responses after unmount', async () => {
    auth.mockImplementation((select) => select({ user: { is_org_admin: true } }));
    let resolve!: (value: unknown) => void;
    status.mockReturnValue(new Promise((done) => { resolve = done; }));
    const { unmount } = render(<AgentRegistryStatusBanner />);
    expect(status).toHaveBeenCalledTimes(1);
    unmount();
    await act(async () => { resolve({ column_registry: { ok: false, error: 'Late' } }); });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
