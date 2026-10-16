import { expect, test, type Page } from '@playwright/test';
import type { DashboardWidgetPosition } from '../../src/types/dashboardLayout';
import {
  DEFAULT_MESSAGES_E2E_USER,
  installApiMockSafetyNet, mockAuthenticatedUserApis, mockProjectShellApis,
  seedActiveProject, seedAuthenticatedUser, waitForLayoutMain,
} from '../messages/messages-helpers';

const project = {
  id: 287, slug: 'med-287', name: 'Dashboard Layout Test', is_active: true,
  organization: { id: 28, name: 'Dashboard Test Org', slug: 'dashboard-test-org' },
};
const catalog = [
  { id: 'overall-progress', label: 'Overall progress', x: 0, y: 0, w: 3, h: 5, min_h: 4 },
  { id: 'task-completion-rate', label: 'Task completion rate', x: 3, y: 0, w: 3, h: 5, min_h: 4 },
  { id: 'decisions-summary', label: 'Decisions', x: 0, y: 5, w: 4, h: 10, min_h: 6 },
  { id: 'tasks-summary', label: 'Tasks', x: 4, y: 5, w: 4, h: 10, min_h: 6 },
  { id: 'operations-summary', label: 'Operations', x: 8, y: 5, w: 4, h: 10, min_h: 6 },
  { id: 'audit', label: 'Audit', x: 0, y: 15, w: 6, h: 6, min_h: 4 },
  { id: 'activity', label: 'Recent activity', x: 6, y: 15, w: 6, h: 6, min_h: 4 },
  { id: 'meetings', label: 'Meetings & action items', x: 0, y: 55, w: 6, h: 8, min_h: 4 },
];
const otherDataCatalog = [
  { id: 'custom-kpis', label: 'Custom KPIs', x: 0, y: 43, w: 12, h: 8, min_h: 4 },
  { id: 'project-team', label: 'Project team', x: 6, y: 65, w: 6, h: 10, min_h: 6 },
];
const otherDataWidgetIds = ['custom-kpis', 'meetings', 'activity', 'audit', 'project-team'];

test.use({ storageState: { cookies: [], origins: [] }, screenshot: 'off' });

async function openDashboard(page: Page, initialPositions: Record<string, Partial<DashboardWidgetPosition>> = {}, includeMinWidth = true, extraCatalog: typeof catalog = [], workspaceMode: 'success' | 'pending' | 'error' = 'success') {
  let releaseWorkspace = () => {};
  const workspaceGate = new Promise<void>((resolve) => { releaseWorkspace = resolve; });
  const user = { ...DEFAULT_MESSAGES_E2E_USER, current_organization: project.organization };
  await installApiMockSafetyNet(page);
  await seedAuthenticatedUser(page, user);
  await mockAuthenticatedUserApis(page, user);
  await mockProjectShellApis(page);
  await seedActiveProject(page, project);
  await page.route('**/api/core/projects**', async (route) => {
    if (!/\/api\/core\/projects\/?$/.test(new URL(route.request().url()).pathname)) {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([project]) });
  });
  await page.route('**/api/core/organizations/dashboard-test-org/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project.organization) });
  });
  await page.route('**/api/core/projects/med-287/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project) });
  });
  await page.route('**/api/dashboard/summary/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      recent_activity: [],
      time_metrics: { completed_last_7_days: 0, updated_last_7_days: 0, created_last_7_days: 0, due_soon: 0 },
      status_overview: { total_work_items: 0, breakdown: [] },
      priority_breakdown: [], types_of_work: [],
    }) });
  });
  await page.route('**/api/dashboard/workspace/**', async (route) => {
    if (workspaceMode === 'pending') await workspaceGate;
    if (workspaceMode === 'error') {
      await route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'You do not have permission' }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      decisions: [], tasks: [], spreadsheets: [], patterns: [],
    }) });
  });
  const activeCatalog = [...catalog, ...extraCatalog];
  let persisted: DashboardWidgetPosition[] = activeCatalog.map(({ id, x, y, w, h }) => ({ id, x, y, w, h, ...initialPositions[id] }));
  let writes = 0;
  let reads = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') {
      writes += 1;
      persisted = route.request().postDataJSON().widgets;
    } else if (route.request().method() === 'GET') {
      reads += 1;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: project.id, project_slug: project.slug, widgets: persisted,
      catalog: activeCatalog, columns: 12, ...(includeMinWidth ? { min_width: 3 } : {}), max_rows: 200, updated_at: null,
    }) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible();
  return { saved: () => persisted, writes: () => writes, reads: () => reads, releaseWorkspace };
}

