import { randomBytes, randomUUID } from 'crypto';
import { test as base, expect, type APIRequestContext } from '@playwright/test';
import { totp } from './totp';

// MED-326: each test gets its own real org admin with MFA already set up,
// created through the public API (same approach as e2e/ads/ads-fixtures.ts).

export type EnrolledAdmin = {
  email: string;
  password: string;
  secret: string;
  recoveryCodes: string[];
};

export const test = base.extend<{ enrolledAdmin: EnrolledAdmin }>({
  enrolledAdmin: async ({ playwright, baseURL }, use) => {
    // Provisioning is intentionally limited to a local/test stack.
    if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseURL!).hostname)) {
      throw new Error('MFA E2E provisioning requires a loopback BASE_URL. Do not run against production.');
    }
    const id = randomUUID();
    const email = `mfa-${id}@example.test`;
    const password = `E2e!${randomBytes(24).toString('base64url')}`;
    const guest = await playwright.request.newContext({ baseURL, timeout: 60_000 });
    let admin: APIRequestContext | null = null;
    const cleanup: Array<() => Promise<void>> = [];

    try {
      const registered = await guest.post('/auth/register/', {
        data: { email, password, username: `mfa-${id.slice(0, 12)}` },
      });
      expect(registered.status(), 'Register the test user').toBe(201);
      const member = await playwright.request.newContext({
        baseURL,
        extraHTTPHeaders: { Authorization: `Bearer ${(await registered.json()).token}` },
      });
      // Creating an organization makes the user its org admin.
      const createdOrg = await member.post('/api/core/organizations/create/', { data: { name: `MFA E2E ${id}` } });
      expect(createdOrg.status(), 'Create the test organization').toBe(201);
      const { organization } = await createdOrg.json();
      await member.dispose();

      const loggedIn = await guest.post('/auth/login/', { data: { email, password } });
      const challenge = await loggedIn.json();
      expect(challenge.mfa_enrollment_required, 'A new org admin must set up MFA').toBe(true);

      const started = await guest.post('/auth/mfa/enroll/start/', { data: { mfa_token: challenge.mfa_token } });
      expect(started.status(), 'Start MFA setup').toBe(200);
      const { secret } = await started.json();
      const confirmed = await guest.post('/auth/mfa/enroll/confirm/', {
        data: { mfa_token: challenge.mfa_token, code: totp(secret) },
      });
      expect(confirmed.status(), 'Confirm MFA setup').toBe(200);
      const enrolled = await confirmed.json();

      admin = await playwright.request.newContext({
        baseURL,
        extraHTTPHeaders: {
          Authorization: `Bearer ${enrolled.token}`,
          ...(enrolled.organization_access_token ? { 'X-Organization-Token': enrolled.organization_access_token } : {}),
        },
      });
      const adminApi = admin;
      cleanup.push(async () => {
        const response = await adminApi.delete('/auth/me/delete/', { data: { confirm: 'DELETE MY ACCOUNT' } });
        expect(response.status(), 'Anonymize the test account').toBe(200);
      });
      cleanup.push(async () => {
        const response = await adminApi.delete(`/api/core/organizations/${organization.slug}/?force=true`);
        expect(response.status(), 'Deactivate the test organization').toBe(200);
      });

      await use({ email, password, secret, recoveryCodes: enrolled.recovery_codes });
    } finally {
      // Attempt every cleanup even when an earlier one fails. Never touch pre-existing records.
      const errors: unknown[] = [];
      for (const remove of cleanup.reverse()) {
        try { await remove(); } catch (error) { errors.push(error); }
      }
      if (admin) await admin.dispose();
      await guest.dispose();
      if (errors.length) throw new Error(`MFA E2E cleanup failed:\n${errors.map(String).join('\n')}`);
    }
  },
});

export { expect };
