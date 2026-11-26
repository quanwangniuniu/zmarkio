import { randomBytes, randomUUID } from 'crypto';
import { test as base, expect, type PlaywrightWorkerArgs } from '@playwright/test';
import { enrollMfaAfterLogin, verifyMfaAfterLogin } from './mfa-api';

// secret is set by the fixture (enrolledAdmin) or by the test after enrolling in the UI (newOrgAdmin).
export type OrgAdmin = { email: string; password: string; secret: string; recoveryCodes: string[] };

// MED-326: each test gets its own real org admin, created through the public API,
// and everything it created is removed afterwards.
async function withOrgAdmin(
  playwright: PlaywrightWorkerArgs['playwright'],
  baseURL: string,
  enroll: boolean,
  use: (admin: OrgAdmin) => Promise<void>,
) {
  if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseURL).hostname)) {
    throw new Error('MFA E2E provisioning requires a loopback BASE_URL. Do not run against production.');
  }
  const id = randomUUID();
  const admin: OrgAdmin = {
    email: `mfa-${id}@example.test`,
    password: `E2e!${randomBytes(24).toString('base64url')}`,
    secret: '',
    recoveryCodes: [],
  };
  const credentials = { email: admin.email, password: admin.password };
  const guest = await playwright.request.newContext({ baseURL, timeout: 60_000 });
  let organizationSlug: string | undefined;

  try {
    const registered = await guest.post('/auth/register/', {
      data: { ...credentials, username: `mfa-${id.slice(0, 12)}` },
    });
    expect(registered.status(), 'Register the test user').toBe(201);
    const member = await playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: { Authorization: `Bearer ${(await registered.json()).token}` },
    });
    // Creating an organization makes the user its org admin.
    const createdOrg = await member.post('/api/core/organizations/create/', { data: { name: `MFA E2E ${id}` } });
    expect(createdOrg.status(), 'Create the test organization').toBe(201);
    organizationSlug = (await createdOrg.json()).organization.slug;
    await member.dispose();

    if (enroll) {
      const enrolled = await enrollMfaAfterLogin(guest, await (await guest.post('/auth/login/', { data: credentials })).json());
      admin.secret = enrolled.secret;
      admin.recoveryCodes = enrolled.recovery_codes;
    }

    await use(admin);
  } finally {
    if (organizationSlug) {
      const challenge = await (await guest.post('/auth/login/', { data: credentials })).json();
      const session = challenge.mfa_enrollment_required
        ? await enrollMfaAfterLogin(guest, challenge)
        : await verifyMfaAfterLogin(guest, challenge, admin.secret);
      const api = await playwright.request.newContext({
        baseURL,
        extraHTTPHeaders: {
          Authorization: `Bearer ${session.token}`,
          ...(session.organization_access_token ? { 'X-Organization-Token': session.organization_access_token } : {}),
        },
      });
      expect((await api.delete(`/api/core/organizations/${organizationSlug}/?force=true`)).status()).toBe(200);
      expect((await api.delete('/auth/me/delete/', { data: { confirm: 'DELETE MY ACCOUNT' } })).status()).toBe(200);
      await api.dispose();
    }
    await guest.dispose();
  }
}

export const test = base.extend<{ enrolledAdmin: OrgAdmin; newOrgAdmin: OrgAdmin }>({
  enrolledAdmin: async ({ playwright, baseURL }, use) => withOrgAdmin(playwright, baseURL!, true, use),
  newOrgAdmin: async ({ playwright, baseURL }, use) => withOrgAdmin(playwright, baseURL!, false, use),
});

export { expect };
