import axios from 'axios';
import {
  persistAuthTokens,
  clearPersistedAuthState,
  getValidAccessToken,
  isAccessTokenExpiring,
} from '@/lib/api';

// Unsigned JWT with the given claims; only the payload is read client-side.
function jwt(claims: { iat?: number; exp?: number }): string {
  const b64 = (o: object) =>
    btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${b64({ alg: 'none' })}.${b64(claims)}.sig`;
}

const FIFTEEN_MIN = 15 * 60;
const nowSeconds = () => Math.floor(Date.now() / 1000);

describe('access token expiry with a skewed client clock', () => {
  afterEach(() => {
    clearPersistedAuthState();
    jest.restoreAllMocks();
  });

  it('treats an unreadable token as not expiring', () => {
    expect(isAccessTokenExpiring('not-a-jwt')).toBe(false);
  });

  it('uses the local clock until a refresh reveals the server time', () => {
    const now = nowSeconds();
    expect(isAccessTokenExpiring(jwt({ iat: now, exp: now + FIFTEEN_MIN }))).toBe(false);
    expect(isAccessTokenExpiring(jwt({ iat: now - FIFTEEN_MIN, exp: now }))).toBe(true);
  });

  it('stops refreshing once it learns the client clock is 20 minutes fast', async () => {
    // Server time is 20 minutes behind this browser, so every token it mints
    // already looks expired by the local clock.
    const serverNow = () => nowSeconds() - 20 * 60;
    const serverToken = () => jwt({ iat: serverNow(), exp: serverNow() + FIFTEEN_MIN });

    persistAuthTokens({ token: serverToken(), refreshToken: 'refresh', user: { id: 1 } as any });
    const refreshSpy = jest
      .spyOn(axios, 'post')
      .mockImplementation(async () => ({ data: { access: serverToken() } }) as any);

    // First call: looks expired by the local clock, so it refreshes once...
    const fresh = await getValidAccessToken();
    expect(refreshSpy).toHaveBeenCalledTimes(1);

    // ...and the refreshed token's iat corrects the clock, so it is now fresh
    // and later calls reuse it instead of refreshing again.
    expect(isAccessTokenExpiring(fresh!)).toBe(false);
    await getValidAccessToken();
    await getValidAccessToken();
    expect(refreshSpy).toHaveBeenCalledTimes(1);
  });
});
