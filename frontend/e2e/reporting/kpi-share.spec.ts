import { test, expect, type Locator, type Page } from '@playwright/test';
import {
  getActiveProjectSlug,
  waitForWorkspaceReady,
} from '../tasks/tasks-helpers';

/**
 * Share, open, and revoke a Custom KPI link.
 *
 * An already-open public page keeps the numbers it loaded until the next
 * request. Google Ads and Facebook previews work the same way, so this spec
 * reloads after revoke instead of expecting the open tab to clear itself.
 */

const panel = (page: Page) => page.getByTestId('custom-kpi-panel');
const formulaInput = (page: Page) =>
  page.getByTestId('kpi-formula-editor').locator('.cm-content');

const SAFE_FORMULA = 'revenue + spend';

async function gotoOverview(page: Page): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await waitForWorkspaceReady(page);
  await getActiveProjectSlug(page);
  await page.goto('/overview', { waitUntil: 'domcontentloaded' });
  await expect(panel(page)).toBeVisible({ timeout: 30_000 });
}

async function typeFormula(page: Page, formula: string): Promise<void> {
  const editor = formulaInput(page);
  await editor.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('Backspace');
  await editor.pressSequentially(formula, { delay: 20 });
  await page.getByTestId('kpi-name-input').click();
}

async function createKpi(page: Page, name: string): Promise<void> {
  await page.getByTestId('new-kpi-button').click();
  await expect(page.getByTestId('kpi-builder-dialog')).toBeVisible();
  await page.getByTestId('kpi-name-input').fill(name);
  await typeFormula(page, SAFE_FORMULA);
  await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0, {
    timeout: 15_000,
  });
  await page.getByTestId('kpi-save-button').click();
  await expect(page.getByTestId('kpi-builder-dialog')).not.toBeVisible({
    timeout: 15_000,
  });
  await expect(panel(page).locator(`[data-kpi-name="${name}"]`)).toBeVisible({
    timeout: 15_000,
  });
}

async function deleteKpi(page: Page, name: string): Promise<void> {
  const tile = panel(page).locator(`[data-kpi-name="${name}"]`);
  if (!(await tile.isVisible().catch(() => false))) return;
  await tile.getByRole('button', { name: `Delete ${name}` }).click();
  await expect(tile).toHaveCount(0, { timeout: 10_000 });
}

function isShareRequest(response: { url(): string; request(): { method(): string } }, method: string) {
  const url = new URL(response.url());
  return url.pathname.endsWith('/api/report/kpis/share/') && response.request().method() === method;
}

async function openShareDialog(page: Page): Promise<Locator> {
  const loaded = page.waitForResponse((response) => isShareRequest(response, 'GET'));
  await page.getByTestId('share-kpi-button').click();
  expect((await loaded).ok()).toBeTruthy();
  const dialog = page.getByRole('dialog', { name: 'Share Custom KPIs' });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function revokeIfLive(page: Page, dialog: Locator): Promise<void> {
  const revoke = dialog.getByTestId('revoke-share-link');
  if (!(await revoke.isVisible().catch(() => false))) return;
  const deletion = page.waitForResponse((response) => isShareRequest(response, 'DELETE'));
  await revoke.click();
  expect((await deletion).status()).toBe(204);
  await expect(dialog.getByLabel('Share link')).toHaveValue('');
}

test.describe('Custom KPI share link', () => {
  test('generates a link, lets a visitor read the KPI, and hides it after revoke', async ({
    page,
    browser,
  }) => {
    test.setTimeout(120_000);
    const name = `E2E share ${Date.now()}`;
    await gotoOverview(page);
    await createKpi(page, name);

    const visitor = await browser.newContext({ storageState: { cookies: [], origins: [] } });
    try {
      const dialog = await openShareDialog(page);
      await revokeIfLive(page, dialog);

      await dialog.getByRole('radio', { name: '7 days', exact: true }).click();
      const generation = page.waitForResponse((response) => isShareRequest(response, 'POST'));
      await dialog.getByTestId('create-share-link').click();
      const generated = await generation;
      expect(generated.status()).toBe(201);
      const posted = generated.request().postDataJSON() as { project: string; days: number };
      expect(posted.days).toBe(7);
      expect(posted.project).toBeTruthy();
      const body = await generated.json();
      expect(body.reused).toBe(false);

      const link = await dialog.getByLabel('Share link').inputValue();
      expect(link).toContain(`/share/kpis/${body.token}`);
      await expect(dialog.getByRole('button', { name: 'Copy link', exact: true })).toBeEnabled();
      await expect(dialog.getByTestId('create-share-link')).toBeDisabled();

      const publicPage = await visitor.newPage();
      await publicPage.goto(link);
      const tile = publicPage.locator(`[data-kpi-name="${name}"]`);
      await expect(tile).toBeVisible({ timeout: 15_000 });
      await expect(tile).toContainText(SAFE_FORMULA);
      await expect(publicPage.getByRole('button', { name: `Edit ${name}` })).toHaveCount(0);

      const deletion = page.waitForResponse((response) => isShareRequest(response, 'DELETE'));
      await dialog.getByTestId('revoke-share-link').click();
      expect((await deletion).status()).toBe(204);
      await expect(dialog.getByLabel('Share link')).toHaveValue('');

      const revokedRead = publicPage.waitForResponse((response) =>
        /\/api\/report\/share\/[^/]+\/$/.test(new URL(response.url()).pathname),
      );
      await publicPage.reload();
      const revoked = await revokedRead;
      expect(revoked.status()).toBe(404);
      await expect(publicPage.getByTestId('public-kpi-missing')).toBeVisible();
    } finally {
      await visitor.close();
      await page.keyboard.press('Escape');
      await deleteKpi(page, name);
    }
  });
});
