/** MED-287: editor entry/exit, grouped cards, and completed-gesture autosave. */
import { expect, test, type Page } from '@playwright/test';
import {
  installApiMockSafetyNet, mockAuthenticatedUserApis, mockProjectShellApis,
  seedActiveProject, seedAuthenticatedUser, waitForLayoutMain,
} from '../messages/messages-helpers';

const project = {
  id: 287, slug: 'med-287', name: 'Dashboard Builder Test', is_active: true,
  organization: { id: 28, name: 'Dashboard Test Org', slug: 'dashboard-test-org' },
};
const user = { id: 1, email: 'e2e@example.com', username: 'e2e-user', roles: ['Media Buyer'], current_organization: project.organization };

test.use({ storageState: { cookies: [], origins: [] } });

async function setup(page: Page) {
  await installApiMockSafetyNet(page);
  await seedAuthenticatedUser(page, user);
  await mockAuthenticatedUserApis(page, user);
  await mockProjectShellApis(page);
  await seedActiveProject(page, project);
  await page.route('**/api/core/projects**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/projects/')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([project]) });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/dashboard/summary/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
      recent_activity: [], time_metrics: { completed_last_7_days: 0, updated_last_7_days: 0, created_last_7_days: 0, due_soon: 0 },
      status_overview: { total_work_items: 0, breakdown: [] }, priority_breakdown: [], types_of_work: [], daily_task_activity: [],
    }) });
  });
  await page.route('**/api/dashboard/workspace/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ decisions: [], tasks: [], spreadsheets: [], patterns: [] }) });
  });
  await page.route('**/api/core/organizations/dashboard-test-org/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project.organization) });
  });
  await page.route('**/api/core/organizations/switch/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ current_organization_id: project.organization.id }) });
  });
  await page.route('**/api/core/projects/med-287/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(project) });
  });
}

async function drag(page: Page, source: ReturnType<Page['getByRole']>, destination: ReturnType<Page['getByTestId']>, onHover?: () => Promise<void>) {
  await source.scrollIntoViewIfNeeded();
  const from = await source.boundingBox();
  const to = await destination.boundingBox();
  expect(from).not.toBeNull();
  expect(to).not.toBeNull();
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2);
  await page.mouse.down();
  await page.mouse.move(to!.x + Math.min(to!.width / 2, 90), to!.y + Math.min(to!.height / 2, 90), { steps: 12 });
  await onHover?.();
  await page.mouse.up();
}

test('moves the rendered card with the pointer and shows only a card-sized blue drop preview', async ({ page }) => {
  test.setTimeout(120_000);
  await setup(page);
  await page.route('**/api/dashboard/layout/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: 2, items: [
      { kind: 'widget', id: 'audit', x: 0, y: 0, w: 6, h: 6 },
      { kind: 'widget', id: 'activity', x: 6, y: 0, w: 6, h: 6 },
    ] }) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  const audit = page.getByTestId('dashboard-widget-audit');
  const handle = page.getByRole('button', { name: 'Move Audit', exact: true });
  const activity = page.getByTestId('dashboard-widget-activity');
  const canvas = page.getByTestId('dashboard-canvas');
  await expect(audit).toBeVisible();
  const start = await audit.boundingBox();
  const grip = await handle.boundingBox();
  const target = await activity.boundingBox();
  expect(start && grip && target).toBeTruthy();
  await page.mouse.move(grip!.x + grip!.width / 2, grip!.y + grip!.height / 2);
  await page.mouse.down();
  await page.mouse.move(target!.x + target!.width / 2, target!.y + target!.height / 2, { steps: 12 });
  await expect.poll(async () => (await audit.boundingBox())!.x).toBeGreaterThan(start!.x + 30);
  const preview = page.getByTestId('dashboard-drop-preview');
  await expect(preview).toBeVisible();
  const previewBox = await preview.boundingBox();
  const canvasBox = await canvas.boundingBox();
  expect(previewBox!.width).toBeLessThan(canvasBox!.width);
  await expect(canvas).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await page.mouse.up();
  await expect(preview).toHaveCount(0);
});

