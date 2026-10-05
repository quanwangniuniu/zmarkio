/** MED-287: completed drag and resize gestures persist across reloads. */
import { expect, test, type Page } from '@playwright/test';
import type { DashboardWidgetPosition } from '../../src/types/dashboardLayout';
import {
  installApiMockSafetyNet, mockAuthenticatedUserApis, mockProjectShellApis,
  seedActiveProject, seedAuthenticatedUser, waitForLayoutMain,
} from '../messages/messages-helpers';

const project = {
  id: 287, slug: 'med-287', name: 'Dashboard Builder Test', is_active: true,
  organization: { id: 28, name: 'Dashboard Test Org', slug: 'dashboard-test-org' },
};
const initial = [
  { id: 'audit', x: 0, y: 0, w: 6, h: 5 },
  { id: 'activity', x: 6, y: 0, w: 6, h: 5 },
  { id: 'meetings', x: 6, y: 10, w: 6, h: 5 },
];

test.use({ storageState: { cookies: [], origins: [] } });

async function openDashboard(page: Page, widgets: DashboardWidgetPosition[] = initial) {
  await installApiMockSafetyNet(page);
  await seedAuthenticatedUser(page);
  await mockAuthenticatedUserApis(page);
  await mockProjectShellApis(page);
  await seedActiveProject(page, project);
  await page.route('**/api/core/projects**', async (route) => {
    if (!/\/api\/core\/projects\/?$/.test(new URL(route.request().url()).pathname)) {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([project]) });
  });
  await page.route('**/api/dashboard/summary/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      recent_activity: [], time_metrics: { completed_last_7_days: 0, updated_last_7_days: 0, created_last_7_days: 0, due_soon: 0 },
      status_overview: { total_work_items: 0, breakdown: [] }, priority_breakdown: [], types_of_work: [],
    }) });
  });
  await page.route('**/api/dashboard/workspace/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      decisions: [], tasks: [], spreadsheets: [], patterns: [],
    }) });
  });
  let persisted = widgets.map((widget) => ({ ...widget }));
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') {
      writes += 1;
      persisted = route.request().postDataJSON().widgets;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: project.id, project_slug: project.slug, widgets: persisted,
    }) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  return { saved: () => persisted, writes: () => writes };
}

test('enables layout controls and title editing only while Customize is on', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'section-title-overview', title: 'Project overview', x: 0, y: 0, w: 12, h: 1 },
    { id: 'activity', x: 0, y: 1, w: 6, h: 4 },
  ]);
  const toggle = page.getByRole('button', { name: 'Customize', exact: true });
  const tile = page.getByTestId('dashboard-widget-activity');
  const heading = tile.getByText('Recent Activity', { exact: true });
  const icon = tile.locator('svg.lucide-clipboard-list');
  const controls = page.getByRole('button', { name: /^(Move|Resize|Remove) / });
  const titleInput = page.getByRole('textbox', { name: 'Section title text' });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByRole('heading', { name: 'Project overview', exact: true })).toBeVisible();
  await tile.hover();
  await expect(controls).toHaveCount(0);
  await expect(titleInput).toHaveCount(0);
  await expect(page.getByLabel('Widget picker')).toHaveCount(0);
  const headingX = (await heading.boundingBox())!.x;
  const iconX = (await icon.boundingBox())!.x;
  const from = (await heading.boundingBox())!;
  await page.mouse.move(from.x + 20, from.y + 5); await page.mouse.down();
  await page.mouse.move(from.x + 100, from.y + 65, { steps: 8 }); await page.mouse.up();
  expect(layout.writes()).toBe(0);
  expect(layout.saved()[1]).toMatchObject({ x: 0, y: 1 });

  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByLabel('Widget picker')).toBeVisible();
  await expect(tile.getByRole('button', { name: 'Move Recent activity', exact: true })).toBeAttached();
  await expect(tile.getByRole('button', { name: 'Resize Recent activity', exact: true })).toBeAttached();
  await expect(tile.getByRole('button', { name: 'Remove Recent activity', exact: true })).toBeAttached();
  expect((await heading.boundingBox())!.x).toBeCloseTo(headingX, 0);
  expect((await icon.boundingBox())!.x).toBeCloseTo(iconX, 0);
  await titleInput.fill('My overview');
  await titleInput.press('Enter');
  await expect.poll(() => layout.saved()[0].title).toBe('My overview');
  await toggle.click();
  await tile.hover();
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await expect(controls).toHaveCount(0);
  await expect(titleInput).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'My overview', exact: true })).toBeVisible();
  expect((await heading.boundingBox())!.x).toBeCloseTo(headingX, 0);
  expect((await icon.boundingBox())!.x).toBeCloseTo(iconX, 0);
  expect(await icon.evaluate((element) => {
    for (let node: Element | null = element; node; node = node.parentElement) {
      if (getComputedStyle(node).opacity === '0') return false;
    }
    return true;
  })).toBe(true);
  await expect(tile.getByRole('button', { name: 'View all activity', exact: true })).toBeVisible();
  expect(layout.writes()).toBe(1);
});

