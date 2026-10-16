import { initialLayoutState, layoutReducer } from '@/components/dashboard/builder/layoutReducer';

const widgets = [{ id: 'audit', x: 0, y: 4, w: 6, h: 6 }];

describe('dashboard layout reducer', () => {
  it('uses loaded positions and marks a change as pending until its save succeeds', () => {
    const loaded = layoutReducer(initialLayoutState, {
      type: 'loaded', widgets, maxRows: 200, marginRows: 8, revision: 1,
    });
    expect(loaded).toMatchObject({ widgets, rowLimit: 18, saveState: 'idle' });

    const moved = [{ ...widgets[0], y: 8 }];
    const pending = layoutReducer(loaded, {
      type: 'changed', widgets: moved, maxRows: 200, marginRows: 8, revision: 2,
    });
    expect(pending).toMatchObject({ widgets: moved, rowLimit: 22, saveState: 'saving' });
    expect(layoutReducer(pending, { type: 'saved', revision: 2 }).saveState).toBe('saved');
  });

  it('ignores an older save result after a newer move, or after a project reset', () => {
    const first = layoutReducer(initialLayoutState, {
      type: 'changed', widgets, maxRows: 200, marginRows: 8, revision: 1,
    });
    const second = layoutReducer(first, {
      type: 'changed', widgets: [{ ...widgets[0], y: 10 }], maxRows: 200, marginRows: 8, revision: 2,
    });
    expect(layoutReducer(second, { type: 'saved', revision: 1 })).toBe(second);
    expect(layoutReducer(second, { type: 'failed', revision: 1 })).toBe(second);
    expect(layoutReducer(second, { type: 'failed', revision: 2 }).saveState).toBe('error');

    const reset = layoutReducer(second, { type: 'reset', revision: 3 });
    expect(layoutReducer(reset, { type: 'saved', revision: 2 })).toBe(reset);
    expect(reset.widgets).toEqual([]);
  });

  it('clears Saved when a new drag starts before any layout change', () => {
    const saved = { ...initialLayoutState, saveState: 'saved' as const, revision: 4 };
    expect(layoutReducer(saved, { type: 'invalidate', revision: 5 })).toMatchObject({ saveState: 'idle', revision: 5 });
  });
});
