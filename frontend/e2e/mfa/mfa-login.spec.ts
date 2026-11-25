import type { Page } from '@playwright/test';
import { test, expect } from './mfa-fixtures';
import { totp } from './totp';

// MED-326: real backend, no mocked routes.
// An org-admin-only page and the admin API it loads.
const ADMIN_PAGE = '/admin/override-audits';
const ADMIN_API = '/api/access_control/admin-override-audits/';

test.use({ storageState: { cookies: [], origins: [] } });

async function signInWithPassword(page: Page, email: string, password: string) {
  await page.goto('/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
}

// Before MFA, the admin page is out of reach. This page sends anyone not
// signed in as an admin to /unauthorized (its ProtectedRoute fallback).
async function expectAdminPageBlocked(page: Page) {
  await page.goto(ADMIN_PAGE);
  await expect(page).toHaveURL(/\/unauthorized/);
  await expect(page.getByRole('heading', { name: 'Override Audit Log' })).toHaveCount(0);
}

// After MFA, the admin page and its admin-only API both work.
async function expectAdminPageOpens(page: Page) {
  const adminApiResponse = page.waitForResponse((response) => response.url().includes(ADMIN_API));
  await page.goto(ADMIN_PAGE);
  await expect(page.getByRole('heading', { name: 'Override Audit Log' })).toBeVisible();
  expect((await adminApiResponse).status()).toBe(200);
}

test('enrolled org admin signs in with an authenticator code', async ({ page, enrolledAdmin }) => {
  await signInWithPassword(page, enrolledAdmin.email, enrolledAdmin.password);

  // The correct password alone only leads to the MFA step.
  await expect(page.getByRole('heading', { name: 'Two-step verification' })).toBeVisible();
  await expect(page.getByLabel('Verification code')).toBeVisible();
  await expectAdminPageBlocked(page);

  // With the code from the authenticator app, sign-in completes.
  await signInWithPassword(page, enrolledAdmin.email, enrolledAdmin.password);
  await page.getByLabel('Verification code').fill(totp(enrolledAdmin.secret));
  await page.getByRole('button', { name: 'Verify' }).click();
  await expect(page).toHaveURL(/\/overview/);
  await expectAdminPageOpens(page);
});

test('org admin signs in with a recovery code, which works only once', async ({ page, browser, enrolledAdmin }) => {
  const [recoveryCode] = enrolledAdmin.recoveryCodes;

  await signInWithPassword(page, enrolledAdmin.email, enrolledAdmin.password);
  await expect(page.getByRole('heading', { name: 'Two-step verification' })).toBeVisible();
  await expectAdminPageBlocked(page);

  // No phone: switch to a recovery code.
  await signInWithPassword(page, enrolledAdmin.email, enrolledAdmin.password);
  await page.getByRole('button', { name: 'Use a recovery code' }).click();
  await page.getByLabel('Recovery code').fill(recoveryCode);
  await page.getByRole('button', { name: 'Verify' }).click();
  await expect(page).toHaveURL(/\/overview/);
  await expectAdminPageOpens(page);

  // A new sign-in session cannot use the same recovery code again.
  const secondSession = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const secondPage = await secondSession.newPage();
  await signInWithPassword(secondPage, enrolledAdmin.email, enrolledAdmin.password);
  await secondPage.getByRole('button', { name: 'Use a recovery code' }).click();
  await secondPage.getByLabel('Recovery code').fill(recoveryCode);
  await secondPage.getByRole('button', { name: 'Verify' }).click();
  await expect(secondPage.getByText('That recovery code is not valid or has already been used.')).toBeVisible();
  await expect(secondPage.getByRole('heading', { name: 'Two-step verification' })).toBeVisible();
  await expectAdminPageBlocked(secondPage);
  await secondSession.close();
});
