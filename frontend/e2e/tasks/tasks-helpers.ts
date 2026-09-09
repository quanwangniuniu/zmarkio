import { type Page, type Locator, expect } from '@playwright/test';

function isProjectsListGetResponse(resp: { url(): string; request(): { method(): string } }): boolean {
  const url = new URL(resp.url());
  return url.pathname.includes('/api/core/projects') && resp.request().method() === 'GET';
}

function isTaskListGetResponse(resp: { url(): string; request(): { method(): string } }): boolean {
  const url = new URL(resp.url());
  const path = url.pathname.replace(/\/$/, '');
  return resp.request().method() === 'GET' && (path === '/api/tasks' || path === '/api/tasks/');
}

export async function waitForAppGatewayReady(page: Page): Promise<void> {
  const origin = process.env.BASE_URL || 'http://localhost';

  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${origin}/`, { timeout: 10_000 });
        return response.status() !== 502 && response.status() !== 504;
      },
      {
        message: `App gateway at ${origin} did not become ready`,
        timeout: 120_000,
        intervals: [1_000, 2_000, 3_000, 5_000],
      },
    )
    .toBe(true);
}

export async function ensureE2EPageReady(page: Page): Promise<void> {
  await waitForAppGatewayReady(page);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await waitForTasksPageReady(page);
}

/**
 * Wait until workspace bootstrap finishes (projects API + loading overlay gone).
 */
export async function waitForWorkspaceReady(page: Page): Promise<void> {
  const preparing = page.getByText('Preparing your workspace');
  const alreadyReady = !(await preparing.isVisible({ timeout: 1_000 }).catch(() => false));
  if (!alreadyReady) {
    await page.waitForResponse(
      (resp) => isProjectsListGetResponse(resp) && resp.ok(),
      { timeout: 30_000 },
    );
    await expect(preparing).not.toBeVisible({ timeout: 30_000 });
  }
  await waitForTasksPageReady(page);
}

/**
 * Wait for any blocking overlay (Guided Onboarding, "Preparing your workspace")
 * to dismiss before interacting with the tasks page.
 * If "Failed to load projects" is shown, clicks "Retry check" and waits for success.
 */
export async function waitForTasksPageReady(page: Page) {
  const retryBtn = page.getByRole('button', { name: 'Retry check' });
  if (await retryBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await retryBtn.click();
  }

  await expect(page.getByText('Guided Onboarding')).not.toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText('Preparing your workspace')).not.toBeVisible({
    timeout: 5_000,
  });
}

export async function getActiveProjectSlug(page: Page): Promise<string> {
  const slug = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('project-storage-v1') ?? localStorage.getItem('project-storage');
      if (!raw) return null;
      return (JSON.parse(raw) as { state?: { activeProject?: { slug?: string } } })?.state?.activeProject?.slug ?? null;
    } catch {
      return null;
    }
  });
  if (!slug) throw new Error('No active project slug found in store');
  return slug;
}

/**
 * Read active project id from persisted store after workspace bootstrap.
 * Lighter than navigateToTasksAndSelectProject — use in beforeAll when tests
 * navigate to tasks themselves.
 */
export async function getActiveProjectIdFromStore(page: Page): Promise<number> {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await waitForWorkspaceReady(page);

  const projectId: number | null = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('project-storage-v1') ?? localStorage.getItem('project-storage');
      if (!raw) return null;
      return (JSON.parse(raw) as any)?.state?.activeProject?.id ?? null;
    } catch {
      return null;
    }
  });

  if (!projectId) {
    throw new Error('No active project found in store — ensure the test user has at least one project');
  }
  return projectId;
}

/**
 * Navigate to /tasks, select a project by name, and return its ID.
 * Run once before all tests (in beforeAll) to grab the project ID.
 * Uses E2E_PROJECT_NAME env var (default "Q1 E2E Task").
 */
export async function navigateToTasksAndSelectProject(page: Page): Promise<number> {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await waitForWorkspaceReady(page);

  const projectId: number | null = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('project-storage-v1') ?? localStorage.getItem('project-storage');
      if (!raw) return null;
      return (JSON.parse(raw) as any)?.state?.activeProject?.id ?? null;
    } catch {
      return null;
    }
  });

  if (!projectId) throw new Error('No active project found in store — ensure the test user has at least one project');

  const projectSlug = await getActiveProjectSlug(page);
  await page.goto(`/projects/${encodeURIComponent(projectSlug)}/tasks`, {
    waitUntil: 'domcontentloaded',
  });
  await expect(page.getByTestId('tab-tasks')).toBeVisible({ timeout: 30_000 });
  await waitForTasksPageReady(page);
  return projectId;
}

/**
 * Ensure we are on the tasks page for the given project.
 * Run before each test (in beforeEach) to navigate to /tasks?project_id=X&view=timeline.
 */
export async function ensureOnTasksPage(page: Page, _projectId: number): Promise<void> {
  const projectSlug = await getActiveProjectSlug(page);
  await page.goto(`/projects/${encodeURIComponent(projectSlug)}/tasks?view=timeline`);
  await waitForTasksPageReady(page);
}

/**
 * Full flow: navigate, select project, wait for ready.
 * Use when you need the full flow in each test (e.g. task-create.spec).
 */
export async function goToTasks(page: Page): Promise<void> {
  await navigateToTasksAndSelectProject(page);
}

/** Alias for goToTasks (same behavior). */
export const goToTasksWithProject = goToTasks;

/**
 * Click the Create button, capture the task ID from the POST response,
 * and wait for the panel to close.
 * @deprecated Use submitNewTaskAndGetId for the /tasks/new page flow.
 */
export async function submitCreateAndGetId(
  page: Page,
  panel: Locator,
): Promise<number | null> {
  const responsePromise = page.waitForResponse((resp) => {
    const path = new URL(resp.url()).pathname;
    return (
      (path === '/api/tasks/' || path === '/api/tasks') &&
      resp.request().method() === 'POST'
    );
  });

  await panel.getByRole('button', { name: 'Create', exact: true }).click();

  const response = await responsePromise;
  let taskId: number | null = null;
  if (response.ok()) {
    const body = await response.json();
    taskId = body.id ?? null;
  }

  await expect(panel).not.toBeVisible({ timeout: 10_000 });
  return taskId;
}

/**
 * Navigate to the /tasks/new page for the given project.
 */
export async function navigateToNewTaskPage(page: Page, projectId: number): Promise<void> {
  await page.goto(`/tasks/new?project_id=${projectId}`);
  await expect(page.getByPlaceholder('Summary of this task')).toBeVisible({ timeout: 15_000 });
}

/**
 * On the /tasks/new page: intercept the task POST, click "Create task", wait for the page to
 * navigate away (which means the linked-object creation + /link/ call have both finished),
 * then return the new task ID.
 *
 * Waiting for navigation is critical: without it, afterEach can delete the Task while the
 * browser is still POSTing the linked object, leaving the object orphaned or the link unset.
 */
export async function submitNewTaskAndGetId(page: Page): Promise<number | null> {
  const responsePromise = page.waitForResponse((resp) => {
    const path = new URL(resp.url()).pathname;
    return (path === '/api/tasks/' || path === '/api/tasks') && resp.request().method() === 'POST';
  });

  // Set up navigation wait BEFORE clicking so we don't miss a fast redirect.
  const navigationPromise = page.waitForURL((url) => !url.pathname.startsWith('/tasks/new'), {
    timeout: 30_000,
  }).catch(() => {});

  const submitButton = page.getByRole('button', { name: 'Create task', exact: true });
  await expect(submitButton).toBeEnabled({ timeout: 10_000 });
  await submitButton.click();

  const response = await responsePromise;
  if (!response.ok()) return null;
  const body = await response.json();
  const taskId = body.id ?? null;

  // Wait for navigation to complete — this means the full create flow (linked object + link)
  // has finished in the browser before afterEach can clean up.
  await navigationPromise;

  return taskId;
}

/**
 * Delete all tasks whose summary starts with "E2E " (left over from interrupted test runs).
 * Fetches up to 100 tasks per page and deletes any E2E ones.
 */
export async function deleteAllE2ETasks(page: Page): Promise<void> {
  const token = await getAuthToken(page);
  if (!token) return;

  const origin = new URL(page.url()).origin;
  let nextUrl: string | null = `${origin}/api/tasks/?page_size=100`;
  while (nextUrl) {
    const listRes = await page.request.get(nextUrl, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!listRes.ok()) break;
    const data = await listRes.json();
    const tasks: { id: number; summary: string }[] = data.results ?? data ?? [];
    for (const t of tasks) {
      if (typeof t.summary === 'string' && t.summary.startsWith('E2E ')) {
        await page.request.delete(`${origin}/api/tasks/${t.id}/`, {
          headers: { Authorization: `Bearer ${token}` },
        }).catch(() => {});
      }
    }
    nextUrl = typeof data.next === 'string' ? data.next : null;
  }
}

/**
 * Select the first real approver option in the approver dropdown on the new-task page.
 * Must be called after a task type is selected (the select is only visible then).
 * Waits for members to load before selecting. Throws if no project members are available.
 */
export async function selectFirstAvailableApprover(page: Page): Promise<void> {
  const approverSelect = page.locator('#task-common-approver select');
  await expect(approverSelect).toBeVisible({ timeout: 10_000 });
  await expect(approverSelect).not.toBeDisabled({ timeout: 10_000 });

  // Wait until the first option's text changes from "Loading…" (members have loaded)
  await expect(approverSelect.locator('option').first()).not.toHaveText(/Loading/i, { timeout: 10_000 });

  const options = await approverSelect.locator('option').allTextContents();
  const realOptions = options.filter(
    (o) => !o.includes('Select an approver') && !o.includes('Unassigned') && !o.includes('Loading'),
  );
  if (realOptions.length > 0) {
    const optionValue = await approverSelect.locator('option').nth(1).getAttribute('value');
    if (!optionValue) throw new Error('First project member option did not include a value');
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await approverSelect.selectOption(optionValue);
      await page.waitForTimeout(200);
      if ((await approverSelect.inputValue()) === optionValue) return;
    }
    throw new Error('Approver selection was reset before it could be submitted');
  } else {
    throw new Error('No project members are available to select as task approver');
  }
}

/**
 * Delete a task by ID via the REST API using the auth token from localStorage.
 */
export async function deleteTaskById(page: Page, taskId: number) {
  const token = await getAuthToken(page);
  if (!token) return;

  const origin = new URL(page.url()).origin;
  await page.request.delete(`${origin}/api/tasks/${taskId}/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export async function getAuthToken(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    try {
      const raw = localStorage.getItem('auth-storage-v1');
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed?.state?.token ?? null;
    } catch {
      return null;
    }
  });
}

