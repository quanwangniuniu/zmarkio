import api from "../api";
import { DashboardSummary } from "@/types/dashboard";
import type { DashboardLayoutResponse, DashboardWidgetPosition } from '@/types/dashboardLayout';

export const DashboardAPI = {
  // Get dashboard summary with optional project filter
  getSummary: (params?: { project_id?: number | string }) =>
    api.get<DashboardSummary>("/api/dashboard/summary/", { params }),
  getLayout: (projectId: number | string) =>
    api.get<DashboardLayoutResponse>('/api/dashboard/layout/', { params: { project_id: projectId } }),
  saveLayout: (projectId: number | string, widgets: DashboardWidgetPosition[]) =>
    api.put<DashboardLayoutResponse>('/api/dashboard/layout/', { widgets }, { params: { project_id: projectId } }),
};
