export interface DashboardWidgetPosition {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardLayoutResponse {
  project_id: number;
  project_slug: string;
  widgets: DashboardWidgetPosition[];
  updated_at?: string;
}
