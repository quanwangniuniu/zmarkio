import { expect, test, type Page } from '@playwright/test';
import {
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  seedAuthenticatedUser,
} from '../messages/messages-helpers';
import type { CampaignPlatformIntegration } from '../../src/types/campaign';

const PROJECT = { id: 242, slug: 'sync-project', name: 'Sync project' };
const SLUG = 'campaign-sync-warning';
const INTEGRATION: CampaignPlatformIntegration = {
  id: 42,
  platform: 'META',
  account_name: 'Campaign ad account',
  connector_name: 'e2e-user',
  can_reconnect: true,
  last_sync_error: 'auth',
  last_sync_attempted_at: '2026-09-23T09:00:00Z',
  last_synced_at: '2026-09-21T09:00:00Z',
};
const RECONNECT_PATH = `/api/campaigns/${SLUG}/platform-integrations/42/reconnect/`;
const OAUTH_URL = 'https://www.facebook.com/v23.0/dialog/oauth?client_id=test-app&state=signed-test-state&response_type=code';

test.use({ storageState: { cookies: [], origins: [] } });

async function openCampaign(page: Page, overrides: Partial<CampaignPlatformIntegration> = {}) {
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
        platform_integrations: [{ ...INTEGRATION, ...overrides }],
      } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto(`/campaigns/${SLUG}`);
  await expect(page.getByText('Campaign sync warning', { exact: true }).first()).toBeVisible();
}

test('auth banner starts the provider OAuth flow', async ({ page }) => {
  await openCampaign(page);
  await page.route(`**${RECONNECT_PATH}`, (route) => route.fulfill({
    json: { authorize_url: OAUTH_URL, state: 'signed-test-state' },
  }));
  // Intercept the external provider, while exercising the real page and API client.
  await page.route('https://www.facebook.com/**', (route) => route.fulfill({
    contentType: 'text/html', body: '<h1>Meta authorization</h1>',
  }));
  await expect(page.getByLabel('Campaign sync warnings').getByRole('alert')).toContainText('Authorization has expired or been revoked');
  await expect(page.getByLabel('Campaign sync warnings').getByRole('alert')).toContainText('Last successful sync:');
  const request = page.waitForRequest((req) => new URL(req.url()).pathname === RECONNECT_PATH);
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  expect((await request).method()).toBe('POST');
  await expect(page).toHaveURL(OAUTH_URL);
  await expect(page.getByRole('heading', { name: 'Meta authorization' })).toBeVisible();
});

test('failed OAuth initiation keeps the warning and allows another attempt', async ({ page }) => {
  await openCampaign(page);
  await page.route(`**${RECONNECT_PATH}`, (route) => route.fulfill({ status: 503, json: {} }));
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect(page.getByLabel('Campaign sync warnings').getByRole('alert')).toContainText('Unable to start reconnection');
  await expect(page.getByRole('button', { name: 'Reconnect', exact: true })).toBeEnabled();
  await expect(page).toHaveURL(new RegExp(`/campaigns/${SLUG}$`));
});

test('transient sync failure shows a retry warning without a reconnect action', async ({ page }) => {
  await openCampaign(page, { last_sync_error: 'transient' });
  await expect(page.getByLabel('Campaign sync warnings').getByRole('alert')).toContainText('temporary problem');
  await expect(page.getByRole('button', { name: 'Reconnect', exact: true })).toHaveCount(0);
});

test('healthy integration has no sync banner', async ({ page }) => {
  await openCampaign(page, { last_sync_error: '' });
  await expect(page.getByLabel('Campaign sync warnings')).toHaveCount(0);
});

test('a teammate is directed to the original connector', async ({ page }) => {
  await openCampaign(page, { can_reconnect: false, connector_name: 'account-owner' });
  await expect(page.getByLabel('Campaign sync warnings').getByRole('alert')).toContainText('Ask account-owner to reconnect');
  await expect(page.getByRole('button', { name: 'Reconnect', exact: true })).toHaveCount(0);
});