test('cancels an active resize when Customize is turned off without saving the preview', async ({ page }) => {
  const layout = await openDashboard(page, [{ id: 'audit', x: 0, y: 0, w: 6, h: 4 }]);
  const toggle = page.getByRole('button', { name: 'Customize', exact: true });
  await toggle.click();
  const tile = page.getByTestId('dashboard-widget-audit');
  const before = (await tile.boundingBox())!;
  const handle = (await tile.getByRole('button', { name: 'Resize Audit', exact: true }).boundingBox())!;
  const x = handle.x + handle.width / 2;
  const y = handle.y + handle.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + 50, y + 50, { steps: 8 });
  await expect.poll(async () => (await tile.boundingBox())!.height).toBeCloseTo(before.height + 50, 0);
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await page.mouse.up();
  await expect.poll(async () => (await tile.boundingBox())!.width).toBeCloseTo(before.width, 0);
  await expect.poll(async () => (await tile.boundingBox())!.height).toBeCloseTo(before.height, 0);
  expect(layout.writes()).toBe(0);
  await expect(tile.getByRole('button', { name: 'Resize Audit' })).toHaveCount(0);
  await toggle.click();
  await tile.getByRole('button', { name: 'Resize Audit' }).press('ArrowDown');
  await expect.poll(() => layout.writes()).toBe(1);
});

test('cancels a drag with Escape and exits Customize without saving the preview', async ({ page }) => {
  const original = initial.map((widget) => widget.id === 'audit' ? { ...widget, w: 4 } : widget);
  const layout = await openDashboard(page, original);
  const toggle = page.getByRole('button', { name: 'Customize', exact: true });
  await toggle.click();
  const tile = page.getByTestId('dashboard-widget-activity');
  const before = (await tile.boundingBox())!;
  const grip = (await tile.getByRole('button', { name: 'Move Recent activity', exact: true }).boundingBox())!;
  const x = grip.x + grip.width / 2;
  const y = grip.y + grip.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x - 50, y + 10, { steps: 8 });
  await expect.poll(async () => (await tile.boundingBox())!.x).toBeCloseTo(before.x - 50, 0);
  // The pointer sensor suppresses clicks during a drag; cancel it first.
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await toggle.focus();
  // dnd-kit retains click suppression for 50ms after canceling a pointer drag.
  await page.keyboard.press('Space', { delay: 60 });
  await expect(toggle).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => (await tile.boundingBox())!.x).toBeCloseTo(before.x, 0);
  await expect.poll(async () => (await tile.boundingBox())!.y).toBeCloseTo(before.y, 0);
  expect(layout.saved()).toEqual(original);
  expect(layout.writes()).toBe(0);
});

test('repositions a widget and autosaves only after the drag ends', async ({ page }) => {
  const layout = await openDashboard(page);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const activity = page.getByRole('button', { name: 'Move Recent activity', exact: true });
  const from = await activity.boundingBox();
  const to = await page.getByRole('button', { name: 'Move Audit', exact: true }).boundingBox();
  expect(from).not.toBeNull();
  expect(to).not.toBeNull();
  await page.mouse.move(from!.x + 20, from!.y + 20);
  await page.mouse.down();
  await page.mouse.move(to!.x + 20, to!.y + 20, { steps: 12 });
  await expect.poll(() => page.getByTestId('dashboard-widget-activity').evaluate((element) => getComputedStyle(element).left)).toBe('0px');
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  expect(layout.saved().find((widget) => widget.id === 'activity')).toMatchObject({ x: 0, y: 0 });
  await page.reload();
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  await expect(activity).toBeVisible();
  expect((await activity.boundingBox())!.x).toBeLessThan(from!.x);
});

test('persists a resized widget after the pointer is released', async ({ page }) => {
  const layout = await openDashboard(page);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-audit');
  const handle = tile.getByRole('button', { name: 'Resize Audit' });
  await handle.scrollIntoViewIfNeeded();
  const before = await tile.boundingBox();
  const box = await handle.boundingBox();
  expect(before).not.toBeNull();
  expect(box).not.toBeNull();
  const x = box!.x + box!.width / 2;
  const y = box!.y + box!.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x, y + 60, { steps: 8 });
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.h).toBe(6);
  expect(layout.writes()).toBe(1);
  await page.reload();
  await expect.poll(async () => (await tile.boundingBox())!.height).toBeGreaterThan(before!.height);
});