test('Overview navigation opens the resizable grid at the original route', async ({ page }) => {
  await openDashboard(page);
  await page.getByRole('button', { name: 'Overview', exact: true }).click();
  await expect(page).toHaveURL(/\/overview$/, { timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible();
});

test('Overview hash scroll reaches the Project team widget', async ({ page }) => {
  await openDashboard(page, {}, true, [
    { id: 'project-team', label: 'Project team', x: 0, y: 65, w: 6, h: 10, min_h: 4 },
  ]);
  await page.evaluate(() => {
    sessionStorage.setItem('overview-pending-section', 'project-team');
    window.location.hash = 'project-team';
  });
  await expect.poll(() => page.locator('main.dashboard-scrollbar').evaluate((main) => main.scrollTop)).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('overview-pending-section'))).toBeNull();
});

test('the grid loads while workspace cards are still loading', async ({ page }) => {
  const layout = await openDashboard(page, {}, true, otherDataCatalog, 'pending');
  expect(layout.reads()).toBeGreaterThan(0);
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toContainText('Loading workspace…');
  for (const id of otherDataWidgetIds) await expect(page.locator(`[data-widget-id="${id}"]`)).toBeVisible();
  layout.releaseWorkspace();
  await expect(page.locator('[data-widget-id="decisions-summary"]')).not.toContainText('Loading workspace…');
});

test('workspace errors leave the grid and unrelated cards available', async ({ page }) => {
  await openDashboard(page, {}, true, otherDataCatalog, 'error');
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toContainText('Could not load workspace');
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toContainText('You do not have permission');
  for (const id of otherDataWidgetIds) await expect(page.locator(`[data-widget-id="${id}"]`)).toBeVisible();
});

test('resizing past the display margin can push cards farther down', async ({ page }) => {
  const layout = await openDashboard(page, {
    audit: { x: 0, y: 15 },
    meetings: { x: 0, y: 21 },
    activity: { x: 0, y: 29 },
  });
  const resize = page.getByRole('button', { name: 'Resize Overall progress' });
  const box = (await resize.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 10 * 32, { steps: 10 });
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'overall-progress')?.h).toBe(15);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'activity')?.y).toBe(39);
});

test('a catalog entry without a renderer does not occupy invisible grid rows', async ({ page }) => {
  const warnings: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'warning') warnings.push(message.text());
  });
  await openDashboard(page, {}, true, [
    { id: 'missing-renderer', label: 'Missing renderer', x: 0, y: 80, w: 6, h: 8, min_h: 4 },
  ]);
  await expect(page.locator('[data-widget-id="missing-renderer"]')).toHaveCount(0);
  const grid = page.locator('[data-widget-id="audit"]').locator('..');
  expect(await grid.evaluate((element) => element.getBoundingClientRect().height)).toBe(71 * 32 - 8);
  expect(warnings.some((message) => message.includes('missing-renderer'))).toBe(true);
});

