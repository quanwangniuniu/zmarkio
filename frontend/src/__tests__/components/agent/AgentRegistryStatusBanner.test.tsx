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
    render(<AgentRegistryStatusBanner isOpen={true} />);
    await flush();
    expect(status).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('detects a later collision and clears it after recovery', async () => {
    const { unmount } = render(<AgentRegistryStatusBanner isOpen={true} />);
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
    render(<AgentRegistryStatusBanner isOpen={true} />);
    await flush();
    expect(screen.getByRole('alert')).toHaveTextContent('Agent status is unavailable');
    expect(screen.getByRole('alert')).not.toHaveTextContent('startup is blocked');
  });

  it('only requests diagnostics while the panel is open', async () => {
    const user = { is_staff: true };
    auth.mockImplementation((select) => select({ user }));
    status.mockResolvedValue({ column_registry: { ok: false, error: 'Duplicate sales alias' } });
    const { rerender } = render(<AgentRegistryStatusBanner isOpen={false} />);
    await flush();
    expect(status).not.toHaveBeenCalled();

    rerender(<AgentRegistryStatusBanner isOpen={true} />);
    await flush();
    expect(status).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert')).toHaveTextContent('Duplicate sales alias');

    rerender(<AgentRegistryStatusBanner isOpen={false} />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await act(async () => {
      jest.advanceTimersByTime(60000);
      window.dispatchEvent(new Event('focus'));
    });
    expect(status).toHaveBeenCalledTimes(1);

    status.mockResolvedValue({ column_registry: { ok: true } });
    rerender(<AgentRegistryStatusBanner isOpen={true} />);
    await flush();
    expect(status).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('supports organisation admins and ignores late responses after closing', async () => {
    auth.mockImplementation((select) => select({ user: { is_org_admin: true } }));
    let resolve!: (value: unknown) => void;
    status.mockReturnValue(new Promise((done) => { resolve = done; }));
    const { rerender } = render(<AgentRegistryStatusBanner isOpen={true} />);
    expect(status).toHaveBeenCalledTimes(1);
    rerender(<AgentRegistryStatusBanner isOpen={false} />);
    await act(async () => { resolve({ column_registry: { ok: false, error: 'Late' } }); });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