test('keeps canvas and resized widget widths unchanged when the floating Widgets window opens or closes', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'widget', id: 'audit', x: 0, y: 0, w: 6, h: 6 },
    { kind: 'widget', id: 'activity', x: 6, y: 0, w: 6, h: 6 },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); writes += 1; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  const canvas = page.getByTestId('dashboard-canvas');
  const audit = page.getByTestId('dashboard-widget-audit');
  const closedCanvas = await canvas.boundingBox();
  const closedWidget = await audit.boundingBox();
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const panel = page.getByLabel('Dashboard widget palette');
  await expect(panel).toHaveCSS('position', 'fixed');
  const openCanvas = await canvas.boundingBox();
  const openWidget = await audit.boundingBox();
  expect(Math.abs(openCanvas!.width - closedCanvas!.width)).toBeLessThan(1);
  expect(Math.abs(openWidget!.width - closedWidget!.width)).toBeLessThan(1);

  const handle = panel.getByRole('button', { name: 'Move Widgets window' });
  const from = await handle.boundingBox();
  const panelBefore = await panel.boundingBox();
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2);
  await page.mouse.down();
  await page.mouse.move(from!.x + from!.width / 2 - 80, from!.y + from!.height / 2 + 80, { steps: 8 });
  await page.mouse.up();
  const panelAfter = await panel.boundingBox();
  expect(panelAfter!.x).toBeLessThan(panelBefore!.x - 40);
  expect(panelAfter!.y).toBeGreaterThan(panelBefore!.y + 40);
  expect(Math.abs((await canvas.boundingBox())!.width - closedCanvas!.width)).toBeLessThan(1);
  expect(writes).toBe(0);

  await audit.getByRole('button', { name: 'Resize Audit' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => writes).toBe(1);
  const resizedWidth = (await audit.boundingBox())!.width;
  await panel.getByRole('button', { name: 'Close layout panel' }).click();
  expect(Math.abs((await canvas.boundingBox())!.width - closedCanvas!.width)).toBeLessThan(1);
  expect(Math.abs((await audit.boundingBox())!.width - resizedWidth)).toBeLessThan(1);
});

test('scrolls the widget list inside the white floating window while its header stays visible', async ({ page }) => {
  await setup(page);
  await page.route('**/api/dashboard/layout/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: 3, items: [] }) });
  });
  await page.setViewportSize({ width: 1280, height: 600 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const panel = page.getByLabel('Dashboard widget palette');
  const scroll = panel.getByTestId('widgets-panel-scroll');
  const metrics = await scroll.evaluate((element) => ({ height: element.clientHeight, content: element.scrollHeight }));
  expect(metrics.content).toBeGreaterThan(metrics.height);
  const panelBox = await panel.boundingBox();
  const scrollBox = await scroll.boundingBox();
  expect(scrollBox!.x + scrollBox!.width).toBeLessThan(panelBox!.x + panelBox!.width - 4);
  const header = panel.getByRole('heading', { name: 'Widgets', exact: true });
  const headerBefore = await header.boundingBox();
  await scroll.evaluate((element) => { element.scrollTop = 240; });
  await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(100);
  const headerAfter = await header.boundingBox();
  expect(Math.abs(headerAfter!.y - headerBefore!.y)).toBeLessThan(1);
});

test('drags a new group from Widgets and repositions it on the canvas', async ({ page }) => {
  test.setTimeout(120_000);
  await setup(page);
  let saved: any = { version: 2, items: [
    { kind: 'widget', id: 'audit', x: 0, y: 6, w: 6, h: 5 },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); writes += 1; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const canvas = page.getByTestId('dashboard-canvas');
  await drag(page, page.getByRole('button', { name: 'New titled group' }), canvas);
  await expect.poll(() => writes).toBe(1);
  const group = page.locator('[data-testid^="dashboard-group-group-"]').first();
  await expect(group).toBeVisible();
  const groupId = saved.items.find((item: any) => item.kind === 'group').id;
  const originalY = saved.items.find((item: any) => item.id === groupId).y;
  const moveHandle = group.getByRole('button', { name: 'Move group New group' });
  const from = await moveHandle.boundingBox();
  const audit = await page.getByTestId('dashboard-widget-audit').boundingBox();
  expect(from && audit).toBeTruthy();
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2);
  await page.mouse.down();
  await page.mouse.move(audit!.x + 40, audit!.y + audit!.height / 2, { steps: 12 });
  await expect(group).toHaveCSS('pointer-events', 'none');
  await page.mouse.up();
  await expect.poll(() => writes).toBe(2);
  expect(saved.items.find((item: any) => item.id === groupId).y).toBeGreaterThan(originalY);
});

test('previews and saves a new widget at the width of the vacant group slot', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'group', id: 'group-project-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 6, children: [
      { kind: 'widget', id: 'task-completion-rate', x: 3, y: 0, w: 3, h: 4 },
      { kind: 'widget', id: 'needs-attention', x: 6, y: 0, w: 6, h: 4 },
    ] },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); writes += 1; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const group = page.getByTestId('dashboard-group-group-project-overview');
  const grid = group.locator('[data-group-grid]');
  await drag(page, page.getByRole('button', { name: 'Drag Overall Progress onto dashboard' }),
    group.locator('[aria-label^="Drop widgets into"]'), async () => {
      const preview = await page.getByTestId('dashboard-drop-preview').boundingBox();
      const gridBox = await grid.boundingBox();
      expect(preview!.width).toBeGreaterThan(gridBox!.width / 5);
      expect(preview!.width).toBeLessThan(gridBox!.width / 3);
    });
  await expect.poll(() => writes).toBe(1);
  const children = saved.items[0].children;
  expect(children.find((child: any) => child.id === 'overall-progress')).toMatchObject({ x: 0, w: 3 });
  expect(children.find((child: any) => child.id === 'task-completion-rate')).toMatchObject({ x: 3, y: 0, w: 3 });
  await expect(group.getByTestId('dashboard-widget-overall-progress')).toBeVisible();
});

