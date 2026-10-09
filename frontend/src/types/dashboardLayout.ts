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
  configuration: DashboardLayoutConfiguration;
  updated_at?: string;
}

/** Server-owned presets and limits; the browser only supplies renderers and gestures. */
export interface DashboardLayoutConfiguration {
  columns: number;
  row_height: number;
  gap: number;
  resize_step: number;
  max_y: number;
  min_width: number;
  min_height: number;
  max_height: number;
  max_widgets: number;
  position_epsilon: number;
  widgets: Array<DashboardWidgetPosition & { label: string; min_resize_height: number; add_x?: number }>;
}
