import { widgetInsertionAtY } from '@/components/dashboard/builder/widgetInsertion';

it('shows the boundary between grouped widgets even when the dragged widget is a neighbor', () => {
  const rows = [
    { id: 'task-status', top: 100, bottom: 132 },
    { id: 'overdue-tasks', top: 132, bottom: 164 },
    { id: 'audit', top: 164, bottom: 196 },
  ];
  expect(widgetInsertionAtY(rows, 133, 'audit')).toEqual({ targetId: 'overdue-tasks', side: 'before' });
  expect(widgetInsertionAtY(rows, 163, 'audit')).toEqual({ targetId: 'overdue-tasks', side: 'after' });
  expect(widgetInsertionAtY(rows, 148, 'overdue-tasks')).toBeNull();
});

it('shows a line in the gap between top-level widgets and groups', () => {
  const rows = [
    { id: 'audit', top: 100, bottom: 140 },
    { id: null, top: 146, bottom: 210 },
    { id: 'activity', top: 216, bottom: 256 },
  ];
  expect(widgetInsertionAtY(rows, 143, 'activity')).toEqual({ targetId: 'audit', side: 'after' });
  expect(widgetInsertionAtY(rows, 213, 'audit')).toEqual({ targetId: 'activity', side: 'before' });
  expect(widgetInsertionAtY(rows, 180, 'audit')).toBeNull();
});

it('shows a line between two groups for an independent widget without targeting either group interior', () => {
  const rows = [
    { id: null, groupId: 'overview', top: 100, bottom: 140 },
    { id: null, groupId: 'summary', top: 146, bottom: 186 },
  ];
  expect(widgetInsertionAtY(rows, 143, 'audit')).toEqual({ targetId: 'summary', side: 'before' });
  expect(widgetInsertionAtY(rows, 160, 'audit')).toBeNull();
});