test('moves Overdue Tasks left into the gap and stops at its neighbor without pushing it down', async ({ page }) => {
  const original = [
    { id: 'task-completion-rate', x: 0, y: 0, w: 2.7, h: 3 },
    { id: 'overdue-tasks', x: 3, y: 0, w: 2.5, h: 3 },
    { id: 'audit', x: 0, y: 8, w: 6, h: 4 },
  ];
  const layout = await openDashboard(page, original);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-overdue-tasks');
  const before = (await tile.boundingBox())!;
  const completion = (await page.getByTestId('dashboard-widget-task-completion-rate').boundingBox())!;
  const grip = (await tile.getByRole('button', { name: 'Move Overdue Tasks', exact: true }).boundingBox())!;
  const x = grip.x + grip.width / 2;
  const y = grip.y + grip.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x - 10, y, { steps: 4 });
  await expect.poll(async () => (await tile.boundingBox())!.x).toBeCloseTo(before.x - 10, 0);
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  const after = (await tile.boundingBox())!;
  expect(after.y).toBeCloseTo(before.y, 0);
  expect(after.x - completion.x - completion.width).toBeGreaterThanOrEqual(12 - 0.5);
  expect(layout.saved().find((widget) => widget.id === 'task-completion-rate')).toEqual(original[0]);
  expect(layout.saved().find((widget) => widget.id === 'audit')).toEqual({ ...original[2], y: 3 });
  const nextGrip = (await tile.getByRole('button', { name: 'Move Overdue Tasks', exact: true }).boundingBox())!;
  const nextX = nextGrip.x + nextGrip.width / 2;
  const nextY = nextGrip.y + nextGrip.height / 2;
  await page.mouse.move(nextX, nextY); await page.mouse.down();
  await page.mouse.move(nextX - 40, nextY, { steps: 8 });
  await expect.poll(async () => (await tile.boundingBox())!.x - completion.x - completion.width).toBeCloseTo(12, 0);
  expect(layout.writes()).toBe(1);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(2);
  expect(layout.saved().find((widget) => widget.id === 'task-completion-rate')).toEqual(original[0]);
  expect(layout.saved().find((widget) => widget.id === 'audit')).toEqual({ ...original[2], y: 3 });
  await page.reload();
  await expect.poll(async () => (await tile.boundingBox())!.x - completion.x - completion.width).toBeCloseTo(12, 0);
});

