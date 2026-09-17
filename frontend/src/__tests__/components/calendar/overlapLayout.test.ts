import {
  layoutOverlappingEvents,
  overlapColumnStyle,
} from '@/components/calendar/overlapLayout';

const hour = (h: number, m = 0) => Date.UTC(2026, 8, 2, h, m);

describe('layoutOverlappingEvents', () => {
  it('leaves a single event full width', () => {
    const layout = layoutOverlappingEvents([
      { id: 'a', startMs: hour(9), endMs: hour(10) },
    ]);
    expect(layout.get('a')).toEqual({ colIndex: 0, colCount: 1 });
  });

  it('places two overlapping events side by side', () => {
    const layout = layoutOverlappingEvents([
      { id: 'a', startMs: hour(9), endMs: hour(10) },
      { id: 'b', startMs: hour(9, 30), endMs: hour(10, 30) },
    ]);
    expect(layout.get('a')?.colCount).toBe(2);
    expect(layout.get('b')?.colCount).toBe(2);
    expect(layout.get('a')?.colIndex).not.toBe(layout.get('b')?.colIndex);
  });

  it('does not treat back-to-back events as overlapping', () => {
    const layout = layoutOverlappingEvents([
      { id: 'a', startMs: hour(9), endMs: hour(10) },
      { id: 'b', startMs: hour(10), endMs: hour(11) },
    ]);
    expect(layout.get('a')).toEqual({ colIndex: 0, colCount: 1 });
    expect(layout.get('b')).toEqual({ colIndex: 0, colCount: 1 });
  });

  it('reuses a column once an earlier event has ended', () => {
    const layout = layoutOverlappingEvents([
      { id: 'a', startMs: hour(9), endMs: hour(10) },
      { id: 'b', startMs: hour(9, 30), endMs: hour(10, 30) },
      { id: 'c', startMs: hour(10), endMs: hour(11) },
    ]);
    expect(layout.get('a')?.colIndex).toBe(0);
    expect(layout.get('b')?.colIndex).toBe(1);
    expect(layout.get('c')?.colIndex).toBe(0);
    expect(layout.get('c')?.colCount).toBe(2);
  });
});

describe('overlapColumnStyle', () => {
  it('splits the column width so cards do not cover each other', () => {
    const left = overlapColumnStyle({ colIndex: 0, colCount: 2 });
    const right = overlapColumnStyle({ colIndex: 1, colCount: 2 });
    expect(left.left).toContain('0%');
    expect(right.left).toContain('50%');
    expect(left.width).toContain('50%');
    expect(right.width).toContain('50%');
  });
});