/** Nested task list URL with quick-view drawer open (path segment, not query). */
export function buildTasksListDrawerUrl(projectSlug: string, taskSlug: string): string {
  return `/projects/${encodeURIComponent(projectSlug)}/tasks/${encodeURIComponent(taskSlug)}`;
}

export type DraftTaskFixture = { id: number; slug: string };

function taskPathKey(task: Pick<DraftTaskFixture, 'id' | 'slug'> | number | string): string {
  if (typeof task === 'number') {
    return String(task);
  }
  if (typeof task === 'object' && task != null) {
    return String(task.id);
  }
  if (typeof task === 'string') {
    return task;
  }
  throw new Error('Task path key requires a task id, slug, or fixture');
}

export async function createDraftTaskViaApi(
  page: Page,
  projectId: number,
  summary: string,
  overrides: Record<string, unknown> = {},
): Promise<DraftTaskFixture> {
  const token = await getAuthToken(page);
  if (!token) throw new Error('No auth token found for task fixture creation');

  const origin = new URL(page.url()).origin;
  const response = await page.request.post(`${origin}/api/tasks/`, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: {
      project_id: projectId,
      type: 'execution',
      summary,
      priority: 'MEDIUM',
      create_as_draft: true,
      ...overrides,
    },
  });

  if (!response.ok()) {
    throw new Error(`Failed to create task fixture (${response.status()}): ${await response.text()}`);
  }

  const body = await response.json();
  if (!body?.id || !body?.slug) {
    throw new Error('Task fixture response did not include id and slug');
  }
  return { id: body.id as number, slug: body.slug as string };
}

