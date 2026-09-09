import { expect, test } from '@playwright/test';
import { goToKlaviyoList } from '../email_draft/email-draft-helpers';

/**
 * identical error toasts from a retry loop must merge into one toast
 * with a visible ×N count badge (via toastDeduped + notificationStore).
 */
test.describe('Toast dedupe on retry', () => {
  test('identical create failures merge into one toast with count badge', async ({ page }) => {
    await page.route('**/api/klaviyo/klaviyo-drafts/', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Create failed' }),
      });
    });

    try {
      await goToKlaviyoList(page);

      const newTemplate = page.getByRole('button', { name: 'New template' });
      await expect(newTemplate).toBeVisible({ timeout: 30_000 });

      // Simulate user retrying the same failed create three times.
      for (let attempt = 0; attempt < 3; attempt += 1) {
        await expect(newTemplate).toBeEnabled({ timeout: 10_000 });
        await newTemplate.click();
        await expect(page.getByTestId('toast-error')).toBeVisible({ timeout: 10_000 });
      }

      // One merged toast only — not three stacked toasts.
      await expect(page.getByTestId('toast-error')).toHaveCount(1);
      await expect(
        page.getByText('Failed to create template. Please try again.'),
      ).toBeVisible();
      await expect(page.getByTestId('toast-count-badge')).toHaveText('×3');
    } finally {
      await page.unroute('**/api/klaviyo/klaviyo-drafts/');
    }
  });
});
