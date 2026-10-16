import type { DashboardWidgetPosition } from '@/types/dashboardLayout';

export type SaveState = 'idle' | 'saving' | 'saved' | 'error';

export interface LayoutState {
  widgets: DashboardWidgetPosition[];
  rowLimit: number;
  saveState: SaveState;
  revision: number;
}

export const initialLayoutState: LayoutState = {
  widgets: [], rowLimit: 0, saveState: 'idle', revision: 0,
};

export type LayoutAction =
  | { type: 'reset'; revision: number }
  | { type: 'loaded' | 'changed'; widgets: DashboardWidgetPosition[]; maxRows: number; marginRows: number; revision: number }
  | { type: 'invalidate' | 'saved' | 'failed'; revision: number };

export function layoutReducer(state: LayoutState, action: LayoutAction): LayoutState {
  if (action.type === 'reset') return { ...initialLayoutState, revision: action.revision };
  if (action.revision < state.revision) return state;

  if (action.type === 'loaded' || action.type === 'changed') {
    const bottom = Math.max(0, ...action.widgets.map((widget) => widget.y + widget.h));
    return {
      widgets: action.widgets,
      rowLimit: Math.min(action.maxRows, bottom + action.marginRows),
      saveState: action.type === 'changed' ? 'saving' : 'idle',
      revision: action.revision,
    };
  }
  if (action.type === 'invalidate') return { ...state, saveState: 'idle', revision: action.revision };
  if (action.revision !== state.revision) return state;
  return { ...state, saveState: action.type === 'saved' ? 'saved' : 'error' };
}
