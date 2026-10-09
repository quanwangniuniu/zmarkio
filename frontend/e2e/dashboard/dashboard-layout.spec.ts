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
  { id: 'audit', label: 'Audit', x: 0, y: 0, w: 6, h: 6, min_h: 4 },
  { id: 'activity', label: 'Recent activity', x: 6, y: 0, w: 6, h: 6, min_h: 4 },
];

test.use({ storageState: { cookies: [], origins: [] }, screenshot: 'off' });

async function openDashboard(page: Page) {
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
  let persisted: DashboardWidgetPosition[] = catalog.map(({ id, x, y, w, h }) => ({ id, x, y, w, h }));
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') {
      writes += 1;
      persisted = route.request().postDataJSON().widgets;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: project.id, project_slug: project.slug, widgets: persisted,
      catalog, columns: 12, max_rows: 200, updated_at: null,
    }) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/dashboard', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible();
  return { saved: () => persisted, writes: () => writes };
}

test('moving and resizing cards saves their layout after each gesture', async ({ page }) => {
  const layout = await openDashboard(page);
  const move = page.getByRole('button', { name: 'Move Audit' });
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
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'audit')?.y).toBeGreaterThan(0);

  const resize = page.getByRole('button', { name: 'Resize Audit' });
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
  await page.reload();
  await expect(page.getByRole('button', { name: 'Move Audit' })).toBeVisible();
  expect(layout.saved().find((widget) => widget.id === 'audit')).toEqual(saved);
  await expect(page.locator('[data-widget-id="audit"]')).toHaveCSS('grid-row-start', String(saved.y + 1));
});