export async function linkSubtaskViaApi(
  page: Page,
  parent: Pick<DraftTaskFixture, 'id' | 'slug'> | number | string,
  childTaskId: number,
): Promise<void> {
  const token = await getAuthToken(page);
  if (!token) throw new Error('No auth token found for subtask link');

  const origin = new URL(page.url()).origin;
  const response = await page.request.post(`${origin}/api/tasks/${taskPathKey(parent)}/subtasks/`, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: { child_task_id: childTaskId },
  });

  if (!response.ok()) {
    throw new Error(`Failed to link subtask (${response.status()}): ${await response.text()}`);
  }
}

export async function moveSubtaskViaApi(
  page: Page,
  newParent: Pick<DraftTaskFixture, 'id' | 'slug'> | number | string,
  child: Pick<DraftTaskFixture, 'id' | 'slug'> | number | string,
  oldParentId: number,
): Promise<{ status: number; body: Record<string, unknown> | null }> {
  const token = await getAuthToken(page);
  if (!token) throw new Error('No auth token found for subtask move');

  const origin = new URL(page.url()).origin;
  const response = await page.request.post(
    `${origin}/api/tasks/${taskPathKey(newParent)}/subtasks/${taskPathKey(child)}/move/`,
    {
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      data: { old_parent_id: oldParentId },
    },
  );

  const text = await response.text();
  let body: Record<string, unknown> | null = null;
  if (text) {
    try {
      body = JSON.parse(text) as Record<string, unknown>;
    } catch {
      body = { raw: text };
    }
  }

  return { status: response.status(), body };
}