test('resizes by 10px on both axes and retains that fine size after reload', async ({ page }) => {
  const layout = await openDashboard(page, [{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-audit');
  const before = (await tile.boundingBox())!;
  const handle = (await tile.getByRole('button', { name: 'Resize Audit' }).boundingBox())!;
  const x = handle.x + handle.width / 2;
  const y = handle.y + handle.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + 10, y + 10, { steps: 4 });
  await expect.poll(async () => (await tile.boundingBox())!.width).toBeCloseTo(before.width + 10, 0);
  await expect.poll(async () => (await tile.boundingBox())!.height).toBeCloseTo(before.height + 10, 0);
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  expect(Number.isInteger(layout.saved()[0].w)).toBe(false);
  expect(Number.isInteger(layout.saved()[0].h)).toBe(false);
  await page.reload();
  await expect.poll(async () => (await tile.boundingBox())!.width).toBeCloseTo(before.width + 10, 0);
  await expect.poll(async () => (await tile.boundingBox())!.height).toBeCloseTo(before.height + 10, 0);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  await tile.getByRole('button', { name: 'Resize Audit' }).press('ArrowDown');
  await expect.poll(async () => (await tile.boundingBox())!.height).toBeCloseTo(before.height + 20, 0);
});

test('closes the blank space above a section title and shifts its content after editing a card', async ({ page }) => {
  const original = [
    { id: 'audit', x: 0, y: 0, w: 5.5, h: 4 },
    { id: 'activity', x: 6, y: 0, w: 6, h: 5 },
    { id: 'section-title-modules', title: 'Modules', x: 0, y: 12, w: 12, h: 1 },
    { id: 'tasks', x: 0, y: 13, w: 4, h: 7 },
    { id: 'operations', x: 4, y: 13, w: 4, h: 7 },
  ];
  const layout = await openDashboard(page, original);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const title = page.getByTestId('dashboard-widget-section-title-modules');
  const before = (await title.boundingBox())!;
  const handle = (await page.getByRole('button', { name: 'Move Audit', exact: true }).boundingBox())!;
  const x = handle.x + handle.width / 2;
  const y = handle.y + handle.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + 10, y, { steps: 4 });
  await expect.poll(async () => (await title.boundingBox())!.y).toBeCloseTo(before.y - 420, 0);
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  expect(layout.saved().find((widget) => widget.id === 'activity')).toEqual(original[1]);
  expect(layout.saved().find((widget) => widget.id === 'section-title-modules')).toEqual({ ...original[2], y: 5 });
  expect(layout.saved().find((widget) => widget.id === 'tasks')).toEqual({ ...original[3], y: 6 });
  expect(layout.saved().find((widget) => widget.id === 'operations')).toEqual({ ...original[4], y: 6 });
  await page.reload();
  await expect.poll(async () => (await title.boundingBox())!.y - (await page.getByTestId('dashboard-canvas').boundingBox())!.y).toBeCloseTo(300, 0);
});

test('ordinary cards and section titles follow the bottom edge during resize and after delete or reload', async ({ page }) => {
  const original = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 8 },
    { id: 'tasks', x: 6, y: 0, w: 6, h: 6 },
    { id: 'activity', x: 0, y: 10, w: 6, h: 4 },
    { id: 'meetings', x: 0, y: 18, w: 6, h: 4 },
    { id: 'section-title-next', title: 'Next section', x: 0, y: 25, w: 12, h: 1 },
    { id: 'custom-kpis', x: 0, y: 28, w: 12, h: 4 },
  ];
  const layout = await openDashboard(page, original);
  const toggle = page.getByRole('button', { name: 'Customize', exact: true });
  await toggle.click();
  const audit = page.getByTestId('dashboard-widget-audit');
  const activity = page.getByTestId('dashboard-widget-activity');
  const meetings = page.getByTestId('dashboard-widget-meetings');
  const title = page.getByTestId('dashboard-widget-section-title-next');
  const kpis = page.getByTestId('dashboard-widget-custom-kpis');
  const gapAfter = (below: typeof activity, aboveId: string) => below.evaluate((element, id) =>
    element.getBoundingClientRect().top - document.querySelector(`[data-testid="dashboard-widget-${id}"]`)!.getBoundingClientRect().bottom, aboveId);
  const handle = audit.getByRole('button', { name: 'Resize Audit' });
  await handle.scrollIntoViewIfNeeded();
  const from = (await handle.boundingBox())!;
  const x = from.x + from.width / 2;
  const y = from.y + from.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x, y - 240, { steps: 16 });
  await expect.poll(() => gapAfter(activity, 'audit')).toBeCloseTo(12, 0);
  await expect.poll(() => gapAfter(meetings, 'activity')).toBeCloseTo(12, 0);
  await expect.poll(() => gapAfter(title, 'meetings')).toBeCloseTo(12, 0);
  await expect.poll(() => gapAfter(kpis, 'section-title-next')).toBeCloseTo(12, 0);
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  expect(layout.saved().find((widget) => widget.id === 'activity')).toEqual({ ...original[2], y: 4 });
  expect(layout.saved().find((widget) => widget.id === 'meetings')).toEqual({ ...original[3], y: 8 });
  expect(layout.saved().find((widget) => widget.id === 'tasks')).toEqual(original[1]);
  await page.reload();
  await expect.poll(() => gapAfter(activity, 'audit')).toBeCloseTo(12, 0);
  await expect.poll(() => gapAfter(meetings, 'activity')).toBeCloseTo(12, 0);
  await toggle.click();
  await audit.getByRole('button', { name: 'Remove Audit' }).click();
  await expect(audit).toHaveCount(0);
  await expect.poll(() => layout.writes()).toBe(2);
  expect(layout.saved().find((widget) => widget.id === 'activity')?.y).toBe(0);
  expect(layout.saved().find((widget) => widget.id === 'meetings')?.y).toBe(4);
  expect(layout.saved().find((widget) => widget.id === 'section-title-next')?.y).toBe(8);
  await expect.poll(() => gapAfter(title, 'meetings')).toBeCloseTo(12, 0);
});

test('resizes Type breakdown repeatedly while its handle moves with the preview', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'task-types', x: 0, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 4, y: 0, w: 4, h: 8 },
  ]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-task-types');
  const handle = tile.getByRole('button', { name: 'Resize Type breakdown' });
  const initialWidth = (await tile.boundingBox())!.width;

  for (const [change, expectedWidth, expectedWrites] of [[160, initialWidth + 160, 1], [-160, initialWidth, 2]] as const) {
    await handle.scrollIntoViewIfNeeded();
    const box = await handle.boundingBox();
    expect(box).not.toBeNull();
    const x = box!.x + box!.width / 2;
    const y = box!.y + box!.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + change, y, { steps: 12 });
    await expect.poll(async () => (await tile.boundingBox())!.width).toBeCloseTo(expectedWidth, 0);
    expect(layout.writes()).toBe(expectedWrites - 1);
    await page.mouse.up();
    await expect.poll(() => layout.writes()).toBe(expectedWrites);
    expect(layout.writes()).toBe(expectedWrites);
  }
  await page.reload();
  await expect.poll(async () => (await tile.boundingBox())!.width).toBeCloseTo(initialWidth, 0);
});

