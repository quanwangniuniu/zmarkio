import { ROW_STEP, addGroup, addWidget, expandLegacyWorkspace, fitWidgetToVacancy, legacyDocument, moveLayoutItem, moveWidget, placeWidgetInContainer, removeLayoutItem, removeWidget, reorderLayoutGroup, resizeLayoutItem, resizeWidget, snapNearPrevious, updateLayoutItem } from '@/components/dashboard/builder/layoutReducer';
import type { DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';
import type { DashboardLayoutDocument } from '@/types/dashboardLayout';

const widgets: Widget[] = [
  { id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
  { id: 'decisions', x: 6, y: 0, w: 6, h: 4 },
  { id: 'task-status', x: 0, y: 4, w: 6, h: 4 },
];

it('snaps a move into the grid and pushes collided widgets below it', () => {
  const result = moveWidget(widgets, 'decisions', -6, 0);
  expect(result.find((item) => item.id === 'decisions')).toMatchObject({ x: 0, y: 0 });
  expect(result.find((item) => item.id === 'overall-progress')).toMatchObject({ x: 0, y: 4 });
  expect(result.find((item) => item.id === 'task-status')).toMatchObject({ x: 0, y: 8 });
});

it('constrains resize to grid bounds and moves any widget it overlaps', () => {
  const result = resizeWidget(widgets, 'overall-progress', 8, 1);
  expect(result.find((item) => item.id === 'overall-progress')).toMatchObject({ w: 12, h: 5 });
  expect(result.find((item) => item.id === 'decisions')?.y).toBeGreaterThanOrEqual(5);
});

it('adds and removes widget types without changing other widgets', () => {
  const newWidget: Widget = { id: 'meetings', x: 0, y: 0, w: 6, h: 5 };
  const added = addWidget(widgets, newWidget);
  expect(added.find((item) => item.id === 'meetings')?.y).toBe(8);
  expect(addWidget(added, newWidget)).toBe(added);
  expect(removeWidget(added, 'meetings')).toEqual(widgets);
});

it('does not change layout for a zero movement', () => {
  expect(moveWidget(widgets, 'overall-progress', 0, 0)).toBe(widgets);
});

it('expands an old workspace into independent cards without overlapping existing widgets', () => {
  const old: Widget[] = [
    { id: 'workspace', x: 0, y: 0, w: 6, h: 12 },
    { id: 'audit', x: 6, y: 0, w: 6, h: 6 },
    { id: 'activity', x: 6, y: 6, w: 6, h: 6 },
  ];
  const replacements: Widget[] = [
    { id: 'overall-progress', x: 0, y: 0, w: 3, h: 4 },
    { id: 'tasks-completed', x: 3, y: 0, w: 2, h: 4 },
    { id: 'task-trend', x: 0, y: 27, w: 12, h: 8 },
  ];
  const expanded = expandLegacyWorkspace(old, replacements);
  expect(expanded.map((widget) => widget.id)).toEqual(['overall-progress', 'tasks-completed', 'task-trend', 'audit', 'activity']);
  expect(expanded.find((widget) => widget.id === 'audit')?.y).toBe(35);
  expect(expanded.find((widget) => widget.id === 'activity')?.y).toBe(41);
  expect(old[0].id).toBe('workspace');
});

it('keeps the dropped card at its snapped target while neighbors move into gaps above', () => {
  const spread: Widget[] = [
    { id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
    { id: 'tasks-completed', x: 6, y: 0, w: 6, h: 4 },
    { id: 'decisions', x: 0, y: 8, w: 6, h: 4 },
    { id: 'operations', x: 6, y: 8, w: 6, h: 4 },
  ];
  const result = moveWidget(spread, 'overall-progress', 6, 8);
  expect(result.find((item) => item.id === 'overall-progress')).toMatchObject({ x: 6, y: 8 });
  expect(result.find((item) => item.id === 'decisions')).toMatchObject({ x: 0, y: 0 });
  expect(result.find((item) => item.id === 'operations')).toMatchObject({ x: 6, y: 4 });
  expect(result.find((item) => item.id === 'tasks-completed')).toMatchObject({ x: 6, y: 0 });
});

it('fills gaps after a card is removed', () => {
  const spread: Widget[] = [
    { id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
    { id: 'decisions', x: 0, y: 8, w: 6, h: 4 },
  ];
  expect(removeWidget(spread, 'overall-progress')).toEqual([{ id: 'decisions', x: 0, y: 0, w: 6, h: 4 }]);
});

it('keeps content-heavy cards at usable minimum heights', () => {
  const tall: Widget[] = [
    { id: 'project-team', x: 0, y: 0, w: 6, h: 10 },
    { id: 'meetings', x: 6, y: 0, w: 6, h: 8 },
  ];
  expect(resizeWidget(tall, 'project-team', 0, -20).find((item) => item.id === 'project-team')?.h).toBe(6);
  expect(resizeWidget(tall, 'meetings', 0, -20).find((item) => item.id === 'meetings')?.h).toBe(4);
});

const grouped: DashboardLayoutDocument = {
  version: 2,
  items: [
    { kind: 'group', id: 'group-overview', title: 'Project Overview', x: 0, y: 0, w: 12, h: 6,
      children: [{ kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 }] },
    { kind: 'widget', id: 'audit', x: 0, y: 8, w: 6, h: 5 },
  ],
};

it('moves existing widgets into and out of a one-level group without duplicates', () => {
  const inside = placeWidgetInContainer(grouped, { kind: 'widget', id: 'audit', x: 0, y: 8, w: 6, h: 5 }, 'group-overview', { x: 6, y: 0 });
  expect(inside.items.filter((item) => item.id === 'audit')).toHaveLength(0);
  expect(inside.items[0].kind === 'group' && inside.items[0].children.map((item) => item.id)).toEqual(['overall-progress', 'audit']);
  const outside = placeWidgetInContainer(inside, { kind: 'widget', id: 'audit', x: 0, y: 0, w: 6, h: 5 }, null);
  expect(outside.items.filter((item) => item.id === 'audit')).toHaveLength(1);
  expect(outside.items[0].kind === 'group' && outside.items[0].children.map((item) => item.id)).toEqual(['overall-progress']);
});

it('moves a group as one item and leaves unrelated cards in place for a clamped drag', () => {
  expect(moveLayoutItem(grouped, 'group-overview', -3, 0)).toBe(grouped);
  const moved = moveLayoutItem(grouped, 'group-overview', 0, 2);
  expect(moved.items.find((item) => item.id === 'group-overview')?.y).toBe(2);
  expect(moved.items.find((item) => item.id === 'audit')?.y).toBe(8);
});

it('inserts a whole group between two groups without losing its children', () => {
  const document: DashboardLayoutDocument = { version: 3, items: [
    { kind: 'group', id: 'overview', title: 'Overview', x: 0, y: 0, w: 12, h: 6, children: [] },
    { kind: 'group', id: 'data', title: 'Data', x: 0, y: 6, w: 12, h: 7, children: [] },
    { kind: 'group', id: 'new', title: 'New group', x: 0, y: 13, w: 12, h: 5, children: [
      { kind: 'widget', id: 'audit', x: 0, y: 0, w: 6, h: 4 },
    ] },
  ] };
  const before = reorderLayoutGroup(document, 'new', 'data', 'before');
  expect(before.items.map((item) => [item.id, item.y])).toEqual([['overview', 0], ['new', 6], ['data', 11]]);
  expect(before.items[1]).toMatchObject({ id: 'new', children: [{ id: 'audit' }] });
  const after = reorderLayoutGroup(document, 'overview', 'data', 'after');
  expect(after.items.map((item) => [item.id, item.y])).toEqual([['data', 0], ['overview', 7], ['new', 13]]);
  expect(reorderLayoutGroup(document, 'data', 'data', 'before')).toBe(document);
});

it('resizes children and their group, edits settings, and ungrouping keeps business cards', () => {
  const taller = resizeLayoutItem(grouped, 'overall-progress', 0, 3);
  const group = taller.items.find((item) => item.id === 'group-overview');
  expect(group?.h).toBe(8);
  const renamed = updateLayoutItem(taller, 'overall-progress', { title: 'Progress this week', accent: 'lime' });
  expect(renamed.items[0].kind === 'group' && renamed.items[0].children[0]).toMatchObject({ title: 'Progress this week', settings: { accent: 'lime' } });
  const ungrouped = removeLayoutItem(renamed, 'group-overview');
  expect(ungrouped.items.map((item) => item.id).sort()).toEqual(['audit', 'overall-progress']);
});

it('creates an empty titled group and converts legacy flat rows without losing widgets', () => {
  expect(addGroup(grouped, 'group-new').items.at(-1)).toMatchObject({ id: 'group-new', title: 'New group', children: [] });
  const old = legacyDocument([{ id: 'audit', x: 0, y: 0, w: 6, h: 5 }], []);
  expect(old).toEqual({ version: 2, items: [{ kind: 'widget', id: 'audit', x: 0, y: 0, w: 6, h: 5 }] });
});

it('snaps within 20 pixels of the preceding card but retains a larger manual gap', () => {
  const peers: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 5 },
    { id: 'meetings', x: 0, y: 8, w: 6, h: 5 },
  ];
  expect(snapNearPrevious(peers, 'meetings', 0, 6, 5 * ROW_STEP + 19)).toBe(5);
  expect(snapNearPrevious(peers, 'meetings', 0, 6, 5 * ROW_STEP + 21)).toBeNull();
  expect(snapNearPrevious(peers, 'meetings', 6, 6, 5 * ROW_STEP + 10)).toBeNull();
});

it('fits a new widget to the full vacant width beside existing group cards', () => {
  const peers: Widget[] = [
    { id: 'task-completion-rate', x: 3, y: 0, w: 3, h: 4 },
    { id: 'needs-attention', x: 6, y: 0, w: 6, h: 4 },
  ];
  const incoming: Widget = { id: 'overall-progress', x: 0, y: 0, w: 4, h: 4 };
  expect(fitWidgetToVacancy(peers, incoming, { x: 1, y: 0 })).toMatchObject({ x: 0, y: 0, w: 3, h: 4 });
  expect(peers[0]).toMatchObject({ x: 3, w: 3 });
  expect(incoming.w).toBe(4);
  expect(fitWidgetToVacancy([{ id: 'left', x: 0, y: 0, w: 4, h: 4 }, { id: 'right', x: 8, y: 0, w: 4, h: 4 }],
    { ...incoming, w: 2 }, { x: 5, y: 0 })).toMatchObject({ x: 4, w: 4 });
});

it('keeps default width when dropping on an occupied cell or a completely empty row', () => {
  const peer: Widget = { id: 'task-completion-rate', x: 3, y: 0, w: 3, h: 4 };
  const incoming: Widget = { id: 'overall-progress', x: 0, y: 0, w: 4, h: 4 };
  expect(fitWidgetToVacancy([peer], incoming, { x: 3, y: 0 })).toMatchObject({ x: 3, w: 4 });
  expect(fitWidgetToVacancy([peer], incoming, { x: 1, y: 8 })).toMatchObject({ x: 1, y: 8, w: 4 });
});

it('closes a removed card’s vertical space through a stack without moving another column', () => {
  const document: DashboardLayoutDocument = { version: 2, items: [
    { kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
    { kind: 'widget', id: 'decisions', x: 0, y: 4, w: 6, h: 4 },
    { kind: 'widget', id: 'task-status', x: 0, y: 8, w: 6, h: 4 },
    { kind: 'widget', id: 'audit', x: 6, y: 8, w: 6, h: 5 },
  ] };
  const removed = removeLayoutItem(document, 'overall-progress');
  expect(removed.items.map(({ id, y }) => [id, y])).toEqual([
    ['decisions', 0], ['task-status', 4], ['audit', 8],
  ]);
  expect(document.items[1].y).toBe(4);
});

it('pulls cards up after a height reduction but preserves an existing deliberate gap', () => {
  const document: DashboardLayoutDocument = { version: 2, items: [
    { kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 8 },
    { kind: 'widget', id: 'decisions', x: 0, y: 10, w: 6, h: 4 },
    { kind: 'widget', id: 'task-status', x: 0, y: 14, w: 6, h: 4 },
  ] };
  const shrunk = resizeLayoutItem(document, 'overall-progress', 0, -4);
  expect(shrunk.items.map(({ id, y }) => [id, y])).toEqual([
    ['overall-progress', 0], ['decisions', 6], ['task-status', 10],
  ]);
});

it('keeps a manually dragged widget at its lower target while other cards fill its old slot', () => {
  const document: DashboardLayoutDocument = { version: 2, items: [
    { kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
    { kind: 'widget', id: 'decisions', x: 0, y: 4, w: 6, h: 4 },
    { kind: 'widget', id: 'task-status', x: 0, y: 8, w: 6, h: 4 },
  ] };
  const moved = moveLayoutItem(document, 'decisions', 0, 8);
  expect(moved.items.map(({ id, y }) => [id, y])).toEqual([
    ['overall-progress', 0], ['task-status', 4], ['decisions', 12],
  ]);
});

it('shrinks a group when a child leaves and brings following canvas cards along', () => {
  const document: DashboardLayoutDocument = { version: 2, items: [
    { kind: 'group', id: 'group-first', title: 'First', x: 0, y: 0, w: 12, h: 10, children: [
      { kind: 'widget', id: 'overall-progress', x: 0, y: 0, w: 6, h: 4 },
      { kind: 'widget', id: 'decisions', x: 0, y: 4, w: 6, h: 5 },
    ] },
    { kind: 'widget', id: 'audit', x: 6, y: 10, w: 6, h: 5 },
  ] };
  const moved = placeWidgetInContainer(document, { kind: 'widget', id: 'decisions', x: 0, y: 4, w: 6, h: 5 }, null);
  const group = moved.items.find((item) => item.id === 'group-first');
  expect(group?.h).toBe(5);
  expect(moved.items.find((item) => item.id === 'audit')?.y).toBe(5);
  expect(moved.items.filter((item) => item.id === 'decisions')).toHaveLength(1);
});