test('moving and resizing cards saves their layout after each gesture', async ({ page }) => {
  const layout = await openDashboard(page);
  const readsBeforeMove = layout.reads();
  const move = page.getByRole('button', { name: 'Move Audit' });
  await move.scrollIntoViewIfNeeded();
  const moveBox = (await move.boundingBox())!;
  const startX = moveBox.x + moveBox.width / 2;
  const startY = moveBox.y + moveBox.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX, startY + 110, { steps: 8 });
  await expect(page.getByTestId('dashboard-drop-preview')).toBeVisible();
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect(page.getByTestId('dashboard-drop-preview')).toHaveCount(0);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.y).toBeGreaterThan(15);
  await expect(page.getByRole('status').getByText('Saved')).toBeVisible();
  expect(layout.reads()).toBe(readsBeforeMove);

  const resize = page.getByRole('button', { name: 'Resize Audit' });
  await resize.scrollIntoViewIfNeeded();
  const resizeBox = (await resize.boundingBox())!;
  const x = resizeBox.x + resizeBox.width / 2;
  const y = resizeBox.y + resizeBox.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 70, { steps: 8 });
  await expect(page.locator('[data-widget-id="audit"]')).toHaveCSS('grid-row-end', /span (8|9)/);
  const before = layout.writes();
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.h).toBeGreaterThan(6);
  expect(layout.writes()).toBe(before + 1);

  const saved = layout.saved().find((widget) => widget.id === 'audit')!;
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible({ timeout: 15_000 });
  expect(layout.saved().find((widget) => widget.id === 'audit')).toEqual(saved);
  await expect(page.locator('[data-widget-id="audit"]')).toHaveCSS('grid-row-start', String(saved.y + 1));
});

test('cards pushed down by a tall overview card return when it shrinks', async ({ page }) => {
  const layout = await openDashboard(page);
  const resize = page.getByRole('button', { name: 'Resize Task completion rate' });
  const changeHeight = async (pixels: number) => {
    const box = (await resize.boundingBox())!;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y + pixels, { steps: 8 });
    await page.mouse.up();
  };

  await changeHeight(7 * 32);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.h).toBe(12);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(12);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'tasks-summary')?.y).toBe(12);

  await changeHeight(-7 * 32);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.h).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'tasks-summary')?.y).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'operations-summary')?.y).toBe(5);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toHaveCSS('grid-row-start', '6');
});

test('cards pushed down by an overview card return when it moves back up', async ({ page }) => {
  const layout = await openDashboard(page);
  const move = page.getByRole('button', { name: 'Move Task completion rate' });
  const moveRows = async (pixels: number) => {
    const box = (await move.boundingBox())!;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y + pixels, { steps: 8 });
    await page.mouse.up();
  };

  await moveRows(7 * 32);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.y).toBe(7);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(12);

  await moveRows(-7 * 32);
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.y).toBe(0);
  expect(layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'tasks-summary')?.y).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'operations-summary')?.y).toBe(5);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toHaveCSS('grid-row-start', '6');
});

test('an older layout response without a width limit cannot corrupt a resized card', async ({ page }) => {
  const layout = await openDashboard(page, {}, false);
  const resize = page.getByRole('button', { name: 'Resize Overall progress' });
  const box = (await resize.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 100, box.y + box.height / 2, { steps: 8 });
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'overall-progress')?.w).toBeGreaterThan(3);
  await expect(page.locator('[data-widget-id="overall-progress"]')).not.toHaveAttribute('style', /NaN/);
});

test('dragging the last Audit card down keeps the dashboard height steady', async ({ page }) => {
  const layout = await openDashboard(page, {
    audit: { y: 55 },
    meetings: { y: 15 },
  });
  const move = page.getByRole('button', { name: 'Move Audit' });
  await move.scrollIntoViewIfNeeded();
  const box = (await move.boundingBox())!;
  const startX = box.x + box.width / 2;
  const startY = box.y + box.height / 2;
  const grid = page.locator('[data-widget-id="audit"]').locator('..');
  const initialHeight = await grid.evaluate((element) => element.getBoundingClientRect().height);

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  for (const offset of [45, 90, 130, 170]) {
    await page.mouse.move(startX, Math.min(760, startY + offset), { steps: 3 });
    await expect(page.getByTestId('dashboard-drop-preview')).toBeVisible();
    expect(await grid.evaluate((element) => element.getBoundingClientRect().height)).toBe(initialHeight);
  }
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.y).toBeGreaterThan(55);
  await expect(page.locator('[data-widget-id="audit"]')).toBeVisible();
});