test('removes a widget from the canvas after resizing and keeps it removed after reload', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'task-types', x: 0, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 4, y: 0, w: 4, h: 8 },
  ]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-task-types');
  const handle = tile.getByRole('button', { name: 'Resize Type breakdown' });
  await handle.scrollIntoViewIfNeeded();
  const box = await handle.boundingBox();
  const columnStep = ((await page.locator('.dashboard-builder-grid').boundingBox())!.width + 12) / 12;
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 + columnStep, box!.y + box!.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-types')?.w).toBeGreaterThan(4);

  await tile.hover();
  await tile.getByRole('button', { name: 'Remove Type breakdown' }).click();
  await expect(tile).toHaveCount(0);
  await expect(page.getByTestId('dashboard-widget-task-status')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add Type breakdown', exact: true })).toBeVisible();
  await expect.poll(() => layout.saved().some((widget) => widget.id === 'task-types')).toBe(false);
  await page.reload();
  await expect(tile).toHaveCount(0);
});

test('does not keep a removed widget visible in an interrupted resize preview', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'task-types', x: 0, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 4, y: 0, w: 4, h: 8 },
  ]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-task-types');
  await tile.getByRole('button', { name: 'Resize Type breakdown' }).scrollIntoViewIfNeeded();
  const box = await tile.getByRole('button', { name: 'Resize Type breakdown' }).boundingBox();
  const columnStep = ((await page.locator('.dashboard-builder-grid').boundingBox())!.width + 12) / 12;
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 + columnStep, box!.y + box!.height / 2, { steps: 8 });
  await tile.getByRole('button', { name: 'Remove Type breakdown' }).focus();
  await page.keyboard.press('Enter');
  await expect(tile).toHaveCount(0);
  await page.mouse.up();
  await expect(page.getByRole('button', { name: 'Add Type breakdown', exact: true })).toBeVisible();
  await expect.poll(() => layout.saved().some((widget) => widget.id === 'task-types')).toBe(false);
});

test('keeps the widget picker open while adding multiple widgets until manually collapsed', async ({ page }) => {
  const layout = await openDashboard(page, [{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }]);
  const toggle = page.getByRole('button', { name: 'Customize' });
  const picker = page.getByLabel('Widget picker');
  await toggle.click();
  await picker.getByRole('button', { name: 'Add Type breakdown', exact: true }).click();
  await expect(picker).toBeVisible();
  await expect(picker.getByRole('button', { name: 'Add Type breakdown', exact: true })).toHaveCount(0);
  await picker.getByRole('button', { name: 'Add Overall Progress', exact: true }).click();
  await expect(picker).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-task-types')).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-overall-progress')).toBeVisible();
  await expect.poll(() => layout.saved().filter((widget) => ['task-types', 'overall-progress'].includes(widget.id)).length).toBe(2);
  await toggle.click();
  await expect(picker).toHaveCount(0);
});

test('keeps the widget picker open after the last widget until manually collapsed', async ({ page }) => {
  const placedIds = [
    'overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks',
    'needs-attention', 'decisions', 'tasks', 'operations', 'task-status',
    'task-priority', 'task-trend', 'custom-kpis', 'meetings', 'activity', 'audit',
    'project-team',
  ];
  const layout = await openDashboard(page, placedIds.map((id, index) => ({
    id, x: (index % 3) * 4, y: Math.floor(index / 3) * 8, w: 4, h: 8,
  })));
  const toggle = page.getByRole('button', { name: 'Customize' });
  const picker = page.getByLabel('Widget picker');
  await toggle.click();
  await expect(picker.getByRole('button', { name: /^Add / })).toHaveCount(2);
  await picker.getByRole('button', { name: 'Add Type breakdown', exact: true }).click();
  await expect(picker).toBeVisible();
  await expect(page.getByRole('note')).toHaveText('Tips: Drag any component onto the dashboard to customize your layout. You can also rename section titles.');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await toggle.click();
  await expect(picker).toHaveCount(0);
  await expect.poll(() => layout.saved().some((widget) => widget.id === 'task-types')).toBe(true);
});

