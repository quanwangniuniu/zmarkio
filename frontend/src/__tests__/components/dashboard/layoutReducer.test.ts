import { addWidget, compactWidgets, dropWidget, moveWidget, removeWidget, resizeDelta, resizeWidget } from '@/components/dashboard/builder/layoutReducer';
import { MAX_SECTION_TITLES } from '@/components/dashboard/builder/sectionTitle';
import type { DashboardWidgetPosition as Widget } from '@/types/dashboardLayout';

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

it('adds and removes widget types in a compact layout', () => {
  const newWidget: Widget = { id: 'meetings', x: 0, y: 0, w: 6, h: 5 };
  const added = addWidget(widgets, newWidget);
  expect(added.find((item) => item.id === 'meetings')?.y).toBe(8);
  expect(addWidget(added, newWidget)).toBe(added);
  expect(removeWidget(added, 'meetings')).toEqual(widgets);
});

it('does not change layout for a zero movement', () => {
  expect(moveWidget(widgets, 'overall-progress', 0, 0)).toBe(widgets);
});

it('moves a resized card left without shifting neighbors sideways and closes vertical gaps', () => {
  const layout: Widget[] = [
    { id: 'task-completion-rate', x: 0, y: 0, w: 2.7, h: 3 },
    { id: 'overdue-tasks', x: 3, y: 0, w: 2.5, h: 3 },
    { id: 'audit', x: 0, y: 8, w: 6, h: 4 },
  ];
  const delta = resizeDelta(-10, 0, 80);
  const result = moveWidget(layout, 'overdue-tasks', delta.dw, delta.dh);
  expect(result.find((item) => item.id === 'overdue-tasks')).toMatchObject({ x: 2.875, y: 0, w: 2.5 });
  expect(result.find((item) => item.id === 'task-completion-rate')).toEqual(layout[0]);
  expect(result.find((item) => item.id === 'audit')).toEqual({ ...layout[2], y: 3 });
});

it('stops small moves at neighboring edges instead of pushing those cards down', () => {
  const layout: Widget[] = [
    { id: 'task-completion-rate', x: 0, y: 0, w: 2.7, h: 3 },
    { id: 'overdue-tasks', x: 3, y: 0, w: 2.5, h: 3 },
    { id: 'audit', x: 0, y: 8, w: 6, h: 4 },
  ];
  const left = moveWidget(layout, 'overdue-tasks', -0.5, 0);
  expect(left.find((item) => item.id === 'overdue-tasks')).toMatchObject({ x: 2.7, y: 0 });
  expect(left.find((item) => item.id === 'task-completion-rate')).toEqual(layout[0]);
  expect(left.find((item) => item.id === 'audit')).toEqual({ ...layout[2], y: 3 });
  const right = moveWidget(layout, 'task-completion-rate', 0.5, 0);
  expect(right.find((item) => item.id === 'task-completion-rate')?.x).toBeCloseTo(0.3);
  expect(right.find((item) => item.id === 'overdue-tasks')).toEqual(layout[1]);
  const diagonal = moveWidget(layout, 'overdue-tasks', -0.5, 1 / 6);
  expect(diagonal.find((item) => item.id === 'overdue-tasks')).toMatchObject({ x: 2.7, y: 0 });
  expect(diagonal.find((item) => item.id === 'task-completion-rate')).toEqual(layout[0]);
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

it('drops a new preset at the requested column and snaps upward while retaining its dimensions', () => {
  const incoming = { id: 'task-types', x: 8, y: 18, w: 4, h: 9 };
  const dropped = dropWidget(widgets, incoming, 11, 3);
  expect(dropped.find((item) => item.id === incoming.id)).toEqual({ ...incoming, x: 8, y: 0 });
  expect(dropWidget(dropped, incoming, 0, 0)).toBe(dropped);
  expect(widgets).toHaveLength(3);
});

it('preserves independent title text through moving, resizing and deleting another title', () => {
  const titles: Widget[] = [
    { id: 'section-title-first', title: 'Overview', x: 0, y: 0, w: 12, h: 1 },
    { id: 'audit', x: 0, y: 1, w: 6, h: 4 },
    { id: 'section-title-second', title: 'Updates', x: 0, y: 5, w: 12, h: 1 },
  ];
  const moved = moveWidget(titles, 'section-title-second', 0, -5);
  expect(moved.find((item) => item.id === 'section-title-second')).toMatchObject({ title: 'Updates', y: 0 });
  const resized = resizeWidget(moved, 'section-title-second', -6, -10);
  expect(resized.find((item) => item.id === 'section-title-second')).toMatchObject({ title: 'Updates', w: 6, h: 1 });
  const removed = removeWidget(resized, 'section-title-first');
  expect(removed.some((item) => item.id === 'section-title-first')).toBe(false);
  expect(removed.find((item) => item.id === 'section-title-second')?.title).toBe('Updates');
});

it('resizes in 10px increments while preserving minimum size and non-overlapping neighbors', () => {
  const baseline: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 5 },
    { id: 'activity', x: 6, y: 0, w: 6, h: 5 },
  ];
  const { dw, dh } = resizeDelta(12, 8, 80);
  expect(dw * 80).toBe(10);
  expect(dh * 60).toBe(10);
  expect(resizeDelta(4, -4, 80)).toEqual({ dw: 0, dh: -0 });
  const result = resizeWidget(baseline, 'audit', dw, dh);
  expect(result.find((widget) => widget.id === 'audit')).toMatchObject({ w: 6.125, h: 5 + 10 / 60 });
  expect(result.find((widget) => widget.id === 'activity')?.y).toBe(5 + 10 / 60);
  const shrunk = resizeWidget(result, 'audit', -100, -100);
  expect(shrunk.find((widget) => widget.id === 'audit')).toMatchObject({ w: 1, h: 4 });
});

