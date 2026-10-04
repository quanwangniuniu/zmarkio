export interface DashboardWidgetPosition {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardWidget extends DashboardWidgetPosition {
  kind: 'widget';
  title?: string;
  settings?: { accent?: 'slate' | 'cyan' | 'lime' };
}

export interface DashboardGroup extends DashboardWidgetPosition {
  kind: 'group';
  title: string;
  children: DashboardWidget[];
}

export type DashboardItem = DashboardWidget | DashboardGroup;

export interface DashboardLayoutDocument {
  version: 2 | 3;
  items: DashboardItem[];
}

export interface DashboardLayoutResponse {
  version?: 2 | 3;
  items?: DashboardItem[];
  /** Older API responses and saved layouts use a flat widget list. */
  widgets?: DashboardWidgetPosition[];
  updated_at?: string;
}