test('adds multiple section titles, edits and reloads their text, resizes and removes them independently', async ({ page }) => {
  const layout = await openDashboard(page, []);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const addTitle = page.getByRole('button', { name: 'Add Section title', exact: true });
  await addTitle.click();
  const titles = page.getByRole('textbox', { name: 'Section title text', exact: true });
  await titles.first().fill('Project overview');
  await titles.first().press('Enter');
  await addTitle.click();
  await titles.nth(1).fill('Recent updates');
  await titles.nth(1).press('Enter');
  await expect.poll(() => layout.saved().map((widget) => widget.title)).toEqual(['Project overview', 'Recent updates']);
  expect(new Set(layout.saved().map((widget) => widget.id)).size).toBe(2);
  await page.reload();
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  await expect(titles.first()).toHaveValue('Project overview');
  await expect(titles.nth(1)).toHaveValue('Recent updates');
  const resize = page.getByRole('button', { name: 'Resize Project overview', exact: true });
  await resize.press('ArrowLeft');
  await resize.press('ArrowUp');
  await expect.poll(() => layout.saved()[0].w).toBeLessThan(12);
  expect(layout.saved()[0].h).toBe(1);
  await page.getByRole('button', { name: 'Remove Project overview', exact: true }).click();
  await expect(titles).toHaveCount(1);
  await expect.poll(() => layout.saved().length).toBe(1);
  await page.reload();
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  await expect(titles).toHaveValue('Recent updates');
  await page.setViewportSize({ width: 390, height: 844 });
  expect((await page.locator('[data-section-title]').boundingBox())!.height).toBeLessThan(100);
});

test('drags a section title from the picker with a full-width one-row preview', async ({ page }) => {
  const layout = await openDashboard(page, []);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const source = page.getByRole('button', { name: 'Drag Section title onto dashboard', exact: true });
  const from = (await source.boundingBox())!;
  const canvas = (await page.getByTestId('dashboard-canvas').boundingBox())!;
  await page.mouse.move(from.x + 20, from.y + 10); await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + 20, { steps: 12 });
  const preview = page.getByTestId('widget-drop-preview');
  expect((await preview.boundingBox())!.width).toBeCloseTo(canvas.width, 0);
  expect((await preview.boundingBox())!.height).toBe(48);
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.saved().length).toBe(1);
  expect(layout.saved()[0]).toMatchObject({ title: 'Section title', x: 0, y: 0, w: 12, h: 1 });
  await expect(page.getByRole('textbox', { name: 'Section title text' })).toBeVisible();
  await expect(source).toBeVisible();
  const grip = page.getByRole('button', { name: 'Move Section title', exact: true });
  const handle = (await grip.boundingBox())!;
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2 + 120, { steps: 12 });
  await page.mouse.up();
  await expect(grip).toBeAttached();
  expect(layout.saved()[0].y).toBe(0);
  expect(layout.writes()).toBe(1);
});

test('shows the drag grip over a card icon without shifting its title', async ({ page }) => {
  await openDashboard(page);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const tile = page.getByTestId('dashboard-widget-activity');
  const title = tile.getByText('Recent Activity', { exact: true });
  const icon = tile.locator('svg.lucide-clipboard-list');
  const grip = tile.getByRole('button', { name: 'Move Recent activity', exact: true });
  const before = await title.boundingBox();
  const iconBox = await icon.boundingBox();
  expect(before).not.toBeNull();
  expect(iconBox).not.toBeNull();
  expect(await icon.locator('..').evaluate((element) => getComputedStyle(element).opacity)).toBe('1');

  await tile.hover();
  await expect.poll(() => grip.evaluate((element) => getComputedStyle(element).opacity)).toBe('1');
  await expect.poll(() => icon.locator('..').evaluate((element) => getComputedStyle(element).opacity)).toBe('0');
  const after = await title.boundingBox();
  const gripBox = await grip.boundingBox();
  expect(after!.x).toBeCloseTo(before!.x, 0);
  expect(Math.abs((gripBox!.x + gripBox!.width / 2) - (iconBox!.x + iconBox!.width / 2))).toBeLessThan(4);
});

