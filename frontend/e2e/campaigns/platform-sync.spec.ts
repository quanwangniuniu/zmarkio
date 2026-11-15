/**
 * MED-242: Playwright coverage for the campaign Meta reconnect warning.
 *
 * Fully mocked (no real login / backend / Meta). Verifies:
 * 1) An auth sync error shows the warning and Reconnect starts the OAuth flow
 * 2) A failed OAuth start keeps the warning
 * 3) A transient sync error shows no warning
 */

import { test, expect, type Page } from '@playwright/test';
import {
  seedAuthenticatedUser,
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  installApiMockSafetyNet,
} from '../messages/messages-helpers';
import type { CampaignSyncError } from '../../src/types/campaign';

const PROJECT = { id: 242, slug: 'sync-project', name: 'Sync project' };
const SLUG = 'campaign-sync-warning';
const RECONNECT_PATH = '/api/facebook_integration/connect/';
const OAUTH_URL =
  'https://www.facebook.com/v23.0/dialog/oauth?client_id=test-app&state=signed-test-state&response_type=code';

test.use({ storageState: { cookies: [], origins: [] } });

async function mockCampaignApis(page: Page, lastSyncError: CampaignSyncError) {
  await page.route('**/api/core/projects/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([PROJECT]),
    });
  });
  await page.route(`**/api/campaigns/${SLUG}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body =
      path === `/api/campaigns/${SLUG}/`
        ? {
            id: 'b241ba5a-5bfa-40e2-b0ab-3266bf79e242',
            slug: SLUG,
            name: 'Campaign sync warning',
            objective: 'CONVERSION',
            platforms: ['META'],
            status: 'TESTING',
            project: PROJECT,
            owner: { id: 1, username: 'e2e-user', email: 'e2e@example.com' },
            start_date: '2026-09-01',
            created_at: '2026-09-01T09:00:00Z',
            updated_at: '2026-09-23T09:00:00Z',
            last_sync_error: lastSyncError,
            last_sync_attempted_at: '2026-09-23T09:00:00Z',
          }
        : [];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
}

async function openCampaign(page: Page, lastSyncError: CampaignSyncError) {
  await mockCampaignApis(page, lastSyncError);
  await page.goto(`/campaigns/${SLUG}`);
  await expect(page.getByText('Campaign sync warning', { exact: true }).first()).toBeVisible();
}

test.describe('Campaign Meta sync warning (MED-242)', () => {
  test.beforeEach(async ({ page }) => {
    // Register safety net first; specific mocks below override it (Playwright
    // checks routes in reverse registration order).
    await installApiMockSafetyNet(page);
    await seedAuthenticatedUser(page);
    await mockAuthenticatedUserApis(page);
    await mockProjectShellApis(page);
    await seedActiveProject(page, PROJECT);
  });

  test('auth sync error shows the warning and Reconnect starts the OAuth flow', async ({ page }) => {
    await openCampaign(page, 'auth');
    await page.route(`**${RECONNECT_PATH}**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ authorize_url: OAUTH_URL, state: 'signed-test-state' }),
      });
    });
    // Intercept the external provider, while exercising the real page and API client.
    await page.route('https://www.facebook.com/**', async (route) => {
      await route.fulfill({ contentType: 'text/html', body: '<h1>Meta authorization</h1>' });
    });

    await expect(page.getByText('Meta authorization expired')).toBeVisible();
    const request = page.waitForRequest((req) => new URL(req.url()).pathname === RECONNECT_PATH);
    await page.getByRole('button', { name: 'Reconnect' }).click();
    expect(new URL((await request).url()).searchParams.get('project_id')).toBe(String(PROJECT.id));
    await expect(page).toHaveURL(OAUTH_URL);
    await expect(page.getByRole('heading', { name: 'Meta authorization' })).toBeVisible();
  });

  test('failed OAuth start keeps the warning', async ({ page }) => {
    await openCampaign(page, 'auth');
    await page.route(`**${RECONNECT_PATH}**`, async (route) => {
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
    });

    await page.getByRole('button', { name: 'Reconnect' }).click();
    await expect(page.getByText('Unable to start reconnection. Please try again.')).toBeVisible();
    await expect(page.getByText('Meta authorization expired')).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/campaigns/${SLUG}$`));
  });

  test('transient sync error shows no warning', async ({ page }) => {
    await openCampaign(page, 'transient');
    await expect(page.getByText('Meta authorization expired')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Reconnect' })).toHaveCount(0);
  });
});
