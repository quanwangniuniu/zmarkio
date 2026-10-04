'use client';

import { createContext, useContext, type ReactNode } from 'react';

interface TileControls {
  widgetId: string;
  dragHandle: ReactNode;
  removeButton: ReactNode;
}

const Context = createContext<TileControls | null>(null);

export function DashboardTileControlsProvider({ value, children }: { value: TileControls; children: ReactNode }) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** Only the widget that owns a card receives its drag and remove controls. */
export function useDashboardTileControls(widgetId: string) {
  const controls = useContext(Context);
  return controls?.widgetId === widgetId ? controls : null;
}
