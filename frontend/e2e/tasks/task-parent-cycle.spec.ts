import { test, expect } from '@playwright/test';
import {
  createDraftTaskViaApi,
  deleteTaskById,
  ensureE2EPageReady,
  getActiveProjectIdFromStore,
  getAuthToken,
  linkSubtaskViaApi,
  openSubtaskDetailWithPicker,
  searchAndSelectParentInPicker,
  waitForAppGatewayReady,
} from './tasks-helpers';

const CYCLE_MESSAGE =
  'Cannot set this parent: it would create a circular task hierarchy.';

/**
 * UI coverage for MED-235 parent picker (deterministic waits + API fixtures).
 *
 * The 3-node move-cycle HTTP 422 contract is covered in backend/task/tests/test_hierarchy_cycle.py
 * (public API cannot seed that chain without a DB bypass because of one-level nesting rules).
 */
test.describe('Task hierarchy parent picker (MED-235)', () => {
  test.describe.configure({ mode: 'serial', timeout: 180_000 });

  let projectId: number;
  const createdTaskIds: number[] = [];

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(180_000);
    const context = await browser.newContext({ storageState: 'e2e/.auth/user.json' });
    const page = await context.newPage();
    await waitForAppGatewayReady(page);
    projectId = await getActiveProjectIdFromStore(page);

    // Warm Next.js /tasks/[taskId] compilation so the first spec test does not hit a cold 502.
    const warmup = await createDraftTaskViaApi(page, projectId, `E2E route warmup ${Date.now()}`);
    await expect
      .poll(
        async () => {
          await page.goto(`/tasks/${encodeURIComponent(warmup.slug)}`, { waitUntil: 'domcontentloaded' });
          const gatewayError = page.getByText(/502 Bad Gateway|504 Gateway/i);
          if (await gatewayError.isVisible({ timeout: 500 }).catch(() => false)) {
            return false;
          }
          const token = await getAuthToken(page);
          if (!token) return false;
          const response = await page.request.get(
            `${process.env.BASE_URL || 'http://localhost'}/api/tasks/${warmup.id}/`,
            { headers: { Authorization: `Bearer ${token}` } },
          );
          return response.ok();
        },
        {
          message: 'Task detail route warmup did not complete',
          timeout: 180_000,
          intervals: [3_000, 5_000, 10_000],
        },
      )
      .toBe(true);
    await deleteTaskById(page, warmup.id).catch(() => {});

    await context.close();
  });

  test.afterEach(async ({ page }) => {
    while (createdTaskIds.length > 0) {
      const taskId = createdTaskIds.pop();
      if (taskId != null) {
        await deleteTaskById(page, taskId).catch(() => {});
      }
    }
  });

  test('subtask detail shows Parent picker', async ({ page }) => {
    await ensureE2EPageReady(page);

    const stamp = Date.now();
    const parent = await createDraftTaskViaApi(page, projectId, `E2E Parent visible ${stamp}`);
    const child = await createDraftTaskViaApi(page, projectId, `E2E Child visible ${stamp}`);
    createdTaskIds.push(parent.id, child.id);

    await linkSubtaskViaApi(page, parent, child.id);
    await openSubtaskDetailWithPicker(page, child);

    await expect(page.getByText('Parent', { exact: true })).toBeVisible();
  });

  test('task list keeps the parent relationship payload contract', async ({ page }) => {
    await ensureE2EPageReady(page);

    const stamp = Date.now();
    const parentSummary = `E2E Payload parent ${stamp}`;
    const childSummary = `E2E Payload child ${stamp}`;
    const parent = await createDraftTaskViaApi(page, projectId, parentSummary);
    const child = await createDraftTaskViaApi(page, projectId, childSummary);
    createdTaskIds.push(parent.id, child.id);

    await linkSubtaskViaApi(page, parent, child.id);

    const token = await getAuthToken(page);
    expect(token).toBeTruthy();
    const origin = process.env.BASE_URL || 'http://localhost';
    const response = await page.request.get(`${origin}/api/tasks/`, {
      headers: { Authorization: `Bearer ${token}` },
      params: {
        project_id: String(projectId),
        include_subtasks: 'true',
        search: childSummary,
      },
    });

    expect(response.ok()).toBe(true);
    const body = await response.json();
    const tasks = Array.isArray(body) ? body : body.results;
    const childPayload = tasks.find((task: { id: number }) => task.id === child.id);

    expect(childPayload?.parent_relationship).toEqual([{
      parent_task_id: parent.id,
      parent_task_slug: parent.slug,
      parent_task_summary: parentSummary,
    }]);
    // Normalize only generated fixture identity; preserve the API structure.
    expect(JSON.stringify({
      is_subtask: childPayload.is_subtask,
      parent_relationship: childPayload.parent_relationship.map((relation: {
        parent_task_id: number;
        parent_task_slug: string;
        parent_task_summary: string;
      }) => ({
        ...relation,
        parent_task_id: '<parent-id>',
        parent_task_slug: '<parent-slug>',
        parent_task_summary: '<parent-summary>',
      })),
    }, null, 2) + '\n').toMatchSnapshot('task-list-parent-payload.txt');
  });

  test('parent picker shows inline error when move returns hierarchy cycle 422', async ({ page }) => {
    await ensureE2EPageReady(page);

    const stamp = Date.now();
    const parentA = await createDraftTaskViaApi(page, projectId, `E2E Cycle parent A ${stamp}`);
    const childB = await createDraftTaskViaApi(page, projectId, `E2E Cycle child B ${stamp}`);
    const parentC = await createDraftTaskViaApi(page, projectId, `E2E Cycle parent C ${stamp}`);
    createdTaskIds.push(parentA.id, childB.id, parentC.id);

    await linkSubtaskViaApi(page, parentA, childB.id);
    await openSubtaskDetailWithPicker(page, childB);

    await page.route('**/api/tasks/**/subtasks/**/move/**', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: CYCLE_MESSAGE,
          code: 'task_hierarchy_cycle',
        }),
      });
    });

    const moveResponse = page.waitForResponse(
      (resp) =>
        resp.url().includes('/subtasks/') &&
        resp.url().includes('/move/') &&
        resp.request().method() === 'POST',
    );

    await searchAndSelectParentInPicker(
      page,
      parentC.summary.slice(0, 12),
      parentC.summary,
    );

    const response = await moveResponse;
    expect(response.status()).toBe(422);

    await expect(page.getByTestId('task-parent-picker-error')).toBeVisible();
    await expect(page.getByTestId('task-parent-picker-error')).toContainText('circular task hierarchy');
  });

  test('parent picker can reassign subtask to a new valid parent', async ({ page }) => {
    await ensureE2EPageReady(page);

    const stamp = Date.now();
    const parentA = await createDraftTaskViaApi(page, projectId, `E2E Move parent A ${stamp}`);
    const childB = await createDraftTaskViaApi(page, projectId, `E2E Move child B ${stamp}`);
    const parentC = await createDraftTaskViaApi(page, projectId, `E2E Move parent C ${stamp}`);
    createdTaskIds.push(parentA.id, childB.id, parentC.id);

    await linkSubtaskViaApi(page, parentA, childB.id);
    await openSubtaskDetailWithPicker(page, childB);

    const moveResponse = page.waitForResponse(
      (resp) =>
        resp.url().includes('/subtasks/') &&
        resp.url().includes('/move/') &&
        resp.request().method() === 'POST' &&
        resp.ok(),
    );

    await searchAndSelectParentInPicker(
      page,
      parentC.summary.slice(0, 12),
      parentC.summary,
    );

    await moveResponse;

    await expect(page.getByTestId('task-parent-picker-error')).not.toBeVisible();
    await expect(page.getByRole('combobox', { name: 'Parent task' })).toContainText(parentC.summary);
  });
});