test('shows priority and type breakdown as independently removable widgets', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'group', id: 'group-tasks', title: 'Tasks', x: 0, y: 0, w: 12, h: 8, children: [
      { kind: 'widget', id: 'task-priority', x: 0, y: 0, w: 6, h: 7 },
      { kind: 'widget', id: 'task-types', x: 6, y: 0, w: 6, h: 7 },
    ] },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); writes += 1; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  const priority = page.getByTestId('dashboard-widget-task-priority');
  const types = page.getByTestId('dashboard-widget-task-types');
  await expect(priority.getByRole('button', { name: 'Move Task Priority Distribution', exact: true })).toBeVisible();
  await expect(types.getByRole('button', { name: 'Move Type breakdown', exact: true })).toBeVisible();
  await types.getByRole('button', { name: 'Remove Type breakdown' }).click();
  await expect.poll(() => writes).toBe(1);
  await expect(types).toHaveCount(0);
  await expect(priority).toBeVisible();
  expect(saved.items[0].children.map((child: any) => child.id)).toEqual(['task-priority']);
});

test('shows existing groups and their widgets as a collapsible hierarchy inside Widgets', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'group', id: 'group-project-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 9, children: [
      { kind: 'widget', id: 'overdue-tasks', x: 0, y: 0, w: 6, h: 4 },
      { kind: 'widget', id: 'needs-attention', x: 6, y: 0, w: 6, h: 4 },
      { kind: 'widget', id: 'overall-progress', x: 0, y: 4, w: 6, h: 4 },
      { kind: 'widget', id: 'task-completion-rate', x: 6, y: 4, w: 6, h: 4 },
    ] },
    { kind: 'widget', id: 'audit', x: 0, y: 9, w: 6, h: 5 },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); writes += 1; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const palette = page.getByLabel('Dashboard widget palette');
  const addWidgets = palette.getByLabel('Add widgets');
  const addBox = await addWidgets.boundingBox();
  const layersBox = await palette.getByLabel('Widgets on dashboard').boundingBox();
  expect(addBox!.y).toBeLessThan(layersBox!.y);
  const group = palette.getByTestId('widget-panel-group-group-project-overview');
  const children = group.getByRole('list', { name: 'Widgets in Project Overview' });
  await expect(group.getByRole('button', { name: 'Collapse Project Overview' })).toBeVisible();
  await expect(children.getByRole('listitem')).toHaveText([
    'Overdue Tasks', 'Needs Attention', 'Overall Progress', 'Task Completion Rate',
  ]);
  await expect(palette.getByTestId('widget-panel-item-audit')).toBeVisible();
  await expect(addWidgets.getByRole('button', { name: 'Add Overdue Tasks' })).toHaveCount(0);
  await expect(addWidgets.getByRole('button', { name: 'Add Tasks Completed' })).toBeVisible();
  await group.getByRole('button', { name: 'Collapse Project Overview' }).click();
  await expect(children).toHaveCount(0);
  await group.getByRole('button', { name: 'Expand Project Overview' }).click();
  await expect(children.getByRole('listitem')).toHaveCount(4);
  await group.getByRole('button', { name: 'Add widget to Project Overview' }).click();
  await group.getByLabel('Add widgets to Project Overview').getByRole('button', { name: '+ Tasks Completed' }).click();
  await expect.poll(() => writes).toBe(1);
  await expect(children.getByRole('listitem')).toHaveCount(5);
  expect(saved.items[0].children.map((child: any) => child.id)).toContain('tasks-completed');
  await expect(addWidgets.getByRole('button', { name: 'Add Tasks Completed' })).toHaveCount(0);
  await group.getByRole('button', { name: 'Remove Tasks Completed from Widgets' }).click();
  await expect.poll(() => writes).toBe(2);
  await expect(children.getByRole('listitem')).toHaveCount(4);
  await expect(addWidgets.getByRole('button', { name: 'Add Tasks Completed' })).toBeVisible();
  await drag(page, addWidgets.getByRole('button', { name: 'Drag Tasks Completed onto dashboard' }), group.getByRole('button', { name: 'Collapse Project Overview' }));
  await expect.poll(() => writes).toBe(3);
  await expect(group.getByTestId('widget-panel-item-tasks-completed')).toBeVisible();
  await group.getByRole('button', { name: 'Remove Tasks Completed from Widgets' }).click();
  await expect.poll(() => writes).toBe(4);
  await drag(page, palette.getByRole('button', { name: 'Move Audit in Widgets' }), group.getByRole('button', { name: 'Collapse Project Overview' }));
  await expect.poll(() => writes).toBe(5);
  await expect(group.getByTestId('widget-panel-item-audit')).toBeVisible();
  expect(saved.items[0].children.map((child: any) => child.id)).toContain('audit');
  await drag(page, group.getByRole('button', { name: 'Move Audit in Widgets' }), palette.getByTestId('widget-panel-ungroup-drop'));
  await expect.poll(() => writes).toBe(6);
  await expect(group.getByTestId('widget-panel-item-audit')).toHaveCount(0);
  await expect(palette.getByTestId('widget-panel-item-audit')).toBeVisible();
  expect(saved.items[0].children.map((child: any) => child.id)).not.toContain('audit');
  await group.getByRole('button', { name: 'Edit group Project Overview in Widgets' }).click();
  await expect(page.getByLabel('Selected item settings').getByLabel('Title')).toHaveValue('Project Overview');
});

