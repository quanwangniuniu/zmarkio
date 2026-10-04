'use client';

import { createContext, useContext, type ReactNode } from 'react';

interface TileControls {
  widgetId: string;
  title: string;
  dragHandle: ReactNode;
  removeButton: ReactNode;
}

const Context = createContext<TileControls | null>(null);

export function DashboardTileControlsProvider({ value, children }: { value: TileControls; children: ReactNode }) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** Only the card that owns this tile receives its controls (legacy Workspace contains several cards). */
export function useDashboardTileControls(widgetId: string) {
  const controls = useContext(Context);
  return controls?.widgetId === widgetId ? controls : null;
}
