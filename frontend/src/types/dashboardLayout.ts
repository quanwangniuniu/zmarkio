export interface DashboardWidgetPosition {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardWidgetDefinition extends DashboardWidgetPosition {
  label: string;
  min_h: number;
}

export interface DashboardLayoutResponse {
  project_id: number;
  project_slug: string;
  widgets: DashboardWidgetPosition[];
  catalog: DashboardWidgetDefinition[];
  columns: number;
  max_rows: number;
  updated_at: string | null;
}