test('keeps View all on the title row of the task condition cards in edit mode', async ({ page }) => {
  await setup(page);
  const document = { version: 3, items: [{
    kind: 'group', id: 'group-task-condition', title: 'Task Condition', x: 0, y: 0, w: 12, h: 11,
    children: [
      { kind: 'widget', id: 'decisions', x: 0, y: 0, w: 4, h: 10 },
      { kind: 'widget', id: 'operations', x: 4, y: 0, w: 4, h: 10 },
      { kind: 'widget', id: 'tasks', x: 8, y: 0, w: 4, h: 10 },
    ],
  }] };
  await page.route('**/api/dashboard/layout/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(document) });
  });
  await page.setViewportSize({ width: 1920, height: 900 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  for (const [id, title] of [['decisions', 'Decisions'], ['operations', 'Operations'], ['tasks', 'Tasks']]) {
    const tile = page.getByTestId(`dashboard-widget-${id}`);
    const titleBox = await tile.getByText(title, { exact: true }).first().boundingBox();
    const linkBox = await tile.getByRole('link', { name: 'View all', exact: true }).boundingBox();
    expect(titleBox).not.toBeNull();
    expect(linkBox).not.toBeNull();
    expect(Math.abs(titleBox!.y - linkBox!.y)).toBeLessThan(6);
  }
});

test('inserts a whole group between two groups with a line preview and keeps its children', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'group', id: 'group-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 6, children: [
      { kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
    ] },
    { kind: 'group', id: 'group-data', title: 'Data', x: 0, y: 6, w: 12, h: 6, children: [
      { kind: 'widget', id: 'task-completion-rate', x: 0, y: 0, w: 6, h: 4 },
    ] },
    { kind: 'group', id: 'group-new', title: 'New group', x: 0, y: 12, w: 12, h: 6, children: [
      { kind: 'widget', id: 'audit', x: 0, y: 0, w: 6, h: 4 },
    ] },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { saved = route.request().postDataJSON(); writes += 1; }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const palette = page.getByLabel('Dashboard widget palette');
  const data = palette.getByTestId('widget-panel-group-group-data');
  await drag(page, palette.getByRole('button', { name: 'Move group New group in Widgets' }),
    data.getByRole('button', { name: 'Collapse Data' }), async () => {
      await expect(data.getByTestId('group-insert-indicator')).toHaveAttribute('data-side', 'before');
      await expect(data).toHaveClass(/border-gray-200/);
    });
  await expect.poll(() => writes).toBe(1);
  expect(saved.items.map((item: any) => item.id)).toEqual(['group-overview', 'group-new', 'group-data']);
  expect(saved.items.map((item: any) => item.y)).toEqual([0, 6, 12]);
  expect(saved.items.find((item: any) => item.id === 'group-overview').children[0].id).toBe('overall-progress');
  expect(saved.items.find((item: any) => item.id === 'group-data').children[0].id).toBe('task-completion-rate');
  expect(saved.items.find((item: any) => item.id === 'group-new').children[0].id).toBe('audit');
});

