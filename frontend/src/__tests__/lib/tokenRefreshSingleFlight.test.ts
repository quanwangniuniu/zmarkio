import axios from 'axios';
import type { AxiosAdapter, AxiosError, InternalAxiosRequestConfig } from 'axios';
import api, {
  persistAuthTokens,
  clearPersistedAuthState,
  readPersistedAuthState,
  SESSION_ENDED_EVENT,
} from '@/lib/api';
import { api as preferencesApiClient } from '@/lib/api/preferencesApi';

type RetriableRequestConfig = InternalAxiosRequestConfig & { _retry?: boolean };


function reject401(config: RetriableRequestConfig) {
  return Promise.reject({
    response: { status: 401, data: {} },
    config,
  } as AxiosError);
}


function resolve200(config: RetriableRequestConfig) {
  return Promise.resolve({
    data: { ok: true },
    status: 200,
    statusText: 'OK',
    headers: {},
    config,
  });
}

describe('single-flight token refresh', () => {
  const originalApiAdapter = api.defaults.adapter;
  const originalPreferencesAdapter = preferencesApiClient.defaults.adapter;

  beforeEach(() => {
    persistAuthTokens({
      token: 'old-access-token',
      refreshToken: 'valid-refresh-token',
      user: { id: 1, roles: [] } as any,
    });
  });

  afterEach(() => {
    api.defaults.adapter = originalApiAdapter;
    preferencesApiClient.defaults.adapter = originalPreferencesAdapter;
    clearPersistedAuthState();
    jest.restoreAllMocks();
  });

  // Scenario: three requests receive 401 at (effectively) the same time.
  // Without single-flight, each would independently call the refresh
  // endpoint (three calls). This proves they now share one in-flight
  // refresh instead, and all three still get retried successfully.
  it('three concurrent 401s trigger exactly one refresh call, and all requests retry successfully', async () => {
    const mockAdapter = jest
      .fn()
      .mockImplementationOnce(reject401)
      .mockImplementationOnce(reject401)
      .mockImplementationOnce(reject401)
      .mockImplementationOnce(resolve200)
      .mockImplementationOnce(resolve200)
      .mockImplementationOnce(resolve200);
    api.defaults.adapter = mockAdapter as unknown as AxiosAdapter;

    const refreshSpy = jest.spyOn(axios, 'post').mockResolvedValue({
      data: { access: 'new-access-token', refresh: 'new-refresh-token' },
    } as any);

    const results = await Promise.all([
      api.get('/protected/one/'),
      api.get('/protected/two/'),
      api.get('/protected/three/'),
    ]);

    expect(refreshSpy).toHaveBeenCalledTimes(1);
    results.forEach((res) => expect(res.status).toBe(200));
  });

  describe('ending the session', () => {
    const onSessionEnded = jest.fn();

    beforeEach(() => {
      window.addEventListener(SESSION_ENDED_EVENT, onSessionEnded);
      // jsdom can't navigate, so the login redirect logs "Not implemented: navigation".
      jest.spyOn(console, 'error').mockImplementation(() => {});
      jest.spyOn(console, 'warn').mockImplementation(() => {});
    });

    afterEach(() => {
      window.removeEventListener(SESSION_ENDED_EVENT, onSessionEnded);
      onSessionEnded.mockReset();
    });

    // Scenario: the refresh call itself is rejected (e.g. refresh token expired
    // or revoked). All three requests should still only trigger ONE refresh
    // attempt (not three), all three should end up rejected rather than
    // hanging or silently retrying forever, and the dead session is cleared
    // exactly once instead of leaving the user stranded.
    it('when refresh is rejected, refresh is attempted once, all requests reject, and the session ends', async () => {
      const mockAdapter = jest
        .fn()
        .mockImplementationOnce(reject401)
        .mockImplementationOnce(reject401)
        .mockImplementationOnce(reject401);
      api.defaults.adapter = mockAdapter as unknown as AxiosAdapter;

      const refreshSpy = jest.spyOn(axios, 'post').mockRejectedValue({
        response: { status: 401, data: { detail: 'Refresh token invalid' } },
      });

      const results = await Promise.allSettled([
        api.get('/protected/one/'),
        api.get('/protected/two/'),
        api.get('/protected/three/'),
      ]);

      expect(refreshSpy).toHaveBeenCalledTimes(1);
      results.forEach((res) => expect(res.status).toBe('rejected'));
      expect(onSessionEnded).toHaveBeenCalledTimes(1);
      expect(readPersistedAuthState()).toBeNull();
    });

    // Scenario: the refresh endpoint is erroring (5xx) or unreachable. That can
    // recover, so the user must stay signed in.
    it('when refresh fails with a server error, the session is kept', async () => {
      api.defaults.adapter = jest.fn().mockImplementationOnce(reject401) as unknown as AxiosAdapter;
      jest.spyOn(axios, 'post').mockRejectedValue({ response: { status: 503, data: {} } });

      await expect(api.get('/protected/one/')).rejects.toMatchObject({
        response: { status: 401 },
      });

      expect(onSessionEnded).not.toHaveBeenCalled();
      expect(readPersistedAuthState()?.state?.refreshToken).toBe('valid-refresh-token');
    });

    // Scenario: Google Docs reports its own integration token expired. That is
    // not the app session, so it must neither refresh nor log the user out.
    it('a Google Docs integration 401 neither refreshes nor ends the session', async () => {
      api.defaults.adapter = jest.fn().mockImplementationOnce((config: RetriableRequestConfig) =>
        Promise.reject({
          response: { status: 401, data: { error_code: 'google_token_expired' } },
          config,
        } as AxiosError),
      ) as unknown as AxiosAdapter;
      const refreshSpy = jest
        .spyOn(axios, 'post')
        .mockRejectedValue(new Error('refresh should not be called'));

      await expect(api.get('/api/google-docs/documents/')).rejects.toMatchObject({
        response: { status: 401 },
      });

      expect(refreshSpy).not.toHaveBeenCalled();
      expect(onSessionEnded).not.toHaveBeenCalled();
    });
  });

  // Scenario: two DIFFERENT axios instances — api.ts's own instance, and
  // preferencesApi.ts's separate instance — both get a 401 around the same
  // time. Proves the shared refresh promise lives in one place (api.ts) and
  // is genuinely reused across files, not just within a single instance.
  it('a 401 from preferencesApi and a 401 from api share the same single refresh call', async () => {
    const mockApiAdapter = jest
      .fn()
      .mockImplementationOnce(reject401)
      .mockImplementationOnce(resolve200);
    api.defaults.adapter = mockApiAdapter as unknown as AxiosAdapter;

    const mockPreferencesAdapter = jest
      .fn()
      .mockImplementationOnce(reject401)
      .mockImplementationOnce(resolve200);
    preferencesApiClient.defaults.adapter = mockPreferencesAdapter as unknown as AxiosAdapter;

    const refreshSpy = jest.spyOn(axios, 'post').mockResolvedValue({
      data: { access: 'new-access-token', refresh: 'new-refresh-token' },
    } as any);

    const results = await Promise.all([
      api.get('/protected/one/'),
      preferencesApiClient.get('/users/me/preferences/'),
    ]);

    expect(refreshSpy).toHaveBeenCalledTimes(1);
    results.forEach((res) => expect(res.status).toBe(200));
  });
});