it('closes gaps above successive titles and shifts their content without changing widths or order', () => {
  const layout: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 5.5, h: 4 },
    { id: 'activity', x: 6, y: 0, w: 6, h: 6 },
    { id: 'section-title-first', title: 'Modules', x: 0, y: 15, w: 12, h: 1 },
    { id: 'tasks', x: 0, y: 16, w: 4, h: 7 },
    { id: 'operations', x: 4, y: 16, w: 4, h: 7 },
    { id: 'section-title-second', title: 'More', x: 0, y: 30, w: 12, h: 1 },
    { id: 'decisions', x: 0, y: 31, w: 4, h: 7 },
  ];
  const moved = moveWidget(layout, 'audit', 0.125, 0);
  expect(moved.find((item) => item.id === 'activity')).toEqual(layout[1]);
  expect(moved.find((item) => item.id === 'section-title-first')).toEqual({ ...layout[2], y: 6 });
  expect(moved.find((item) => item.id === 'tasks')).toEqual({ ...layout[3], y: 7 });
  expect(moved.find((item) => item.id === 'operations')).toEqual({ ...layout[4], y: 7 });
  expect(moved.find((item) => item.id === 'section-title-second')).toEqual({ ...layout[5], y: 14 });
  expect(moved.find((item) => item.id === 'decisions')).toEqual({ ...layout[6], y: 15 });
  expect(layout[2].y).toBe(15);
});

it('brings a title and its content upward when the preceding card shrinks', () => {
  const layout: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 8 },
    { id: 'section-title-next', title: 'Next', x: 0, y: 10, w: 12, h: 1 },
    { id: 'tasks', x: 0, y: 11, w: 4, h: 7 },
  ];
  const resized = resizeWidget(layout, 'audit', 0, -4);
  expect(resized.find((item) => item.id === 'section-title-next')?.y).toBe(4);
  expect(resized.find((item) => item.id === 'tasks')).toEqual({ ...layout[2], y: 5 });
});

it('snaps a dragged title upward when there is no preceding component', () => {
  const title: Widget = { id: 'section-title-heading', title: 'Heading', x: 0, y: 0, w: 12, h: 1 };
  expect(moveWidget([title], title.id, 0, 5)).toEqual([title]);
});

it('makes ordinary cards follow a shrinking card through the whole column', () => {
  const layout: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 8 },
    { id: 'activity', x: 0, y: 10, w: 6, h: 4 },
    { id: 'meetings', x: 0, y: 18, w: 6, h: 4 },
    { id: 'tasks', x: 6, y: 0, w: 6, h: 7 },
  ];
  const result = resizeWidget(layout, 'audit', 0, -4);
  expect(result.find((widget) => widget.id === 'activity')).toEqual({ ...layout[1], y: 4 });
  expect(result.find((widget) => widget.id === 'meetings')).toEqual({ ...layout[2], y: 8 });
  expect(result.find((widget) => widget.id === 'tasks')).toEqual(layout[3]);
  expect(layout[1].y).toBe(10);
});

it('follows the lowest bottom among horizontally overlapping predecessors and does not cross titles', () => {
  const layout: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 4 + 1 / 6 },
    { id: 'activity', x: 6, y: 0, w: 6, h: 6 },
    { id: 'meetings', x: 3, y: 12, w: 6, h: 4 },
    { id: 'section-title-next', title: 'Next', x: 0, y: 20, w: 3, h: 1 },
    { id: 'tasks', x: 6, y: 25, w: 6, h: 7 },
  ];
  const result = compactWidgets(layout);
  expect(result.find((widget) => widget.id === 'meetings')).toEqual({ ...layout[2], y: 6 });
  expect(result.find((widget) => widget.id === 'section-title-next')).toEqual({ ...layout[3], y: 10 });
  expect(result.find((widget) => widget.id === 'tasks')).toEqual({ ...layout[4], y: 11 });
  expect(compactWidgets(result)).toEqual(result);
});

it('closes the vacated column after moving a card sideways', () => {
  const layout: Widget[] = [
    { id: 'audit', x: 0, y: 0, w: 6, h: 4 },
    { id: 'activity', x: 0, y: 8, w: 6, h: 4 },
    { id: 'meetings', x: 0, y: 16, w: 6, h: 4 },
  ];
  const result = moveWidget(layout, 'audit', 6, 0);
  expect(result.find((widget) => widget.id === 'activity')).toEqual({ ...layout[1], y: 0 });
  expect(result.find((widget) => widget.id === 'meetings')).toEqual({ ...layout[2], y: 4 });
  expect(result.find((widget) => widget.id === 'audit')).toEqual({ ...layout[0], x: 6 });
});

it('stops adding section titles at the limit but still adds other widgets', () => {
  const titles: Widget[] = Array.from({ length: MAX_SECTION_TITLES }, (_, index) => ({
    id: `section-title-${index}`, title: `Section ${index}`, x: 0, y: index, w: 12, h: 1,
  }));
  const extra: Widget = { id: 'section-title-extra', title: 'Extra', x: 0, y: 0, w: 12, h: 1 };
  expect(addWidget(titles, extra)).toBe(titles);
  expect(dropWidget(titles, extra, 0, 0)).toBe(titles);
  expect(addWidget(titles.slice(1), extra)).toHaveLength(MAX_SECTION_TITLES);
  expect(addWidget(titles, { id: 'audit', x: 0, y: 0, w: 6, h: 4 })).toHaveLength(MAX_SECTION_TITLES + 1);
});