test('inserts a whole group between two independent widgets with a line preview', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'widget', id: 'activity', x: 0, y: 0, w: 6, h: 4 },
    { kind: 'widget', id: 'audit', x: 6, y: 0, w: 6, h: 4 },
    { kind: 'group', id: 'group-new', title: 'New group', x: 0, y: 4, w: 12, h: 5, children: [
      { kind: 'widget', id: 'overdue-tasks', x: 0, y: 0, w: 6, h: 4 },
    ] },
  ] };
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') saved = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const palette = page.getByLabel('Dashboard widget palette');
  const audit = palette.getByTestId('widget-panel-item-audit');
  const groupHandle = palette.getByRole('button', { name: 'Move group New group in Widgets' });
  await groupHandle.scrollIntoViewIfNeeded();
  await audit.scrollIntoViewIfNeeded();
  const from = await groupHandle.boundingBox();
  const to = await audit.boundingBox();
  expect(from && to).toBeTruthy();
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2);
  await page.mouse.down();
  await page.mouse.move(to!.x + to!.width / 2, to!.y + 4, { steps: 12 });
  await expect(palette.getByTestId('group-insert-indicator')).toHaveAttribute('data-side', 'before');
  await page.mouse.up();
  await expect.poll(() => saved.items.map((item: any) => item.id)).toEqual(['activity', 'group-new', 'audit']);
  expect(saved.items.map((item: any) => item.y)).toEqual([0, 4, 9]);
  expect(saved.items[1].children.map((item: any) => item.id)).toEqual(['overdue-tasks']);
});

test('inserts an independent widget between two top-level groups at the blue line', async ({ page }) => {
  await setup(page);
  let saved: any = { version: 3, items: [
    { kind: 'group', id: 'group-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 5, children: [
      { kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
    ] },
    { kind: 'group', id: 'group-summary', title: 'Module Summary', x: 0, y: 5, w: 12, h: 5, children: [
      { kind: 'widget', id: 'tasks-completed', x: 0, y: 0, w: 6, h: 4 },
    ] },
    { kind: 'widget', id: 'audit', x: 0, y: 10, w: 6, h: 4 },
  ] };
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') saved = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  const palette = page.getByLabel('Dashboard widget palette');
  const source = palette.getByRole('button', { name: 'Move Audit in Widgets' });
  const summary = palette.getByTestId('widget-panel-group-group-summary');
  await summary.scrollIntoViewIfNeeded();
  await source.scrollIntoViewIfNeeded();
  const from = await source.boundingBox();
  const to = await summary.boundingBox();
  expect(from && to).toBeTruthy();
  await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2);
  await page.mouse.down();
  await page.mouse.move(to!.x + to!.width / 2, to!.y - 3, { steps: 12 });
  await expect(summary.getByTestId('widget-insert-indicator')).toHaveAttribute('data-side', 'before');
  await page.mouse.up();
  await expect.poll(() => saved.items.map((item: any) => item.id)).toEqual(['group-overview', 'audit', 'group-summary']);
  expect(saved.items.map((item: any) => item.y)).toEqual([0, 5, 9]);
  expect(saved.items[0].children.map((item: any) => item.id)).toEqual(['overall-progress']);
  expect(saved.items[2].children.map((item: any) => item.id)).toEqual(['tasks-completed']);
});

