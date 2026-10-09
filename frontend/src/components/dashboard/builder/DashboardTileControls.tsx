'use client';

import { createContext, useContext, type ReactNode } from 'react';

interface TileControls {
  widgetId: string;
  dragHandle: ReactNode;
  removeButton: ReactNode;
}

const Context = createContext<TileControls | null>(null);

export function DashboardTileControlsProvider({ value, children }: { value: TileControls | null; children: ReactNode }) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** Only the widget that owns a card receives its drag and remove controls. */
export function useDashboardTileControls(widgetId: string) {
  const controls = useContext(Context);
  return controls?.widgetId === widgetId ? controls : null;
}

/** Put the drag grip over an existing card icon so its title never shifts. */
export function DashboardTileIcon({ icon, dragHandle }: { icon: ReactNode; dragHandle?: ReactNode }) {
  if (!dragHandle) return <>{icon}</>;
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center">
      <span className="inline-flex group-hover:opacity-0 group-focus-within:opacity-0">{icon}</span>
      <span className="absolute inset-0 z-10 flex items-center justify-center">{dragHandle}</span>
    </span>
  );
}
