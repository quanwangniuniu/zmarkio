import { clearPortalAuth, getPortalUser, initPortalAuth } from '@/lib/portalAuth';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: {
    post: jest.fn(),
    defaults: { headers: { common: {} as Record<string, string> } },
  },
}));

import api from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

function makeJwt(expSecondsFromNow: number): string {
  const header = btoa(JSON.stringify({ alg: 'none' }));
  const payload = btoa(
    JSON.stringify({ exp: Math.floor(Date.now() / 1000) + expSecondsFromNow })
  );
  return `${header}.${payload}.sig`;
}

describe('portalAuth', () => {
  beforeEach(() => {
    localStorage.clear();
    mockApi.post.mockReset();
    api.defaults.headers.common = {};
  });

  it('returns null when nothing is stored', async () => {
    await expect(initPortalAuth()).resolves.toBeNull();
    expect(getPortalUser()).toBeNull();
  });

  it('uses a still-valid token', async () => {
    const token = makeJwt(3600);
    localStorage.setItem(
      'portal-auth',
      JSON.stringify({
        state: {
          token,
          refresh: 'r',
          user: { id: 1, email: 'a@b.com' },
          isAuthenticated: true,
        },
      })
    );

    await expect(initPortalAuth()).resolves.toBe(token);
    expect(api.defaults.headers.common.Authorization).toBe(`Bearer ${token}`);
    expect(getPortalUser()).toEqual({ id: 1, email: 'a@b.com' });
  });

  it('refreshes expired tokens and clears on failure', async () => {
    const expired = makeJwt(-100);
    localStorage.setItem(
      'portal-auth',
      JSON.stringify({
        state: {
          token: expired,
          refresh: 'refresh-token',
          user: { id: 1, email: 'a@b.com' },
          isAuthenticated: true,
        },
      })
    );

    mockApi.post.mockResolvedValue({ data: { access: 'new-access' } } as any);
    await expect(initPortalAuth()).resolves.toBe('new-access');
    expect(mockApi.post).toHaveBeenCalledWith('/api/token/refresh/', {
      refresh: 'refresh-token',
    });

    localStorage.setItem(
      'portal-auth',
      JSON.stringify({
        state: {
          token: expired,
          refresh: 'bad',
          user: { id: 1, email: 'a@b.com' },
          isAuthenticated: true,
        },
      })
    );
    mockApi.post.mockRejectedValue(new Error('nope'));
    await expect(initPortalAuth()).resolves.toBeNull();
    expect(localStorage.getItem('portal-auth')).toBeNull();
  });

  it('clears auth state', () => {
    localStorage.setItem('portal-auth', '{"state":{}}');
    api.defaults.headers.common.Authorization = 'Bearer x';
    clearPortalAuth();
    expect(localStorage.getItem('portal-auth')).toBeNull();
    expect(api.defaults.headers.common.Authorization).toBeUndefined();
  });
});
