/** MED-287: a completed drag saves the new position and reloads it. */
import { expect, test } from '@playwright/test';
import {
  installApiMockSafetyNet,
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  seedAuthenticatedUser,
  waitForLayoutMain,
} from '../messages/messages-helpers';

const project = {
  id: 287,
  slug: 'med-287',
  name: 'Dashboard Builder Test',
  is_active: true,
  organization: { id: 28, name: 'Dashboard Test Org', slug: 'dashboard-test-org' },
};
const initial = [
  { id: 'audit', x: 0, y: 0, w: 6, h: 5 },
  { id: 'activity', x: 6, y: 0, w: 6, h: 5 },
  { id: 'meetings', x: 6, y: 10, w: 6, h: 5 },
];

test.use({ storageState: { cookies: [], origins: [] } });

test('repositions a widget and autosaves the completed drag', async ({ page }) => {
  test.setTimeout(120_000);
  await installApiMockSafetyNet(page);
  await seedAuthenticatedUser(page);
  await mockAuthenticatedUserApis(page);
  await mockProjectShellApis(page);
  await seedActiveProject(page, project);
  // The shared shell mock returns no projects; keep this seeded project active.
  await page.route('**/api/core/projects**', async (route) => {
    if (!/\/api\/core\/projects\/?$/.test(new URL(route.request().url()).pathname)) {
      await route.fallback();
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([project]) });
  });
  await page.route('**/api/dashboard/summary/**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        recent_activity: [],
        time_metrics: { completed_last_7_days: 0, updated_last_7_days: 0, created_last_7_days: 0, due_soon: 0 },
        status_overview: { total_work_items: 0, breakdown: [] },
        priority_breakdown: [],
        types_of_work: [],
      }),
    });
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
  const audit = page.getByRole('button', { name: 'Move Audit', exact: true });
  const activity = page.getByRole('button', { name: 'Move Recent activity', exact: true });
  await expect(audit).toBeVisible();
  await expect(activity).toBeVisible();
  const from = await activity.boundingBox();
  const to = await audit.boundingBox();
  expect(from).not.toBeNull();
  expect(to).not.toBeNull();
  await page.mouse.move(from!.x + 20, from!.y + 20);
  await page.mouse.down();
  await page.mouse.move(to!.x + 20, to!.y + 20, { steps: 12 });
  await expect(page.getByTestId('dashboard-widget-activity')).toHaveClass(/outline-dashed/);
  await expect.poll(() => page.getByTestId('dashboard-widget-activity').evaluate((element) => getComputedStyle(element).gridColumnStart)).toBe('1');
  await expect.poll(() => page.getByTestId('dashboard-widget-meetings').evaluate((element) => getComputedStyle(element).gridRowStart)).toBe('1');
  expect(writes).toBe(0);
  await page.mouse.up();
  await expect.poll(() => writes).toBe(1);
  await expect.poll(() => persisted.find((widget) => widget.id === 'activity')?.x).toBe(0);
  expect(persisted.find((widget) => widget.id === 'activity')?.y).toBe(0);
  expect(persisted.find((widget) => widget.id === 'audit')?.y).toBe(5);
  expect(persisted.find((widget) => widget.id === 'meetings')?.y).toBe(0);
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();

  await page.reload();
  await expect(activity).toBeVisible();
  const reloadedActivity = await activity.boundingBox();
  expect(reloadedActivity!.x).toBeLessThan(from!.x);
  expect(writes).toBe(1);
});