test('moving the last card up releases the unused rows below it', async ({ page }) => {
  const layout = await openDashboard(page, { audit: { y: 65 } });
  const move = page.getByRole('button', { name: 'Move Audit' });
  const grid = page.locator('[data-widget-id="audit"]').locator('..');
  const initialHeight = await grid.evaluate((element) => element.getBoundingClientRect().height);

  await move.scrollIntoViewIfNeeded();
  const box = (await move.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y - 32, { steps: 8 });
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.y).toBe(64);
  await expect.poll(() => grid.evaluate((element) => element.getBoundingClientRect().height)).toBe(initialHeight - 32);
});

test('restoring a compact layout does not reserve the catalog default height', async ({ page }) => {
  await openDashboard(page, { meetings: { y: 21 }, audit: { y: 29 } });
  const grid = page.locator('[data-widget-id="audit"]').locator('..');
  const compactHeight = await grid.evaluate((element) => element.getBoundingClientRect().height);
  expect(compactHeight).toBeLessThan(1800);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => grid.evaluate((element) => element.getBoundingClientRect().height)).toBe(compactHeight);
});

test('Audit keeps moving down when later cards must move beyond the initial bottom', async ({ page }) => {
  const layout = await openDashboard(page, {
    audit: { x: 1, y: 55 },
    activity: { x: 0, y: 61 },
    meetings: { x: 0, y: 67 },
  });
  const move = page.getByRole('button', { name: 'Move Audit' });
  const scroller = page.locator('main.dashboard-scrollbar');
  await move.scrollIntoViewIfNeeded();
  const box = (await move.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 20, { steps: 4 });
  await scroller.evaluate((element) => { element.scrollTop += 8 * 32; });
  await page.mouse.move(x, y + 21);

  const preview = page.getByTestId('dashboard-drop-preview');
  await expect(preview).toBeVisible();
  await expect.poll(() => preview.evaluate((element) => Number(getComputedStyle(element).gridRowStart))).toBeGreaterThan(56);
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.y).toBeGreaterThan(55);
  expect(Math.max(...layout.saved().map((widget) => widget.y + widget.h))).toBeGreaterThan(83);
});

test('a project overview statistic moves without moving its neighbor', async ({ page }) => {
  const layout = await openDashboard(page);
  const move = page.getByRole('button', { name: 'Move Overall progress' });
  const box = (await move.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 160, { steps: 8 });
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'overall-progress')?.y).toBeGreaterThan(0);
  expect(layout.saved().find((widget) => widget.id === 'task-completion-rate')?.y).toBe(0);
});

test('dropping a card onto another card keeps it at the drop position', async ({ page }) => {
  const layout = await openDashboard(page);
  const move = page.getByRole('button', { name: 'Move Overall progress' });
  const box = (await move.boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 33, { steps: 8 });
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'overall-progress')?.y).toBe(1);
  expect(layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(6);
  expect(layout.saved().map((widget) => widget.id).sort()).toEqual(catalog.map((widget) => widget.id).sort());
  await expect(page.locator('[data-widget-id="overall-progress"]')).toHaveCSS('grid-row-start', '2');
});

test('same-size cards swap positions when one is dropped on the other', async ({ page }) => {
  const layout = await openDashboard(page);
  const source = (await page.getByRole('button', { name: 'Move Task completion rate' }).boundingBox())!;
  const target = (await page.getByRole('button', { name: 'Move Overall progress' }).boundingBox())!;
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 8 });
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.x).toBe(0);
  expect(layout.saved().find((widget) => widget.id === 'overall-progress')?.x).toBe(3);
  expect(layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(5);
});