test('opens and closes Layout, moves cards into a group, and persists edits', async ({ page }) => {
  test.setTimeout(120_000);
  await setup(page);
  let persisted: any = { widgets: [
    { id: 'audit', x: 0, y: 0, w: 6, h: 5 },
    { id: 'activity', x: 6, y: 0, w: 6, h: 5 },
  ] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { writes += 1; persisted = route.request().postDataJSON(); }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(persisted) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await expect(page.getByTestId('dashboard-widget-audit')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
  await expect(page.getByText('Use the edit button to add or edit cards.')).toHaveCount(0);
  await expect(page.getByTestId('dashboard-widget-activity').getByRole('button', { name: 'View all activity', exact: true })).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-audit').getByRole('button', { name: 'View all actions', exact: true })).toBeVisible();
  const editLayout = page.getByRole('button', { name: 'Edit dashboard layout' });
  await expect(editLayout).toBeVisible();
  await editLayout.click();
  await expect(editLayout).toHaveCount(0);
  await expect(page.getByLabel('Widgets on dashboard').getByTestId('widget-panel-item-audit')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Drag Audit onto dashboard' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'New titled group' })).toBeVisible();
  await page.getByRole('button', { name: 'New titled group' }).click();
  await expect.poll(() => writes).toBe(1);
  const group = page.locator('[data-testid^="dashboard-group-group-"]').first();
  await expect(group).toBeVisible();
  await expect(group).toHaveCSS('outline-style', 'dashed');
  await expect(group.getByRole('button', { name: 'Move group New group' })).toBeVisible();
  await drag(page, page.getByRole('button', { name: 'Move Recent activity', exact: true }), group.locator('[aria-label^="Drop widgets into"]'), async () => {
    await expect(group).toHaveAttribute('data-drop-target', 'true');
    await expect(group).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect(page.getByTestId('dashboard-drop-preview')).toBeVisible();
    await expect(page.getByTestId('dashboard-canvas')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  });
  await expect(group).not.toHaveAttribute('data-drop-target', 'true');
  await expect.poll(() => writes).toBe(2);
  await expect(group.getByTestId('dashboard-widget-activity')).toBeVisible();
  expect(persisted.items.find((item: any) => item.kind === 'group').children.map((item: any) => item.id)).toContain('activity');
  await expect(page.getByLabel('Dashboard layers')).toHaveCount(0);
  await expect(group.getByTestId('dashboard-widget-activity').getByRole('button', { name: 'Edit Recent activity' })).toHaveCount(0);
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await group.getByRole('button', { name: 'Edit group New group' }).click();
  const settings = page.getByLabel('Selected item settings');
  await expect(group).toHaveCSS('outline-style', 'none');
  await expect(group).not.toHaveCSS('box-shadow', 'none');
  await settings.getByLabel('Title').fill('My Project Overview');
  await settings.getByLabel('Title').blur();
  await expect.poll(() => writes).toBe(3);
  const resize = group.getByRole('button', { name: 'Resize Recent activity' });
  await resize.focus();
  await page.keyboard.press('ArrowDown');
  await expect.poll(() => writes).toBe(4);
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close layout panel' }).click();
  await expect(page.getByLabel('Dashboard item editor')).toHaveCount(0);
  await expect(page.getByLabel('Dashboard widget palette')).toHaveCount(0);
  await expect(group).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(group).toHaveCSS('box-shadow', 'none');
  await expect(group).toHaveCSS('outline-style', 'none');
  await expect(group.locator('svg.lucide-layers-3')).toHaveCount(0);
  const titleBox = await group.getByText('My Project Overview').boundingBox();
  const groupBox = await group.boundingBox();
  expect(titleBox!.x - groupBox!.x).toBeLessThan(16);
  await expect(editLayout).toBeVisible();
  await editLayout.click();
  await expect(editLayout).toHaveCount(0);
  await expect(page.getByLabel('Dashboard widget palette')).toBeVisible();
  await page.reload();
  await expect(page.getByText('My Project Overview')).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-audit')).toBeVisible();
  await expect(page.getByTestId('dashboard-widget-activity')).toBeVisible();
  expect(writes).toBe(4);
});

test('keeps business cards usable while moving a widget out of a group', async ({ page }) => {
  test.setTimeout(120_000);
  await setup(page);
  const document = { version: 2, items: [
    { kind: 'group', id: 'group-project-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 7,
      children: [{ kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 4, h: 4 }] },
    { kind: 'widget', id: 'custom-kpis', x: 0, y: 7, w: 12, h: 7 },
    { kind: 'widget', id: 'meetings', x: 0, y: 14, w: 6, h: 8 },
  ] };
  let saved: any = document;
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') { writes += 1; saved = route.request().postDataJSON(); }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await expect(page.getByTestId('dashboard-widget-meetings')).toBeVisible();
  await drag(page, page.getByRole('button', { name: 'Move Overall Progress', exact: true }), page.getByTestId('dashboard-widget-custom-kpis'));
  await expect.poll(() => writes).toBe(1);
  expect(saved.items.find((item: any) => item.kind === 'group').children).toHaveLength(0);
  expect(saved.items.filter((item: any) => item.id === 'overall-progress')).toHaveLength(1);
});

test('treats a titled group as a movable container while keeping its widgets movable', async ({ page }) => {
  test.setTimeout(120_000);
  await setup(page);
  let saved: any = { version: 2, items: [
    { kind: 'group', id: 'group-project-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 6, children: [] },
    { kind: 'widget', id: 'audit', x: 0, y: 6, w: 6, h: 5 },
  ] };
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') saved = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  const group = page.getByTestId('dashboard-group-group-project-overview');
  await expect(group.getByRole('button', { name: 'Move group Project Overview' })).toBeVisible();
  await expect(group.getByRole('button', { name: 'Resize Project Overview' })).toHaveCount(0);
  await expect(group.getByText('Project Overview')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Move Audit', exact: true })).toBeVisible();
});

test('keeps a new group locally after a rejected save and retries without losing it', async ({ page }) => {
  test.setTimeout(120_000);
  await setup(page);
  let saved: any = { widgets: [{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }] };
  let writes = 0;
  await page.route('**/api/dashboard/layout/**', async (route) => {
    if (route.request().method() === 'PUT') {
      writes += 1;
      if (writes === 1) {
        await route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ items: ['Unsupported layout version'] }) });
        return;
      }
      saved = route.request().postDataJSON();
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(saved) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: 'Edit dashboard layout' }).click();
  await page.getByRole('button', { name: 'New titled group' }).click();
  await expect.poll(() => writes).toBe(1);
  await expect(page.getByRole('alert').filter({ hasText: 'Could not save layout' })).toContainText('items: Unsupported layout version');
  await expect(page.locator('[data-testid^="dashboard-group-group-"]')).toBeVisible();
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect.poll(() => writes).toBe(2);
  await expect(page.getByText('Saved', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('[data-testid^="dashboard-group-group-"]')).toBeVisible();
});

test('Hide Panel actually collapses the right meetings sidebar and Show Panel restores it', async ({ page }) => {
  test.setTimeout(60_000);
  await setup(page);
  await page.route('**/api/dashboard/layout/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: 2, items: [] }) });
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/dashboard-test-org/med-287/overview', { waitUntil: 'domcontentloaded', timeout: 90_000 });
  await waitForLayoutMain(page);
  const meetings = page.locator('[data-upcoming-meetings-panel]');
  await expect(page.getByRole('button', { name: 'Hide Panel' })).toBeVisible();
  await expect(meetings).toBeVisible();
  await page.getByRole('button', { name: 'Hide Panel' }).click();
  await expect(page.getByRole('button', { name: 'Show Panel' })).toBeVisible();
  await expect(meetings).toHaveCount(0);
  await expect(page.getByText('Upcoming Meetings', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Show Panel' }).click();
  await expect(meetings).toBeVisible();
});
