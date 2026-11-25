import { randomBytes, randomUUID } from 'crypto';
import { test as base, expect, type APIRequestContext, type PlaywrightWorkerArgs } from '@playwright/test';
import { enrollMfaAfterLogin, verifyMfaAfterLogin } from './mfa-api';

// MED-326: each test gets its own real org admin, created through the public API
// (same approach as e2e/ads/ads-fixtures.ts).

export type OrgAdmin = {
  email: string;
  password: string;
  // Known once MFA is set up, by the fixture or by the test through the UI.
  secret?: string;
  recoveryCodes: string[];
};
export type EnrolledAdmin = OrgAdmin & { secret: string };

async function withOrgAdmin(
  playwright: PlaywrightWorkerArgs['playwright'],
  baseURL: string,
  enroll: boolean,
  use: (admin: OrgAdmin) => Promise<void>,
) {
  // Provisioning is intentionally limited to a local/test stack.
  if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseURL).hostname)) {
    throw new Error('MFA E2E provisioning requires a loopback BASE_URL. Do not run against production.');
  }
  const id = randomUUID();
  const admin: OrgAdmin = {
    email: `mfa-${id}@example.test`,
    password: `E2e!${randomBytes(24).toString('base64url')}`,
    recoveryCodes: [],
  };
  const guest = await playwright.request.newContext({ baseURL, timeout: 60_000 });
  let organizationSlug: string | undefined;

  try {
    const registered = await guest.post('/auth/register/', {
      data: { email: admin.email, password: admin.password, username: `mfa-${id.slice(0, 12)}` },
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
      const challenge = await (await guest.post('/auth/login/', { data: admin })).json();
      expect(challenge.mfa_enrollment_required, 'A new org admin must set up MFA').toBe(true);
      const enrolled = await enrollMfaAfterLogin(guest, challenge);
      admin.secret = enrolled.secret;
      admin.recoveryCodes = enrolled.recovery_codes;
    }

    await use(admin);
  } finally {
    try {
      if (organizationSlug) await removeTestData(playwright, baseURL, guest, admin, organizationSlug);
    } finally {
      await guest.dispose();
    }
  }
}

// Sign in again (finishing MFA either way) and remove only what this test created.
async function removeTestData(
  playwright: PlaywrightWorkerArgs['playwright'],
  baseURL: string,
  guest: APIRequestContext,
  admin: OrgAdmin,
  organizationSlug: string,
) {
  const challenge = await (await guest.post('/auth/login/', { data: admin })).json();
  const session = challenge.mfa_enrollment_required
    ? await enrollMfaAfterLogin(guest, challenge)
    : await verifyMfaAfterLogin(guest, challenge, admin.secret!);
  const api = await playwright.request.newContext({
    baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${session.token}`,
      ...(session.organization_access_token ? { 'X-Organization-Token': session.organization_access_token } : {}),
    },
  });
  try {
    const deactivated = await api.delete(`/api/core/organizations/${organizationSlug}/?force=true`);
    expect(deactivated.status(), 'Deactivate the test organization').toBe(200);
    const anonymized = await api.delete('/auth/me/delete/', { data: { confirm: 'DELETE MY ACCOUNT' } });
    expect(anonymized.status(), 'Anonymize the test account').toBe(200);
  } finally {
    await api.dispose();
  }
}

export const test = base.extend<{ enrolledAdmin: EnrolledAdmin; newOrgAdmin: OrgAdmin }>({
  // An org admin who has already set up MFA.
  enrolledAdmin: async ({ playwright, baseURL }, use) => {
    await withOrgAdmin(playwright, baseURL!, true, (admin) => use(admin as EnrolledAdmin));
  },
  // An org admin who has not set up MFA yet.
  newOrgAdmin: async ({ playwright, baseURL }, use) => {
    await withOrgAdmin(playwright, baseURL!, false, use);
  },
});

export { expect };
