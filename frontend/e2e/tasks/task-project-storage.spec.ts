import { test, expect } from '@playwright/test';
import { getActiveProjectSlug } from './tasks-helpers';

// Isolated browser storage tests: no API, account or database fixture required.
test.use({ storageState: { cookies: [], origins: [] } });
test.beforeEach(async ({ page }) => {
  await page.route('http://storage.test/**', route => route.fulfill({
    contentType: 'text/html', body: '<html><body>Storage test</body></html>',
  }));
  await page.goto('http://storage.test/');
});

test('waits for a complete project after delayed hydration', async ({ page }) => {
  await page.evaluate(() => {
    localStorage.setItem('project-storage-v1', '{}');
    setTimeout(() => localStorage.setItem('project-storage-v1', JSON.stringify({
      state: { activeProject: { id: 7, slug: 'hydrated-project' } },
    })), 500);
  });
  expect(await getActiveProjectSlug(page)).toBe('hydrated-project');
});

test('uses complete legacy data when the new record is incomplete', async ({ page }) => {
  await page.evaluate(() => {
    localStorage.setItem('project-storage-v1', JSON.stringify({ state: { activeProject: { id: 9 } } }));
    localStorage.setItem('project-storage', JSON.stringify({
      state: { activeProject: { id: 7, slug: 'legacy-project' } },
    }));
  });
  expect(await getActiveProjectSlug(page)).toBe('legacy-project');
});