test('a card at the far right can be dropped onto the leftmost card', async ({ page }) => {
  const layout = await openDashboard(page, { 'task-completion-rate': { x: 9 } });
  const source = (await page.getByRole('button', { name: 'Move Task completion rate' }).boundingBox())!;
  const target = (await page.getByRole('button', { name: 'Move Overall progress' }).boundingBox())!;
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 16 });
  await expect(page.getByTestId('dashboard-drop-preview')).toHaveCSS('grid-column-start', '1');
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.x).toBe(0);
  expect(layout.saved().find((widget) => widget.id === 'overall-progress')?.x).toBe(9);
});

test('dropping into the Decisions column moves Decisions while Tasks and Operations stay put', async ({ page }) => {
  const layout = await openDashboard(page);
  const source = (await page.getByRole('button', { name: 'Move Task completion rate' }).boundingBox())!;
  const target = (await page.getByRole('button', { name: 'Move Decisions' }).boundingBox())!;
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 8 });
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toHaveCSS('transform', 'none');
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-completion-rate')?.y).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'task-completion-rate')?.x).toBe(0);
  expect(layout.saved().find((widget) => widget.id === 'decisions-summary')?.y).toBe(10);
  expect(layout.saved().find((widget) => widget.id === 'tasks-summary')?.y).toBe(5);
  expect(layout.saved().find((widget) => widget.id === 'operations-summary')?.y).toBe(5);
  await expect(page.locator('[data-widget-id="decisions-summary"]')).toHaveCSS('transition-duration', '0s');
});

