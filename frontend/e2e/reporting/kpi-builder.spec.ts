import { test, expect, type Page } from '@playwright/test';
import {
  getActiveProjectSlug,
  waitForWorkspaceReady,
} from '../tasks/tasks-helpers';

/** Unique per run so a leaked KPI from a previous run cannot collide. */
const kpiName = () => `E2E ROAS ${Date.now()}`;

const panel = (page: Page) => page.getByTestId('custom-kpi-panel');
const formulaInput = (page: Page) =>
  page.getByTestId('kpi-formula-editor').locator('.cm-content');

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
  // Move focus to another field so the autocomplete popup closes and cannot
  // overlay the Save button. Escape is not usable here: it closes the whole
  // dialog, not just the popup.
  await page.getByTestId('kpi-name-input').click();
}

/** Formulas whose validity never depends on warehouse rows being present. */
const SAFE_FORMULA = 'revenue + spend';
const OTHER_SAFE_FORMULA = 'clicks + impressions';

async function openBuilder(page: Page): Promise<void> {
  await page.getByTestId('new-kpi-button').click();
  await expect(page.getByTestId('kpi-builder-dialog')).toBeVisible();
}

async function deleteKPI(page: Page, name: string): Promise<void> {
  const tile = panel(page).locator(`[data-kpi-name="${name}"]`);
  if (!(await tile.isVisible().catch(() => false))) return;
  await tile.getByRole('button', { name: `Delete ${name}` }).click();
  await expect(tile).toHaveCount(0, { timeout: 10_000 });
}

test.describe('Custom KPI builder', () => {
  test.describe.configure({ mode: 'serial' });

  let createdName: string | null = null;

  test.beforeEach(async ({ page }) => {
    createdName = null;
    await gotoOverview(page);
  });

  test.afterEach(async ({ page }) => {
    if (!createdName) return;
    try {
      await deleteKPI(page, createdName);
    } catch {
      /* best-effort cleanup */
    }
    createdName = null;
  });

  test('an unknown metric is reported inline while typing', async ({ page }) => {
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(kpiName());
    await typeFormula(page, 'revenue / mystery');

    const error = page.getByTestId('kpi-formula-error');
    await expect(error).toBeVisible({ timeout: 15_000 });
    await expect(error).toContainText('mystery');

    // An unsaveable formula must not produce a preview value.
    await expect(page.getByTestId('kpi-preview-value')).toContainText('No value');
  });

  test('a malformed formula is reported inline, not as a failed request', async ({
    page,
  }) => {
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(kpiName());
    await typeFormula(page, 'revenue /');

    await expect(page.getByTestId('kpi-formula-error')).toBeVisible({
      timeout: 15_000,
    });
  });

  test('a project with no warehouse rows says so instead of failing', async ({
    page,
  }) => {
    // The E2E project has no Meta insight rows, so every metric aggregates to
    // zero. A ratio must report that as missing data, not as a division by
    // zero the author could somehow fix.
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(kpiName());
    await typeFormula(page, 'revenue / spend');

    await expect(page.getByTestId('kpi-preview-no-data')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByTestId('kpi-preview-no-data')).toContainText(
      'No data for this period.'
    );
    // And it is not presented as a formula fault, so the KPI stays saveable.
    await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0);
    await expect(page.getByTestId('kpi-save-button')).toBeEnabled();
  });

  test('the error clears once the formula becomes valid', async ({ page }) => {
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(kpiName());

    await typeFormula(page, 'revenue / mystery');
    await expect(page.getByTestId('kpi-formula-error')).toBeVisible({
      timeout: 15_000,
    });

    await typeFormula(page, SAFE_FORMULA);
    await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0, {
      timeout: 15_000,
    });
  });

  test('a KPI can be created and survives a reload', async ({ page }) => {
    const name = kpiName();
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(name);
    await typeFormula(page, SAFE_FORMULA);

    // Wait for the debounced preview to resolve before saving.
    await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0, {
      timeout: 15_000,
    });

    await page.getByTestId('kpi-save-button').click();
    createdName = name;

    await expect(page.getByTestId('kpi-builder-dialog')).not.toBeVisible({
      timeout: 15_000,
    });

    const tile = panel(page).locator(`[data-kpi-name="${name}"]`);
    await expect(tile).toBeVisible({ timeout: 15_000 });
    await expect(tile).toContainText(SAFE_FORMULA);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(panel(page)).toBeVisible({ timeout: 30_000 });
    await expect(
      panel(page).locator(`[data-kpi-name="${name}"]`)
    ).toBeVisible({ timeout: 15_000 });
  });

  test('a saved KPI can be edited', async ({ page }) => {
    const name = kpiName();
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(name);
    await typeFormula(page, SAFE_FORMULA);
    await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0, {
      timeout: 15_000,
    });
    await page.getByTestId('kpi-save-button').click();
    createdName = name;

    const tile = panel(page).locator(`[data-kpi-name="${name}"]`);
    await expect(tile).toBeVisible({ timeout: 15_000 });

    await tile.getByRole('button', { name: `Edit ${name}` }).click();
    await expect(page.getByTestId('kpi-builder-dialog')).toBeVisible();
    await expect(formulaInput(page)).toContainText(SAFE_FORMULA);

    await typeFormula(page, OTHER_SAFE_FORMULA);
    await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0, {
      timeout: 15_000,
    });
    await page.getByTestId('kpi-save-button').click();

    await expect(page.getByTestId('kpi-builder-dialog')).not.toBeVisible({
      timeout: 15_000,
    });
    await expect(tile).toContainText(OTHER_SAFE_FORMULA, { timeout: 15_000 });
  });

  test('a saved KPI can be deleted', async ({ page }) => {
    const name = kpiName();
    await openBuilder(page);
    await page.getByTestId('kpi-name-input').fill(name);
    await typeFormula(page, SAFE_FORMULA);
    await expect(page.getByTestId('kpi-formula-error')).toHaveCount(0, {
      timeout: 15_000,
    });
    await page.getByTestId('kpi-save-button').click();

    const tile = panel(page).locator(`[data-kpi-name="${name}"]`);
    await expect(tile).toBeVisible({ timeout: 15_000 });

    await deleteKPI(page, name);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(panel(page)).toBeVisible({ timeout: 30_000 });
    await expect(
      panel(page).locator(`[data-kpi-name="${name}"]`)
    ).toHaveCount(0);
  });
});