test('keeps metric headings flush and places chart drag control on its section label', async ({ page }) => {
  await openDashboard(page, [
    { id: 'overall-progress', x: 0, y: 0, w: 4, h: 4 },
    { id: 'decisions', x: 4, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 8, y: 0, w: 4, h: 8 },
    { id: 'custom-kpis', x: 0, y: 4, w: 4, h: 8 },
    { id: 'activity', x: 4, y: 8, w: 4, h: 5 },
    { id: 'audit', x: 8, y: 8, w: 4, h: 5 },
  ]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const metric = page.getByTestId('dashboard-widget-overall-progress');
  const metricTitle = metric.getByText('Overall Progress', { exact: true });
  const metricValue = metric.getByText('0%', { exact: true });
  await expect(metricTitle).toBeVisible();
  expect(Math.abs((await metricTitle.boundingBox())!.x - (await metricValue.boundingBox())!.x)).toBeLessThan(3);

  const chart = page.getByTestId('dashboard-widget-task-status');
  const sectionLabel = chart.getByText('Tasks', { exact: true });
  const chartTitle = chart.getByText('Status breakdown', { exact: true });
  const grip = chart.getByRole('button', { name: 'Move Task Status Breakdown', exact: true });
  expect(Math.abs((await sectionLabel.boundingBox())!.x - (await chartTitle.boundingBox())!.x)).toBeLessThan(3);
  expect((await grip.boundingBox())!.y).toBeLessThan((await chartTitle.boundingBox())!.y);

  await expect(page.getByTestId('dashboard-widget-decisions').getByRole('link', { name: 'View all', exact: true })).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-activity').getByRole('button', { name: 'View all activity', exact: true })).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-audit').getByRole('button', { name: 'View all actions', exact: true })).toBeVisible();
  const kpiPanel = page.getByTestId('dashboard-widget-custom-kpis').getByTestId('custom-kpi-panel');
  const kpiHeading = kpiPanel.getByRole('heading', { name: 'Custom KPIs' });
  await expect(kpiHeading).toBeVisible();
  expect(Math.abs((await kpiHeading.boundingBox())!.x - (await kpiPanel.boundingBox())!.x - 17)).toBeLessThan(3);
});

test('keeps remove controls at the top right of narrow chart, KPI, and team widgets', async ({ page }) => {
  await openDashboard(page, [
    { id: 'task-priority', x: 0, y: 0, w: 3, h: 8 },
    { id: 'task-types', x: 3, y: 0, w: 3, h: 8 },
    { id: 'task-status', x: 6, y: 0, w: 3, h: 8 },
    { id: 'task-trend', x: 9, y: 0, w: 3, h: 8 },
    { id: 'custom-kpis', x: 0, y: 8, w: 3, h: 8 },
    { id: 'project-team', x: 3, y: 8, w: 3, h: 8 },
  ]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();

  for (const [id, title] of [
    ['task-priority', 'Task Priority Distribution'],
    ['task-types', 'Type breakdown'],
    ['task-status', 'Task Status Breakdown'],
    ['task-trend', 'Tasks Created vs Completed'],
    ['custom-kpis', 'Custom KPIs'],
    ['project-team', 'Project team'],
  ]) {
    const tile = page.getByTestId(`dashboard-widget-${id}`);
    const grip = tile.getByRole('button', { name: `Move ${title}`, exact: true });
    const remove = tile.getByRole('button', { name: `Remove ${title}`, exact: true });
    await expect(remove).toBeAttached();
    const [tileBox, gripBox, removeBox] = await Promise.all([tile.boundingBox(), grip.boundingBox(), remove.boundingBox()]);
    expect(tileBox).not.toBeNull();
    expect(gripBox).not.toBeNull();
    expect(removeBox).not.toBeNull();
    expect(Math.abs((gripBox!.y + gripBox!.height / 2) - (removeBox!.y + removeBox!.height / 2)), id).toBeLessThan(8);
    expect(tileBox!.x + tileBox!.width - removeBox!.x - removeBox!.width, id).toBeLessThan(40);
  }
});

test('loads the layout for the project in the URL when switching projects', async ({ page }) => {
  const org = { id: 28, name: 'Dashboard Test Org', slug: 'dashboard-test-org' };
  const projects = [
    { id: 287, slug: 'first-project', name: 'First', organization: org },
    { id: 288, slug: 'second-project', name: 'Second', organization: org },
  ];
  const layouts: Record<number, typeof initial> = {
    287: [{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }],
    288: [{ id: 'activity', x: 0, y: 0, w: 6, h: 5 }],
  };
  const requestedProjects: string[] = [];
  await installApiMockSafetyNet(page);
  const user = { id: 1, email: 'e2e@example.com', username: 'e2e-user', current_organization: org };
  await seedAuthenticatedUser(page, user);
  await mockAuthenticatedUserApis(page, user);
  await mockProjectShellApis(page);
  await seedActiveProject(page, projects[0]);
  await page.route('**/api/core/organizations/dashboard-test-org/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(org) });
  });
  await page.route('**/api/core/projects/**', async (route) => {
    const slug = new URL(route.request().url()).pathname.split('/').filter(Boolean).at(-1);
    const selected = projects.find((item) => item.slug === slug);
    if (selected) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(selected) });
    } else {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(projects) });
    }
  });
  await page.route('**/api/dashboard/summary/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      recent_activity: [], time_metrics: { completed_last_7_days: 0, updated_last_7_days: 0, created_last_7_days: 0, due_soon: 0 },
      status_overview: { total_work_items: 0, breakdown: [] }, priority_breakdown: [], types_of_work: [],
    }) });
  });
  await page.route('**/api/dashboard/layout/**', async (route) => {
    const id = Number(new URL(route.request().url()).searchParams.get('project_id'));
    requestedProjects.push(String(id));
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: id, project_slug: projects.find((item) => item.id === id)?.slug,
      widgets: layouts[id],
    }) });
  });
  for (const [slug, expectedId, visible, hidden] of [
    ['first-project', '287', 'audit', 'activity'],
    ['second-project', '288', 'activity', 'audit'],
    ['first-project', '287', 'audit', 'activity'],
  ] as const) {
    await page.goto(`/dashboard-test-org/${slug}/overview`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId(`dashboard-widget-${visible}`)).toBeVisible();
    await expect(page.getByTestId(`dashboard-widget-${hidden}`)).toHaveCount(0);
    expect(requestedProjects.at(-1)).toBe(expectedId);
  }
});

