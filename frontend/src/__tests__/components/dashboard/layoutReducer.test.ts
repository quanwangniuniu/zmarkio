import { addWidget, moveWidget, removeWidget, resizeWidget } from '@/components/dashboard/builder/layoutReducer';
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
