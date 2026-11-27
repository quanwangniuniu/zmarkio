import { expect, type APIRequestContext } from '@playwright/test';
import { totp } from './totp';

// MED-326: finish an org admin's sign-in through the real MFA API, using a
// computed TOTP code instead of a phone. Shared by the MFA and budget E2E.

type MfaChallenge = { mfa_required: true; mfa_enrollment_required: boolean; mfa_token: string };

export type MfaSignIn = {
  token: string;
  refresh: string;
  user: Record<string, unknown>;
  organization_access_token?: string | null;
};

export function isMfaChallenge(body: unknown): body is MfaChallenge {
  return Boolean(body && (body as MfaChallenge).mfa_required);
}

export async function enrollMfaAfterLogin(
  request: APIRequestContext,
  challenge: MfaChallenge,
): Promise<MfaSignIn & { secret: string; recovery_codes: string[] }> {
  const started = await request.post('/auth/mfa/enroll/start/', { data: { mfa_token: challenge.mfa_token } });
  expect(started.status(), 'Start MFA setup').toBe(200);
  const { secret } = await started.json();
  const confirmed = await request.post('/auth/mfa/enroll/confirm/', {
    data: { mfa_token: challenge.mfa_token, code: totp(secret) },
  });
  expect(confirmed.status(), 'Confirm MFA setup').toBe(200);
  return { ...(await confirmed.json()), secret };
}

// origin: '' when the request context has a baseURL, otherwise e.g. 'http://localhost'.
export async function verifyMfaAfterLogin(
  request: APIRequestContext,
  challenge: MfaChallenge,
  secret: string,
  origin = '',
): Promise<MfaSignIn> {
  const verified = await request.post(`${origin}/auth/mfa/verify/`, {
    data: { mfa_token: challenge.mfa_token, code: totp(secret) },
  });
  expect(verified.status(), 'Verify the MFA code').toBe(200);
  return verified.json();
}