test('does not display a layout returned for another project', async ({ page }) => {
  await openDashboard(page);
  await page.route('**/api/dashboard/layout/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: 999, project_slug: 'another-project', widgets: initial,
    }) });
  });
  await page.reload();
  await expect(page.getByRole('alert').filter({ hasText: 'Dashboard layout could not be loaded' })).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-audit')).toHaveCount(0);
});

test('drags a preset out of Add widget with a correctly sized dashed preview and saves only on drop', async ({ page }) => {
  const layout = await openDashboard(page, [{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }]);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const source = page.getByRole('button', { name: 'Drag Type breakdown onto dashboard', exact: true });
  const from = (await source.boundingBox())!;
  const canvas = (await page.getByTestId('dashboard-canvas').boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2); await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width * 0.55, canvas.y + 5 * 60 + 20, { steps: 12 });
  const preview = page.getByTestId('widget-drop-preview');
  await expect(preview).toBeVisible();
  await expect(preview).toHaveCSS('border-top-style', 'dashed');
  const previewBounds = (await preview.boundingBox())!;
  expect(previewBounds.x - canvas.x).toBeCloseTo((canvas.width + 12) / 2, 0);
  expect(previewBounds.width).toBeCloseTo((canvas.width + 12) / 3 - 12, 0);
  await expect.poll(() => preview.evaluate((element) => element.getBoundingClientRect().y - element.parentElement!.getBoundingClientRect().y)).toBeCloseTo(0, 0);
  expect(previewBounds.height).toBe(348);
  const expectedBounds = (await preview.boundingBox())!;
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  expect(layout.saved().find((widget) => widget.id === 'task-types')).toMatchObject({ x: 6, y: 0, w: 4, h: 6 });
  const tile = page.getByTestId('dashboard-widget-task-types');
  const placedBounds = (await tile.boundingBox())!;
  expect(placedBounds.width).toBeCloseTo(expectedBounds.width, 0);
  expect(placedBounds.height).toBeCloseTo(expectedBounds.height, 0);
  await expect(page.getByLabel('Widget picker')).toBeVisible();
  await expect(preview).toHaveCount(0);
});

test('cancels an Add widget drag outside the canvas without adding or saving it', async ({ page }) => {
  const layout = await openDashboard(page);
  await page.getByRole('button', { name: 'Customize', exact: true }).click();
  const source = page.getByRole('button', { name: 'Drag Type breakdown onto dashboard', exact: true });
  const from = (await source.boundingBox())!;
  await page.mouse.move(from.x + 20, from.y + 10); await page.mouse.down();
  await page.mouse.move(20, 20, { steps: 12 }); await page.mouse.up();
  expect(layout.writes()).toBe(0);
  await expect(page.getByTestId('dashboard-widget-task-types')).toHaveCount(0);
  await expect(page.getByTestId('widget-drop-preview')).toHaveCount(0);
  await expect(source).toBeVisible();
});

test('Hide Panel removes the sidebar, releases its width and remains hidden after refresh', async ({ page }) => {
  await openDashboard(page);
  const panel = page.locator('[data-upcoming-meetings-panel]');
  await page.getByRole('button', { name: 'Hide Panel', exact: true }).click();
  await expect(panel).toHaveCount(0);
  const wide = (await page.getByTestId('dashboard-canvas').boundingBox())!.width;
  await page.getByRole('button', { name: 'Show Panel', exact: true }).click();
  await expect(panel).toBeVisible();
  const narrow = (await page.getByTestId('dashboard-canvas').boundingBox())!.width;
  expect(wide - narrow).toBeGreaterThan(300);
  await page.getByRole('button', { name: 'Hide Panel', exact: true }).click();
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Show Panel', exact: true })).toBeVisible();
  await expect(panel).toHaveCount(0);
});
