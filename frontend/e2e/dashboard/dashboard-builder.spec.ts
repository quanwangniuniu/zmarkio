/** MED-287: completed drag and resize gestures persist across reloads. */
import { expect, test, type Page } from '@playwright/test';
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

async function openDashboard(page: Page, widgets = initial) {
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
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ widgets: persisted }) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  return { saved: () => persisted, writes: () => writes };
}

test('repositions a widget and autosaves only after the drag ends', async ({ page }) => {
  const layout = await openDashboard(page);
  const activity = page.getByRole('button', { name: 'Move Recent activity', exact: true });
  const from = await activity.boundingBox();
  const to = await page.getByRole('button', { name: 'Move Audit', exact: true }).boundingBox();
  expect(from).not.toBeNull();
  expect(to).not.toBeNull();
  await page.mouse.move(from!.x + 20, from!.y + 20);
  await page.mouse.down();
  await page.mouse.move(to!.x + 20, to!.y + 20, { steps: 12 });
  await expect.poll(() => page.getByTestId('dashboard-widget-activity').evaluate((element) => getComputedStyle(element).gridColumnStart)).toBe('1');
  expect(layout.writes()).toBe(0);
  await page.mouse.up();
  await expect.poll(() => layout.writes()).toBe(1);
  expect(layout.saved().find((widget) => widget.id === 'activity')).toMatchObject({ x: 0, y: 0 });
  await page.reload();
  await expect(activity).toBeVisible();
  expect((await activity.boundingBox())!.x).toBeLessThan(from!.x);
});

test('persists a resized widget after the pointer is released', async ({ page }) => {
  const layout = await openDashboard(page);
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

test('shows the drag grip over a card icon without shifting its title', async ({ page }) => {
  await openDashboard(page);
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
