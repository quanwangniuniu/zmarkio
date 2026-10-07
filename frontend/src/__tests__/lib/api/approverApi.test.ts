import { approverApi } from '@/lib/api/approverApi';

describe('approverApi', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('fetches users and approvers', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => [{ id: 1 }],
    });

    await expect(approverApi.getAllUsers()).resolves.toEqual([{ id: 1 }]);
    await expect(approverApi.getApprovers('budget')).resolves.toEqual([{ id: 1 }]);
  });

  it('sets and removes approvers', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({ ok: true, json: async () => ({}) });

    await approverApi.setApprovers('budget', [1, 2]);
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/access_control/approvers/budget/',
      expect.objectContaining({ method: 'POST' })
    );

    await approverApi.removeApprover('budget', 1);
    expect(global.fetch).toHaveBeenCalledWith(
      '/api/access_control/approvers/budget/1/',
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('throws when responses are not ok', async () => {
    (global.fetch as jest.Mock).mockResolvedValue({ ok: false });
    await expect(approverApi.getAllUsers()).rejects.toThrow('Failed to fetch users');
    await expect(approverApi.getApprovers('x')).rejects.toThrow('Failed to fetch approvers');
    await expect(approverApi.setApprovers('x', [])).rejects.toThrow('Failed to set approvers');
    await expect(approverApi.removeApprover('x', 1)).rejects.toThrow('Failed to remove approver');
  });
});