test('drag and resize handles sit inside the native card', async ({ page }) => {
  await openDashboard(page);
  await expect(page.getByRole('heading', { name: 'Project Overview' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Module Summary' })).toHaveCount(0);
  const widget = page.locator('[data-widget-id="overall-progress"]');
  const moveButton = page.getByRole('button', { name: 'Move Overall progress' });
  const resizeButton = page.getByRole('button', { name: 'Resize Overall progress' });
  await expect(moveButton).toHaveCSS('opacity', '0');
  await expect(resizeButton).toHaveCSS('opacity', '0');
  await widget.hover();
  await expect(moveButton).toHaveCSS('opacity', '1');
  await expect(resizeButton).toHaveCSS('opacity', '1');
  const card = (await page.locator('[data-widget-id="overall-progress"] > div > div > div').first().boundingBox())!;
  const move = (await moveButton.boundingBox())!;
  const resize = (await resizeButton.boundingBox())!;
  expect(move.y).toBeGreaterThan(card.y);
  expect(move.x + move.width).toBeLessThanOrEqual(card.x + card.width);
  expect(resize.x + resize.width).toBeLessThanOrEqual(card.x + card.width);
  expect(resize.y + resize.height).toBeLessThanOrEqual(card.y + card.height);
});

test('a card can be dragged across the scrolling dashboard without passing its bottom edge', async ({ page }) => {
  const layout = await openDashboard(page);
  const main = page.locator('main.dashboard-scrollbar');
  const source = (await page.getByRole('button', { name: 'Move Overall progress' }).boundingBox())!;
  const x = source.x + source.width / 2;
  await page.mouse.move(x, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, 780, { steps: 12 });
  await expect.poll(() => main.evaluate((element) => element.scrollTop), { timeout: 10_000 }).toBeGreaterThan(800);
  const preview = page.getByTestId('dashboard-drop-preview');
  await expect(preview).toBeVisible();
  const dragBounds = await page.evaluate(() => {
    const active = document.querySelector('[data-widget-id="overall-progress"]')!.getBoundingClientRect();
    const grid = document.querySelector('[data-widget-id="overall-progress"]')!.parentElement!.getBoundingClientRect();
    return { cardBottom: active.bottom, limitBottom: grid.top + 71 * 32 - 8 };
  });
  expect(dragBounds.cardBottom).toBeLessThanOrEqual(dragBounds.limitBottom + 1);
  // Auto-scroll can advance between two Playwright calls. Read the preview
  // when the pointer is released, before dnd-kit removes it from the DOM.
  await page.evaluate(() => {
    window.addEventListener('pointerup', () => {
      const preview = document.querySelector('[data-testid="dashboard-drop-preview"]');
      if (!preview) return;
      const style = getComputedStyle(preview);
      document.documentElement.dataset.previewEndAtRelease = String(
        Number(style.gridRowStart) + Number(style.gridRowEnd.replace('span ', '')) - 1,
      );
    }, { capture: true, once: true });
  });
  await page.mouse.up();

  await expect.poll(() => layout.saved().find((widget) => widget.id === 'overall-progress')?.y).toBeGreaterThan(15);
  const savedCard = layout.saved().find((widget) => widget.id === 'overall-progress')!;
  const previewEnd = await page.evaluate(() => Number(document.documentElement.dataset.previewEndAtRelease));
  expect(previewEnd).toBeLessThanOrEqual(71);
  expect(savedCard.y + savedCard.h).toBe(previewEnd);
  expect(layout.saved().every((widget) => widget.y + widget.h <= 71)).toBe(true);
});

test('a bottom card stays under the pointer while dragged upward through the scroll area', async ({ page }) => {
  await openDashboard(page);
  const card = page.locator('[data-widget-id="meetings"]');
  const handle = page.getByRole('button', { name: 'Move Meetings & action items' });
  const scroller = page.locator('main.dashboard-scrollbar');
  await handle.scrollIntoViewIfNeeded();
  const handleBox = (await handle.boundingBox())!;
  const cardBox = (await card.boundingBox())!;
  const x = handleBox.x + handleBox.width / 2;
  const startY = handleBox.y + handleBox.height / 2;
  const gripOffset = startY - cardBox.y;
  await page.mouse.move(x, startY);
  await page.mouse.down();
  await page.mouse.move(x, 100, { steps: 4 });
  await expect(page.getByTestId('dashboard-drop-preview')).toBeVisible();
  const scrollBefore = await scroller.evaluate((element) => element.scrollTop);
  await page.waitForTimeout(250);
  const scrollAfter = await scroller.evaluate((element) => element.scrollTop);
  const metrics = await page.evaluate(() => {
    const card = document.querySelector('[data-widget-id="meetings"]')!;
    const scroller = document.querySelector('main.dashboard-scrollbar')!;
    return { scrollTop: scroller.scrollTop, transform: getComputedStyle(card).transform,
      cardTop: card.getBoundingClientRect().top, gridTop: card.parentElement!.getBoundingClientRect().top };
  });
  expect(scrollAfter).toBeLessThan(scrollBefore);
  expect(scrollBefore - scrollAfter).toBeLessThan(160);
  expect(Math.abs(metrics.cardTop - Math.max(metrics.gridTop, 100 - gripOffset))).toBeLessThan(24);
  await page.mouse.up();
});

test('dragging upward across a tall card does not snap the preview to its top', async ({ page }) => {
  await openDashboard(page, { 'decisions-summary': { x: 0, y: 40, w: 6, h: 10 } });
  const handle = page.getByRole('button', { name: 'Move Meetings & action items' });
  await handle.scrollIntoViewIfNeeded();
  const source = (await handle.boundingBox())!;
  const target = (await page.locator('[data-widget-id="decisions-summary"]').boundingBox())!;
  const x = source.x + source.width / 2;
  const startY = source.y + source.height / 2;
  const endY = target.y + target.height - 20;
  await page.mouse.move(x, startY);
  await page.mouse.down();
  await page.mouse.move(x, endY, { steps: 10 });

  const preview = page.getByTestId('dashboard-drop-preview');
  await expect(preview).toBeVisible();
  const previewRow = await preview.evaluate((element) => Number(getComputedStyle(element).gridRowStart));
  expect(previewRow).toBeGreaterThanOrEqual(47);
  await page.mouse.up();
});
