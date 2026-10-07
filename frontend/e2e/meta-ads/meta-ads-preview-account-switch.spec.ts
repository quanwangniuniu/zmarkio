/**
 * switching Meta ad accounts must not leave a stale creative preview.
 *
 * Fully mocked (no real Meta token / Graph). Verifies:
 * 1) Preview for account A shows account-A marker text
 * 2) After switching to account B, reopening preview shows B (not A)
 *
 * Note: VideoModal is a full-screen overlay, so a real user closes it before
 * using AccountPicker. The parent still clears `previewCreativeId` on
 * `adAccountId` change; this E2E covers the user-visible stale-preview bug.
 */

import { test, expect } from '@playwright/test';

import {
  seedAuthenticatedUser,
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  waitForLayoutMain,
  installApiMockSafetyNet,
} from '../messages/messages-helpers';
import {
  ACCOUNT_A,
  ACCOUNT_B,
  E2E_USER,
  PREVIEW_AD_NAME_A,
  PREVIEW_AD_NAME_B,
  PROJECT,
  metaAdsPagePath,
  mockMetaAdsPreviewApis,
  seedSelectedAdAccount,
} from './meta-ads-helpers';

test.use({ storageState: { cookies: [], origins: [] } });

test.describe('Meta Ads creative preview account switch (MED-248)', () => {
  test.beforeEach(async ({ page }) => {
    // Register safety net first; specific mocks below override it (Playwright
    // checks routes in reverse registration order).
    await installApiMockSafetyNet(page);
    await seedAuthenticatedUser(page, E2E_USER);
    await mockAuthenticatedUserApis(page, E2E_USER);
    await mockProjectShellApis(page);
    await seedActiveProject(page, PROJECT);
    await seedSelectedAdAccount(page, ACCOUNT_A.id);
    await mockMetaAdsPreviewApis(page);
  });

  test('reopens correct preview after switching ad accounts', async ({ page }) => {
    await page.goto(metaAdsPagePath());
    await waitForLayoutMain(page);

    await expect(page.getByRole('heading', { name: 'Meta Ads' })).toBeVisible({
      timeout: 20_000,
    });

    const creativesTab = page.getByRole('button', { name: /Creatives/i });
    if (await creativesTab.isVisible().catch(() => false)) {
      await creativesTab.click();
    }

    await expect(page.getByText(/Creative performance/i)).toBeVisible({
      timeout: 15_000,
    });

    const accountTrigger = page.getByRole('button', {
      name: new RegExp(ACCOUNT_A.name),
    });
    await expect(accountTrigger).toBeVisible();

    // --- Account A preview ---
    await page.getByRole('button', { name: 'Preview creative' }).click();
    await expect(page.getByText('Creative preview')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole('heading', { name: PREVIEW_AD_NAME_A })).toBeVisible();

    // Full-screen modal blocks AccountPicker; close first (real UX).
    await page.getByLabel('Close preview').click();
    await expect(page.getByText('Creative preview')).toHaveCount(0);

    // --- Switch to Account B ---
    await accountTrigger.click();
    await page.getByRole('option', { name: new RegExp(ACCOUNT_B.name) }).click();
    await expect(
      page.getByRole('button', { name: new RegExp(ACCOUNT_B.name) })
    ).toBeVisible({ timeout: 10_000 });

    await expect(page.getByText(/Creative performance/i)).toBeVisible({
      timeout: 15_000,
    });
    // No leftover account-A preview chrome after the switch.
    await expect(page.getByRole('heading', { name: PREVIEW_AD_NAME_A })).toHaveCount(0);
    await expect(page.getByLabel('Close preview')).toHaveCount(0);

    // --- Account B preview must not show A markers ---
    await page.getByRole('button', { name: 'Preview creative' }).click();
    await expect(page.getByText('Creative preview')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole('heading', { name: PREVIEW_AD_NAME_B })).toBeVisible();
    await expect(page.getByRole('heading', { name: PREVIEW_AD_NAME_A })).toHaveCount(0);
  });
});