export async function openTaskDetailPage(page: Page, taskSlug: string): Promise<void> {
  await page.goto(`/tasks/${encodeURIComponent(taskSlug)}`);
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15_000 });
}

function isTaskDetailGetResponse(
  resp: { url(): string; request(): { method(): string }; ok(): boolean },
  task: Pick<DraftTaskFixture, 'id' | 'slug'> | string,
): boolean {
  if (resp.request().method() !== 'GET' || !resp.ok()) {
    return false;
  }
  const url = new URL(resp.url());
  const path = decodeURIComponent(url.pathname.replace(/\/$/, ''));
  const prefix = '/api/tasks/';
  if (!path.startsWith(prefix)) {
    return false;
  }
  const segment = path.slice(prefix.length);
  if (!segment || segment.includes('/')) {
    return false;
  }
  if (typeof task === 'string') {
    return segment === decodeURIComponent(task);
  }
  return segment === task.slug || segment === String(task.id);
}

function isTaskSearchGetResponse(resp: { url(): string; request(): { method(): string } }): boolean {
  const url = new URL(resp.url());
  const path = url.pathname.replace(/\/$/, '');
  return (
    resp.request().method() === 'GET' &&
    (path === '/api/tasks' || path === '/api/tasks/') &&
    url.searchParams.has('search')
  );
}

/**
 * Open a subtask detail page and wait for the task GET + parent picker to be ready.
 * Deterministic waits: task detail API 200, then task-parent-picker visible.
 */
export async function openSubtaskDetailWithPicker(
  page: Page,
  task: Pick<DraftTaskFixture, 'id' | 'slug'> | string,
): Promise<void> {
  const slug = typeof task === 'string' ? task : task.slug;

  await expect
    .poll(
      async () => {
        const detailResponse = page
          .waitForResponse((resp) => isTaskDetailGetResponse(resp, task), { timeout: 15_000 })
          .catch(() => null);

        await page.goto(`/tasks/${encodeURIComponent(slug)}`, { waitUntil: 'domcontentloaded' });

        const gatewayError = page.getByText(/502 Bad Gateway|504 Gateway/i);
        if (await gatewayError.isVisible({ timeout: 500 }).catch(() => false)) {
          return false;
        }

        await detailResponse;
        return page.getByTestId('task-parent-picker').isVisible().catch(() => false);
      },
      {
        message: `Subtask detail and parent picker did not become ready for ${slug}`,
        timeout: 90_000,
        intervals: [1_000, 2_000, 3_000],
      },
    )
    .toBe(true);

  await expect(page.getByRole('combobox', { name: 'Parent task' })).toBeVisible();
}

/**
 * Open the parent picker, run a debounced search, wait for GET /api/tasks/?search=…,
 * then select the matching option.
 */
export async function searchAndSelectParentInPicker(
  page: Page,
  searchText: string,
  optionLabel: string,
): Promise<void> {
  await page.getByRole('combobox', { name: 'Parent task' }).click();

  const searchResponse = page.waitForResponse(
    (resp) => isTaskSearchGetResponse(resp) && resp.ok(),
    { timeout: 15_000 },
  );

  await page.getByTestId('task-parent-picker-search').fill(searchText);
  await searchResponse;

  const option = page.getByRole('option', { name: optionLabel });
  await expect(option).toBeVisible({ timeout: 10_000 });
  await option.click();
}

export async function ensureTaskListReadyWithRows(
  page: Page,
  projectId: number,
  minRows = 1,
): Promise<number[]> {
  const createdIds: number[] = [];
  const currentRows = await page.getByTestId('task-row-open').count();
  const listVisible = await page.getByTestId('task-list').isVisible({ timeout: 5_000 }).catch(() => false);

  if (listVisible && currentRows >= minRows) return createdIds;

  for (let i = currentRows; i < minRows; i += 1) {
    const fixture = await createDraftTaskViaApi(page, projectId, `Task list fixture ${Date.now()} ${i}`);
    createdIds.push(fixture.id);
  }

  const projectSlug = await getActiveProjectSlug(page);
  await page.goto(`/projects/${encodeURIComponent(projectSlug)}/tasks`);
  await waitForTasksPageReady(page);
  await page.getByTestId('tab-tasks').click();
  await expect(page.getByTestId('task-list')).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId('task-row-open').nth(minRows - 1)).toBeVisible({ timeout: 10_000 });

  return createdIds;
}

export async function openQuickTaskCreate(page: Page): Promise<void> {
  const inlineAdd = page.getByTestId('inline-add-task-row');
  if (await inlineAdd.isVisible({ timeout: 1_000 }).catch(() => false)) {
    await inlineAdd.click();
    return;
  }

  await page.getByRole('button', { name: 'Add the first task' }).click();
}
