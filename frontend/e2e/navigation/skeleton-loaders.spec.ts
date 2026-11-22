import { test, expect, type Page } from '@playwright/test';
import {
  createDraftTaskViaApi,
  deleteTaskById,
  getActiveProjectIdFromStore,
  type DraftTaskFixture,
} from '../tasks/tasks-helpers';

type HeldRequest = { requested: Promise<void>; release: () => void };

async function holdGet(page: Page, pathname: string): Promise<HeldRequest> {
  let release: () => void = () => {};
  let markRequested: () => void = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    markRequested = resolve;
  });
  await page.route(
    (url) => decodeURIComponent(url.pathname) === pathname,
    async (route) => {
      if (route.request().method() === 'GET') {
        markRequested();
        await released;
      }
      await route.continue();
    },
  );
  return { requested, release };
}

test.describe('Skeleton loaders', () => {
  test.describe.configure({ mode: 'serial' });
  let task: DraftTaskFixture | null = null;

  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' });
    if (task) {
      await deleteTaskById(page, task.id);
      task = null;
    }
  });

  test('overview keeps one skeleton until the page data and the dashboard data load', async ({ page }) => {
    const summary = await holdGet(page, '/api/dashboard/summary/');
    const workspace = await holdGet(page, '/api/dashboard/workspace/');
    const skeleton = page.locator('main .skeleton-fade');
    const content = page.locator('#custom-kpis');
    const dashboard = page.getByText('Project Overview', { exact: true });

    await page.goto('/overview', { waitUntil: 'domcontentloaded' });

    await summary.requested;
    await expect(skeleton.first()).toBeVisible({ timeout: 30_000 });
    await expect(content).toHaveCount(0);

    summary.release();

    await workspace.requested;
    await expect(content).toBeAttached({ timeout: 30_000 });
    await expect(skeleton.first()).toBeVisible();
    await expect(dashboard).toHaveCount(0);

    workspace.release();

    await expect(dashboard).toBeVisible({ timeout: 30_000 });
    await expect(skeleton).toHaveCount(0);
  });

  test('task list shows skeleton rows until tasks load', async ({ page }) => {
    const projectId = await getActiveProjectIdFromStore(page);
    task = await createDraftTaskViaApi(page, projectId, `E2E skeleton list task ${Date.now()}`);
    const tasks = await holdGet(page, '/api/tasks/');
    const list = page.getByTestId('task-list');
    const row = list.getByTestId('task-row').filter({ hasText: task.summary });

    await page.goto('/tasks?tab=tasks', { waitUntil: 'domcontentloaded' });

    await tasks.requested;
    await expect(list.locator('.skeleton-fade').first()).toBeVisible({ timeout: 30_000 });
    await expect(row).toHaveCount(0);

    tasks.release();

    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(list.locator('.skeleton-fade')).toHaveCount(0);
  });

  test('task detail shows a skeleton header until the task loads', async ({ page }) => {
    const projectId = await getActiveProjectIdFromStore(page);
    task = await createDraftTaskViaApi(page, projectId, `E2E skeleton detail task ${Date.now()}`);
    const detail = await holdGet(page, `/api/tasks/${task.slug}/`);
    const priority = page.getByTestId('header-priority-trigger');

    await page.goto(`/tasks/${encodeURIComponent(task.slug)}`, { waitUntil: 'domcontentloaded' });

    await detail.requested;
    await expect(page.locator('main .skeleton-fade').first()).toBeVisible({ timeout: 30_000 });
    await expect(priority).toHaveCount(0);

    detail.release();

    await expect(priority).toBeVisible({ timeout: 30_000 });
  });
});
