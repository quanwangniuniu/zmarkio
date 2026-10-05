export interface DashboardWidgetPosition {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** Editable text for standalone section titles. */
  title?: string;
}

export interface DashboardLayoutResponse {
  project_id: number;
  project_slug: string;
  widgets: DashboardWidgetPosition[];
  updated_at?: string;
}
