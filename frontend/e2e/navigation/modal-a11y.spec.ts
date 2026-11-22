import { test, expect, type Locator, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const POOL = {
  id: 9001,
  name: 'E2E modal pool',
  project: 1,
  ad_channel: 1,
  ad_channel_name: 'Search',
  total_amount: '1000.00',
  used_amount: '0.00',
  available_amount: '1000.00',
  currency: 'USD',
  created_at: null,
};

async function expectNoAxeViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .disableRules(['color-contrast'])
    .analyze();
  expect(results.violations).toEqual([]);
}

async function expectFocusTrapped(page: Page, dialog: Locator) {
  for (let i = 0; i < 12; i += 1) {
    await page.keyboard.press('Tab');
    await expect(dialog.locator(':focus')).toHaveCount(1);
  }
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.locator(':focus')).toHaveCount(1);
}

test.describe('Modal accessibility', () => {
  test('Modal is announced, traps focus and restores it on Escape', async ({ page }) => {
    await page.goto('/select-project');

    const trigger = page.getByRole('button', { name: 'New Project' });
    await trigger.click();

    const dialog = page.getByRole('dialog', { name: 'Create a new project' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');
    await expectNoAxeViolations(page);
    await expectFocusTrapped(page, dialog);

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });

  test('Dialog is announced, traps focus and restores it on Escape', async ({ page }) => {
    await page.goto('/overview');

    const trigger = page.getByRole('button', { name: 'New KPI' });
    await trigger.click();

    const dialog = page.getByRole('dialog', { name: 'New KPI' });
    await expect(dialog).toBeVisible();
    await expectNoAxeViolations(page);
    await expectFocusTrapped(page, dialog);

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
  });

  test('ConfirmModal is announced with its message, traps focus and closes on Escape', async ({ page }) => {
    await page.route(
      (url) => url.pathname === '/api/budgets/pools/',
      async (route) => {
        if (route.request().method() !== 'GET') {
          await route.continue();
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ count: 1, results: [POOL] }),
        });
      },
    );
    await page.goto('/budget-pools');

    await page.getByTitle('Delete').click();

    const dialog = page.getByRole('dialog', { name: 'Delete budget pool' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleDescription(/permanently removed/);
    await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await expectNoAxeViolations(page);
    await expectFocusTrapped(page, dialog);

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  });
});
