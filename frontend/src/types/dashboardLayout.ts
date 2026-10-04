export interface DashboardWidgetPosition {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardLayoutResponse {
  widgets: DashboardWidgetPosition[];
  updated_at?: string;
}
