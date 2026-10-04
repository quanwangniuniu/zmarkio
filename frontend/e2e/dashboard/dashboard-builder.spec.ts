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

async function openDashboard(page: Page) {
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
  let persisted = initial.map((widget) => ({ ...widget }));
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
