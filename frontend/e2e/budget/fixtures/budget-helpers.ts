import { expect, type Page } from '@playwright/test';
import { waitForTasksPageReady } from '../../tasks/tasks-helpers';

const gotoOpts = { waitUntil: 'domcontentloaded' as const };

export async function waitForTaskDrawerReady(page: Page): Promise<void> {
  await expect(page.getByTestId('task-drawer')).toBeVisible({ timeout: 15_000 });
  await page.waitForFunction(
    () => !document.querySelector('.animate-pulse'),
    { timeout: 15_000 },
  );
}

export async function openTaskDetail(page: Page, taskSlug: string, projectId: number): Promise<void> {
  await page.goto(`/tasks/${encodeURIComponent(taskSlug)}?project_id=${projectId}`, gotoOpts);
  await waitForTasksPageReady(page);
  await expect(page).toHaveURL(new RegExp(`/tasks/${escapeRegex(taskSlug)}(?:\\?|$)`), {
    timeout: 20_000,
  });
  await expect(page.getByRole('heading', { name: /budget details/i })).toBeVisible({
    timeout: 20_000,
  });
}

export async function openTaskDrawer(page: Page, projectId: number, taskSlug: string): Promise<void> {
  await page.goto(
    `/tasks?project_id=${projectId}&drawerTask=${encodeURIComponent(taskSlug)}`,
    gotoOpts,
  );
  await waitForTasksPageReady(page);
  await page.getByTestId('tab-tasks').click().catch(() => {});
  await waitForTaskDrawerReady(page);
}

export async function clickFsmButton(page: Page, label: string): Promise<void> {
  const btn = page.getByRole('button', { name: label, exact: true });
  await expect(btn).toBeVisible({ timeout: 15_000 });
  await expect(btn).toBeEnabled({ timeout: 15_000 });
  await btn.click();
}

export async function submitBudgetTaskFromDrawer(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((resp) => {
    const url = new URL(resp.url()).pathname;
    return url.includes('/submit') && resp.request().method() === 'POST';
  });
  await clickFsmButton(page, 'Submit');
  await responsePromise;
  await expect(page.getByText(/submitted/i).first()).toBeVisible({ timeout: 10_000 });
}

export async function startReviewOnDetail(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((resp) => {
    const url = new URL(resp.url()).pathname;
    return url.includes('/start-review') && resp.request().method() === 'POST';
  });
  await clickFsmButton(page, 'Start Review');
  await responsePromise;
  await expectStatusChip(page, /under.?review/i);
}

export async function approveWithComment(page: Page, _comment?: string): Promise<void> {
  const approvalPromise = page.waitForResponse((resp) => {
    const url = new URL(resp.url()).pathname;
    return url.includes('/make-approval') && resp.request().method() === 'POST';
  });
  const reloadPromise = page.waitForResponse((resp) => {
    const url = new URL(resp.url()).pathname;
    return /\/api\/tasks\/[^/]+\/?$/.test(url) && resp.request().method() === 'GET';
  });
  await clickFsmButton(page, 'Approve');
  const approvalResp = await approvalPromise;
  expect(approvalResp.ok()).toBeTruthy();
  await reloadPromise.catch(() => {});
}

export async function expectAdminOverrideBadge(page: Page): Promise<void> {
  await expect(page.getByTestId('admin-override-badge').first()).toBeVisible({
    timeout: 15_000,
  });
}

export async function rejectWithComment(page: Page, comment: string): Promise<void> {
  await clickFsmButton(page, 'Reject');
  const textarea = page.getByPlaceholder(/explain why you're rejecting/i);
  await expect(textarea).toBeVisible({ timeout: 10_000 });
  await textarea.fill(comment);
  await page.getByRole('button', { name: 'Confirm reject', exact: true }).click();
  await expectStatusChip(page, /rejected/i);
}

export async function expectStatusChip(page: Page, pattern: RegExp): Promise<void> {
  await expect(page.getByText(pattern).first()).toBeVisible({ timeout: 10_000 });
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
