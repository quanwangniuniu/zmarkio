import { expect, test, type Page } from '@playwright/test';
import {
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  seedAuthenticatedUser,
} from '../messages/messages-helpers';
import type { CampaignData } from '../../src/types/campaign';

const PROJECT = { id: 242, slug: 'sync-project', name: 'Sync project' };
const SLUG = 'campaign-sync-warning';
const RECONNECT_PATH = '/api/facebook_integration/connect/';
const OAUTH_URL = 'https://www.facebook.com/v23.0/dialog/oauth?client_id=test-app&state=signed-test-state&response_type=code';

test.use({ storageState: { cookies: [], origins: [] } });

async function openCampaign(page: Page, lastSyncError: CampaignData['last_sync_error'] = 'auth') {
  await seedAuthenticatedUser(page);
  await seedActiveProject(page, PROJECT);
  // Keep shell traffic isolated from any locally running backend.
  await page.route('**/api/**', (route) => route.fulfill({ json: { results: [], count: 0 } }));
  await mockAuthenticatedUserApis(page);
  await mockProjectShellApis(page);
  await page.route('**/api/stripe/quota-preview/**', (route) => route.fulfill({
    json: { project_name: PROJECT.name, tokens_used: 0, monthly_token_quota: null },
  }));
  await page.route('**/api/core/projects/**', (route) => route.fulfill({ json: [PROJECT] }));
  await page.route(`**/api/campaigns/${SLUG}/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/campaigns/${SLUG}/`) {
      return route.fulfill({ json: {
        id: 'b241ba5a-5bfa-40e2-b0ab-3266bf79e242', slug: SLUG,
        name: 'Campaign sync warning', objective: 'CONVERSION', platforms: ['META'],
        status: 'TESTING', project: PROJECT,
        owner: { id: 1, username: 'e2e-user', email: 'e2e@example.com' },
        start_date: '2026-09-01', created_at: '2026-09-01T09:00:00Z',
        updated_at: '2026-09-23T09:00:00Z',
        last_sync_error: lastSyncError, last_sync_attempted_at: '2026-09-23T09:00:00Z',
      } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto(`/campaigns/${SLUG}`);
  await expect(page.getByText('Campaign sync warning', { exact: true }).first()).toBeVisible();
}

const warning = (page: Page) => page.getByRole('alert').filter({ hasText: 'Meta authorization expired' });

test('auth warning starts the provider OAuth flow', async ({ page }) => {
  await openCampaign(page);
  await page.route(`**${RECONNECT_PATH}**`, (route) => route.fulfill({
    json: { authorize_url: OAUTH_URL, state: 'signed-test-state' },
  }));
  // Intercept the external provider, while exercising the real page and API client.
  await page.route('https://www.facebook.com/**', (route) => route.fulfill({
    contentType: 'text/html', body: '<h1>Meta authorization</h1>',
  }));
  await expect(warning(page)).toBeVisible();
  const request = page.waitForRequest((req) => new URL(req.url()).pathname === RECONNECT_PATH);
  await warning(page).getByRole('button', { name: 'Reconnect' }).click();
  expect(new URL((await request).url()).searchParams.get('project_id')).toBe(String(PROJECT.id));
  await expect(page).toHaveURL(OAUTH_URL);
  await expect(page.getByRole('heading', { name: 'Meta authorization' })).toBeVisible();
});

test('failed OAuth initiation keeps the warning', async ({ page }) => {
  await openCampaign(page);
  await page.route(`**${RECONNECT_PATH}**`, (route) => route.fulfill({ status: 503, json: {} }));
  await warning(page).getByRole('button', { name: 'Reconnect' }).click();
  await expect(page.getByText('Unable to start reconnection. Please try again.')).toBeVisible();
  await expect(warning(page)).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/campaigns/${SLUG}$`));
});

for (const lastSyncError of ['transient', ''] as const) {
  test(`no reconnect warning when last_sync_error is '${lastSyncError}'`, async ({ page }) => {
    await openCampaign(page, lastSyncError);
    await expect(warning(page)).toHaveCount(0);
  });
}
