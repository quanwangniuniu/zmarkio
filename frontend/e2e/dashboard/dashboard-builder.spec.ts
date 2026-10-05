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
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      project_id: project.id, project_slug: project.slug, widgets: persisted,
    }) });
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

test('resizes Type breakdown repeatedly while its handle moves with the preview', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'task-types', x: 0, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 4, y: 0, w: 4, h: 8 },
  ]);
  const tile = page.getByTestId('dashboard-widget-task-types');
  const handle = tile.getByRole('button', { name: 'Resize Type breakdown' });
  const grid = page.locator('.dashboard-builder-grid');
  const columnStep = ((await grid.boundingBox())!.width + 12) / 12;

  for (const [change, expectedWidth, expectedWrites] of [[2, 6, 1], [-2, 4, 2]] as const) {
    const box = await handle.boundingBox();
    expect(box).not.toBeNull();
    const x = box!.x + box!.width / 2;
    const y = box!.y + box!.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + change * columnStep, y, { steps: 12 });
    await expect.poll(() => tile.evaluate((element) => getComputedStyle(element).gridColumnEnd)).toBe(`span ${expectedWidth}`);
    expect(layout.writes()).toBe(expectedWrites - 1);
    await page.mouse.up();
    await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-types')?.w).toBe(expectedWidth);
    expect(layout.writes()).toBe(expectedWrites);
  }
  await page.reload();
  await expect.poll(() => tile.evaluate((element) => getComputedStyle(element).gridColumnEnd)).toBe('span 4');
});

test('removes a widget from the canvas after resizing and keeps it removed after reload', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'task-types', x: 0, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 4, y: 0, w: 4, h: 8 },
  ]);
  const tile = page.getByTestId('dashboard-widget-task-types');
  const handle = tile.getByRole('button', { name: 'Resize Type breakdown' });
  const box = await handle.boundingBox();
  const columnStep = ((await page.locator('.dashboard-builder-grid').boundingBox())!.width + 12) / 12;
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 + columnStep, box!.y + box!.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => layout.saved().find((widget) => widget.id === 'task-types')?.w).toBe(5);

  await tile.hover();
  await tile.getByRole('button', { name: 'Remove Type breakdown' }).click();
  await expect(tile).toHaveCount(0);
  await expect(page.getByTestId('dashboard-widget-task-status')).toBeVisible();
  await page.getByRole('button', { name: 'Add widget' }).click();
  await expect(page.getByRole('button', { name: 'Type breakdown', exact: true })).toBeVisible();
  await expect.poll(() => layout.saved().some((widget) => widget.id === 'task-types')).toBe(false);
  await page.reload();
  await expect(tile).toHaveCount(0);
});

test('does not keep a removed widget visible in an interrupted resize preview', async ({ page }) => {
  const layout = await openDashboard(page, [
    { id: 'task-types', x: 0, y: 0, w: 4, h: 8 },
    { id: 'task-status', x: 4, y: 0, w: 4, h: 8 },
  ]);
  const tile = page.getByTestId('dashboard-widget-task-types');
  const box = await tile.getByRole('button', { name: 'Resize Type breakdown' }).boundingBox();
  const columnStep = ((await page.locator('.dashboard-builder-grid').boundingBox())!.width + 12) / 12;
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2 + columnStep, box!.y + box!.height / 2, { steps: 8 });
  await tile.getByRole('button', { name: 'Remove Type breakdown' }).focus();
  await page.keyboard.press('Enter');
  await expect(tile).toHaveCount(0);
  await page.mouse.up();
  await page.getByRole('button', { name: 'Add widget' }).click();
  await expect(page.getByRole('button', { name: 'Type breakdown', exact: true })).toBeVisible();
  await expect.poll(() => layout.saved().some((widget) => widget.id === 'task-types')).toBe(false);
});

test('keeps the widget picker open while adding multiple widgets until manually collapsed', async ({ page }) => {
  const layout = await openDashboard(page, [{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }]);
  const toggle = page.getByRole('button', { name: 'Add widget' });
  const picker = page.getByLabel('Widget picker');
  await toggle.click();
  await picker.getByRole('button', { name: 'Type breakdown', exact: true }).click();
  await expect(picker).toBeVisible();
  await expect(picker.getByRole('button', { name: 'Type breakdown', exact: true })).toHaveCount(0);
  await picker.getByRole('button', { name: 'Overall Progress', exact: true }).click();
  await expect(picker).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-task-types')).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-overall-progress')).toBeVisible();
  await expect.poll(() => layout.saved().filter((widget) => ['task-types', 'overall-progress'].includes(widget.id)).length).toBe(2);
  await toggle.click();
  await expect(picker).toHaveCount(0);
});

test('closes the widget picker after the last available widget is added', async ({ page }) => {
  const placedIds = [
    'overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks',
    'needs-attention', 'decisions', 'tasks', 'operations', 'task-status',
    'task-priority', 'task-trend', 'custom-kpis', 'meetings', 'activity', 'audit',
    'project-team',
  ];
  const layout = await openDashboard(page, placedIds.map((id, index) => ({
    id, x: (index % 3) * 4, y: Math.floor(index / 3) * 8, w: 4, h: 8,
  })));
  const toggle = page.getByRole('button', { name: 'Add widget' });
  const picker = page.getByLabel('Widget picker');
  await toggle.click();
  await expect(picker.getByRole('button')).toHaveCount(1);
  await picker.getByRole('button', { name: 'Type breakdown', exact: true }).click();
  await expect(picker).toHaveCount(0);
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect.poll(() => layout.saved().some((widget) => widget.id === 'task-types')).toBe(true);
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

test('keeps remove controls at the top right of narrow chart, KPI, and team widgets', async ({ page }) => {
  await openDashboard(page, [
    { id: 'task-priority', x: 0, y: 0, w: 3, h: 8 },
    { id: 'task-types', x: 3, y: 0, w: 3, h: 8 },
    { id: 'task-status', x: 6, y: 0, w: 3, h: 8 },
    { id: 'task-trend', x: 9, y: 0, w: 3, h: 8 },
    { id: 'custom-kpis', x: 0, y: 8, w: 3, h: 8 },
    { id: 'project-team', x: 3, y: 8, w: 3, h: 8 },
  ]);

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
