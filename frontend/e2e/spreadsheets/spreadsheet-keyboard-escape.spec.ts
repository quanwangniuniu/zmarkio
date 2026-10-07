import {
  test,
  expect,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from '@playwright/test';
import {
  clickCell,
  getOrCreateSpreadsheetSlug,
  waitForSpreadsheetPageReady,
} from './spreadsheet-helpers';

/**
 * MED-311 — keyboard users can always leave the spreadsheet grid.
 * Esc jumps to the toolbar; Shift+Tab from the first cell leaves the grid and
 * lands on the "Skip to toolbar" link.
 */
test.describe('Spreadsheet grid keyboard escape (MED-311)', () => {
  async function openGrid(
    page: Page,
    context: BrowserContext,
    request: APIRequestContext,
  ) {
    const slug = await getOrCreateSpreadsheetSlug(context, request);
    await page.goto(`/spreadsheets/${slug}`);
    await waitForSpreadsheetPageReady(page);
    await expect(page.locator('td[data-row][data-col]').first()).toBeVisible({
      timeout: 30_000,
    });
    return page.getByTestId('spreadsheet-grid');
  }

  function firstToolbarButton(page: Page) {
    return page
      .getByRole('toolbar', { name: 'Spreadsheet toolbar' })
      .getByRole('button')
      .first();
  }

  test('Esc moves focus from the grid to the toolbar', async ({
    page,
    context,
    request,
  }) => {
    const grid = await openGrid(page, context, request);
    await clickCell(page, 1, 1);
    await expect(grid).toBeFocused();

    await page.keyboard.press('Escape');

    await expect(grid).not.toBeFocused();
    await expect(firstToolbarButton(page)).toBeFocused();
  });

  test('Shift+Tab from the first cell leaves the grid; skip link reaches the toolbar', async ({
    page,
    context,
    request,
  }) => {
    const grid = await openGrid(page, context, request);
    await clickCell(page, 0, 1);
    await expect(grid).toBeFocused();

    // Not on the first cell yet: Shift+Tab moves left and stays in the grid.
    await page.keyboard.press('Shift+Tab');
    await expect(grid).toBeFocused();

    // On the first cell: Shift+Tab leaves the grid without entering edit mode.
    await page.keyboard.press('Shift+Tab');
    await expect
      .poll(() => grid.evaluate((el) => el.contains(document.activeElement)))
      .toBe(false);
    const skipLink = page.getByRole('link', { name: 'Skip to toolbar' });
    await expect(skipLink).toBeFocused();

    await page.keyboard.press('Enter');
    await expect(firstToolbarButton(page)).toBeFocused();
  });
});