test('renders independent workspace cards and keeps the team invite usable beside the meetings panel', async ({ page }) => {
  test.setTimeout(120_000);
  await installApiMockSafetyNet(page);
  const user = { id: 1, username: 'e2e-user', email: 'e2e@example.com', roles: ['owner'], current_organization: project.organization };
  await seedAuthenticatedUser(page, user);
  await mockAuthenticatedUserApis(page, user);
  await mockProjectShellApis(page);
  await seedActiveProject(page, project);
  await page.route('**/api/core/organizations/dashboard-test-org/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project.organization) });
  });
  const inviteRequests: Array<{ email: string; role: string }> = [];
  await page.route('**/api/core/projects**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/projects/287/members/') && route.request().method() === 'POST') {
      inviteRequests.push(route.request().postDataJSON());
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 2 }) });
      return;
    }
    let body: unknown = [];
    if (path.endsWith('/projects/')) body = [project];
    else if (path.endsWith('/projects/med-287/')) body = project;
    else if (path.endsWith('/projects/287/members/')) body = [{ id: 1, user, role: 'owner', is_active: true, project: { ...project, owner: user } }];
    else if (path.endsWith('/projects/287/roles/')) body = { roles: [{ value: 'member', label: 'Member' }], default_role: 'member' };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.route('**/api/dashboard/workspace/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ decisions: [], tasks: [], spreadsheets: [], patterns: [] }) });
  });
  await page.route('**/api/dashboard/summary/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      recent_activity: [],
      time_metrics: { completed_last_7_days: 0, created_last_7_days: 0, due_soon: 0 },
      status_overview: { total_work_items: 0, breakdown: [] }, priority_breakdown: [], types_of_work: [], daily_task_activity: [],
    }) });
  });
  await page.route('**/api/report/kpis/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{
      id: 1, project: project.slug, project_id: project.id, name: 'Revenue', description: '',
      formula: 'spend', display_format: 'currency', value: '1200', error: null,
      created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
    }]) });
  });
  await page.route('**/api/report/kpi-metrics/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ metrics: [] }) });
  });
  // Simulate the saved layout returned by a backend that has not restarted yet.
  const layout = [
    { id: 'workspace', x: 0, y: 0, w: 12, h: 12 },
    { id: 'project-team', x: 9, y: 12, w: 3, h: 10 },
    { id: 'meetings', x: 0, y: 22, w: 6, h: 8 },
    { id: 'activity', x: 6, y: 22, w: 3, h: 8 },
    { id: 'audit', x: 9, y: 22, w: 3, h: 8 },
    { id: 'custom-kpis', x: 0, y: 30, w: 12, h: 7 },
  ];
  let savedLayout = layout as Array<{ id: string; x: number; y: number; w: number; h: number }>;
  let layoutWrites = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') {
      layoutWrites += 1;
      savedLayout = route.request().postDataJSON().widgets;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ widgets: savedLayout }) });
  });

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  for (const name of [
    'Overall Progress', 'Tasks Completed', 'Task Completion Rate', 'Overdue Tasks', 'Needs Attention',
    'Decisions', 'Tasks', 'Operations', 'Task Status Breakdown',
    'Task Priority Distribution', 'Tasks Created vs Completed',
  ]) {
    await expect(page.getByRole('button', { name: `Move ${name}`, exact: true })).toBeVisible();
  }
  await expect(page.getByRole('button', { name: 'Move Workspace', exact: true })).toHaveCount(0);
  const workspaceCard = page.getByTestId('dashboard-widget-overall-progress');
  const workspaceHeader = workspaceCard.getByText('Overall Progress', { exact: true });
  const workspaceGrip = workspaceCard.getByRole('button', { name: 'Move Overall Progress', exact: true });
  const workspaceTrash = workspaceCard.getByRole('button', { name: 'Remove Overall Progress' });
  const metricValue = workspaceCard.getByText('0%', { exact: true });
  const gripBox = (await workspaceGrip.boundingBox())!;
  const titleBox = (await workspaceHeader.boundingBox())!;
  const trashBox = (await workspaceTrash.boundingBox())!;
  const valueBox = (await metricValue.boundingBox())!;
  expect(gripBox.x + gripBox.width).toBeLessThanOrEqual(titleBox.x + 2);
  expect(Math.abs(gripBox.y + gripBox.height / 2 - (titleBox.y + titleBox.height / 2))).toBeLessThan(8);
  expect(trashBox.x).toBeGreaterThan(titleBox.x + titleBox.width);
  expect(Math.abs(valueBox.x - titleBox.x)).toBeLessThan(3);
  await expect(workspaceTrash).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await page.getByTestId('dashboard-widget-overall-progress').screenshot({ path: '/tmp/med287-overall-widget.png' });
  await page.getByRole('button', { name: 'Resize Overall Progress' }).hover();
  await page.getByTestId('dashboard-widget-overall-progress').screenshot({ path: '/tmp/med287-overall-widget-hover.png' });
  await page.screenshot({ path: '/tmp/med287-overlay-controls.png' });
  const team = page.getByTestId('dashboard-widget-project-team');
  const email = team.getByPlaceholder('name@company.com');
  const role = team.getByRole('combobox', { name: 'Invite role' });
  const invite = team.getByRole('button', { name: 'Invite', exact: true });
  await expect(email).toBeVisible();
  await expect(role).toBeVisible();
  await expect(invite).toBeVisible();
  const withinTeam = async () => {
    await expect.poll(async () => {
      const bounds = await team.boundingBox();
      const boxes = await Promise.all([email, role, invite].map((control) => control.boundingBox()));
      return boxes.every((box) => box && bounds && box.x >= bounds.x && box.x + box.width <= bounds.x + bounds.width + 1);
    }).toBe(true);
  };
  await withinTeam();
  await team.screenshot({ path: '/tmp/med287-team-widget.png' });
  await email.fill('teammate@example.com');
  await invite.click();
  await expect.poll(() => inviteRequests).toEqual([{ email: 'teammate@example.com', role: 'member' }]);
  await page.getByRole('button', { name: 'Resize Overall Progress' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => savedLayout.find((widget) => widget.id === 'overall-progress')?.w).toBe(5);
  const auditBeforeResize = savedLayout.find((widget) => widget.id === 'audit')!.y;
  const writesBeforeResize = layoutWrites;
  const before = await team.boundingBox();
  const grip = team.getByRole('button', { name: 'Resize Project team' });
  await grip.scrollIntoViewIfNeeded();
  const handle = await grip.boundingBox();
  expect(handle).not.toBeNull();
  const startX = handle!.x + handle!.width / 2;
  const startY = handle!.y + handle!.height / 2;
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX - before!.width / 3, startY - 420, { steps: 12 });
  await expect.poll(() => page.getByTestId('dashboard-widget-audit').evaluate((element) => Number(getComputedStyle(element).gridRowStart))).toBeLessThan(auditBeforeResize + 1);
  expect(layoutWrites).toBe(writesBeforeResize);
  await page.mouse.up();
  await expect.poll(() => savedLayout.find((widget) => widget.id === 'project-team')).toMatchObject({ w: 2, h: 6 });
  expect(savedLayout.find((widget) => widget.id === 'audit')!.y).toBeLessThan(auditBeforeResize);
  const after = await team.boundingBox();
  expect(after!.width).toBeLessThan(before!.width - 30);
  expect(after!.height).toBeLessThan(before!.height - 200);
  expect(layoutWrites).toBe(writesBeforeResize + 1);

  for (const [id, title] of [['meetings', 'Meetings & action items'], ['activity', 'Recent activity'], ['audit', 'Audit']] as const) {
    const tile = page.getByTestId(`dashboard-widget-${id}`);
    const nativeCard = tile.locator(`[data-overview-card="${id}"]`);
    const grip = tile.getByRole('button', { name: `Resize ${title}` });
    for (const [deltaRows, direction] of [[-1, 'shrink'], [1, 'grow']] as const) {
      await grip.scrollIntoViewIfNeeded();
      const originalHeight = (await nativeCard.boundingBox())!.height;
      const originalRows = savedLayout.find((widget) => widget.id === id)!.h;
      const writes = layoutWrites;
      const box = (await grip.boundingBox())!;
      const x = box.x + box.width / 2;
      const y = box.y + box.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x, y + 60 * deltaRows, { steps: 8 });
      await expect.poll(async () => (await nativeCard.boundingBox())!.height).toBeCloseTo(originalHeight + 60 * deltaRows, 0);
      expect(layoutWrites).toBe(writes);
      await page.mouse.up();
      await expect.poll(() => savedLayout.find((widget) => widget.id === id)!.h).toBe(originalRows + deltaRows);
      await expect.poll(async () => (await nativeCard.boundingBox())!.height).toBeCloseTo(originalHeight + 60 * deltaRows, 0);
      expect(layoutWrites).toBe(writes + 1);
      if (direction === 'shrink') await tile.screenshot({ path: `/tmp/med287-${id}-shrunk.png` });
    }
  }

  const kpiTile = page.getByTestId('dashboard-widget-custom-kpis');
  const kpiPanel = kpiTile.getByTestId('custom-kpi-panel');
  await expect(kpiPanel.getByTestId('custom-kpi-tile')).toContainText('Revenue');
  await expect(kpiPanel.getByTestId('custom-kpi-tile-value')).toContainText('1,200');
  await expect(kpiPanel.getByTestId('new-kpi-button')).toBeVisible();
  for (const [deltaRows, direction] of [[-1, 'shrink'], [1, 'grow']] as const) {
    const resize = kpiTile.getByRole('button', { name: 'Resize Custom KPIs' });
    const gesturePosition = async () => resize.evaluate((element) => {
      let scrollParent = element.parentElement;
      while (scrollParent && !(scrollParent.scrollHeight > scrollParent.clientHeight && /auto|scroll/.test(getComputedStyle(scrollParent).overflowY))) scrollParent = scrollParent.parentElement;
      return { scrollTop: scrollParent?.scrollTop ?? document.scrollingElement?.scrollTop ?? 0, handleY: element.getBoundingClientRect().y, tileY: element.closest('section')?.getBoundingClientRect().y };
    });
    await resize.scrollIntoViewIfNeeded();
    const originalPanel = (await kpiPanel.boundingBox())!;
    const originalTile = (await kpiTile.boundingBox())!;
    expect(Math.abs(originalTile.height - originalPanel.height)).toBeLessThan(3);
    const originalRows = savedLayout.find((widget) => widget.id === 'custom-kpis')!.h;
    const writes = layoutWrites;
    const beforeGesture = await gesturePosition();
    const box = (await resize.boundingBox())!;
    const x = box.x + box.width - 3;
    const y = box.y + box.height - 3;
    expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('button')?.getAttribute('aria-label'), { x, y })).toBe('Resize Custom KPIs');
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y + 60 * deltaRows, { steps: 8 });
    const duringGesture = await gesturePosition();
    expect(duringGesture.scrollTop).toBe(beforeGesture.scrollTop);
    expect(duringGesture.handleY).toBeCloseTo(beforeGesture.handleY + 60 * deltaRows, 0);
    await expect(kpiTile).not.toHaveClass(/outline-dashed/);
    expect(await resize.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe('none');
    await expect.poll(async () => (await kpiPanel.boundingBox())!.height).toBeCloseTo(originalPanel.height + 60 * deltaRows, 0);
    expect(layoutWrites).toBe(writes);
    await page.mouse.up();
    await expect.poll(() => savedLayout.find((widget) => widget.id === 'custom-kpis')!.h).toBe(originalRows + deltaRows);
    await expect.poll(async () => (await kpiPanel.boundingBox())!.height).toBeCloseTo(originalPanel.height + 60 * deltaRows, 0);
    await expect(kpiPanel.getByTestId('new-kpi-button')).toBeVisible();
    expect(layoutWrites).toBe(writes + 1);
    if (direction === 'shrink') await kpiTile.screenshot({ path: '/tmp/med287-custom-kpi-shrunk.png' });
  }

  for (const [id, title] of [['overall-progress', 'Overall Progress'], ['decisions', 'Decisions']] as const) {
    const tile = page.getByTestId(`dashboard-widget-${id}`);
    const move = tile.getByRole('button', { name: `Move ${title}`, exact: true });
    await move.scrollIntoViewIfNeeded();
    await move.hover();
    if (id === 'decisions') {
      await expect.poll(() => tile.getByText('Decisions', { exact: true }).evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
      await tile.screenshot({ path: '/tmp/med287-decisions-hover.png' });
    }
    const box = (await move.boundingBox())!;
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('button')?.getAttribute('aria-label'), { x, y })).toBe(`Move ${title}`);
    const originalY = savedLayout.find((widget) => widget.id === id)!.y;
    const writes = layoutWrites;
    const scrollTop = await page.locator('main').evaluate((element) => element.scrollTop);
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y + 120, { steps: 12 });
    await expect(tile).toHaveClass(/outline-dashed/);
    await expect.poll(() => tile.evaluate((element) => Number(getComputedStyle(element).gridRowStart))).toBe(originalY + 3);
    expect(await page.locator('main').evaluate((element) => element.scrollTop)).toBe(scrollTop);
    expect(layoutWrites).toBe(writes);
    await page.mouse.up();
    await expect.poll(() => savedLayout.find((widget) => widget.id === id)!.y).not.toBe(originalY);
    await expect.poll(() => layoutWrites).toBe(writes + 1);
  }

});
