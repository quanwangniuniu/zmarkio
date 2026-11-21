import { test, expect, type Page } from '@playwright/test';
import {
  createDraftTaskViaApi,
  deleteTaskById,
  getActiveProjectIdFromStore,
  type DraftTaskFixture,
} from '../tasks/tasks-helpers';

async function holdGet(page: Page, matches: (pathname: string) => boolean): Promise<() => void> {
  let release: () => void = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (route.request().method() === 'GET' && matches(pathname)) {
      await released;
    }
    await route.continue();
  });
  return release;
}

test.describe('Skeleton loaders', () => {
  test.describe.configure({ mode: 'serial' });
  let task: DraftTaskFixture | null = null;

  test.afterEach(async ({ page }) => {
    if (task) {
      await deleteTaskById(page, task.id);
      task = null;
    }
  });

  test('overview shows a skeleton until its data loads', async ({ page }) => {
    const release = await holdGet(page, (pathname) => pathname === '/api/dashboard/summary/');
    const skeleton = page.locator('main .skeleton-fade');
    const content = page.locator('#custom-kpis');

    await page.goto('/overview', { waitUntil: 'domcontentloaded' });

    await expect(skeleton.first()).toBeVisible({ timeout: 30_000 });
    await expect(content).toHaveCount(0);

    release();

    await expect(content).toBeVisible({ timeout: 30_000 });
  });

  test('task list shows skeleton rows until tasks load', async ({ page }) => {
    const release = await holdGet(page, (pathname) => pathname === '/api/tasks/');
    const list = page.getByTestId('task-list');

    await page.goto('/tasks?tab=tasks', { waitUntil: 'domcontentloaded' });

    await expect(list.locator('.skeleton-fade').first()).toBeVisible({ timeout: 30_000 });

    release();

    await expect(list.locator('.skeleton-fade')).toHaveCount(0, { timeout: 30_000 });
  });

  test('task detail shows a skeleton header until the task loads', async ({ page }) => {
    await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('tab-tasks')).toBeVisible({ timeout: 30_000 });
    const projectId = await getActiveProjectIdFromStore(page);
    task = await createDraftTaskViaApi(page, projectId, 'E2E skeleton loader task');
    const slug = task.slug;
    const release = await holdGet(
      page,
      (pathname) => decodeURIComponent(pathname) === `/api/tasks/${slug}/`,
    );
    const priority = page.getByTestId('header-priority-trigger');

    await page.goto(`/tasks/${encodeURIComponent(slug)}`, { waitUntil: 'domcontentloaded' });

    await expect(page.locator('main .skeleton-fade').first()).toBeVisible({ timeout: 30_000 });
    await expect(priority).toHaveCount(0);

    release();

    await expect(priority).toBeVisible({ timeout: 30_000 });
  });
});
