import { groupInsertionAtY } from '@/components/dashboard/builder/groupInsertion';

const rows = [
  { id: 'overview', top: 100, bottom: 140 },
  { id: 'data', top: 146, bottom: 186 },
  { id: 'tasks', top: 192, bottom: 232 },
];

it('shows a line between adjacent groups and keeps that position when a neighbor is the dragged group', () => {
  expect(groupInsertionAtY(rows, 143, 'tasks')).toEqual({ targetId: 'data', side: 'before' });
  expect(groupInsertionAtY(rows, 143, 'data')).toEqual({ targetId: 'overview', side: 'after' });
  expect(groupInsertionAtY(rows, 189, 'overview')).toEqual({ targetId: 'tasks', side: 'before' });
});

it('uses the group edge while hovering a row and ignores distant gaps', () => {
  expect(groupInsertionAtY(rows, 150, 'overview')).toEqual({ targetId: 'data', side: 'before' });
  expect(groupInsertionAtY(rows, 182, 'overview')).toEqual({ targetId: 'data', side: 'after' });
  expect(groupInsertionAtY(rows, 150, 'data')).toBeNull();
  expect(groupInsertionAtY([{ id: 'a', top: 0, bottom: 40 }, { id: 'b', top: 100, bottom: 140 }], 70, 'c')).toBeNull();
});

it('targets a top-level widget edge when a whole group is dragged beside it', () => {
  expect(groupInsertionAtY([
    { id: 'overview', top: 100, bottom: 140 },
    { id: 'audit', top: 146, bottom: 186 },
    { id: 'activity', top: 192, bottom: 232 },
  ], 189, 'overview')).toEqual({ targetId: 'activity', side: 'before' });
});
