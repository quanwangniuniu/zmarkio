import api from "../api";
import { DashboardSummary } from "@/types/dashboard";
import type { DashboardLayoutDocument, DashboardLayoutResponse } from '@/types/dashboardLayout';

export const DashboardAPI = {
  // Get dashboard summary with optional project filter
  getSummary: (params?: { project_id?: number | string }) =>
    api.get<DashboardSummary>("/api/dashboard/summary/", { params }),
  getLayout: (projectId: number | string) =>
    api.get<DashboardLayoutResponse>('/api/dashboard/layout/', { params: { project_id: projectId } }),
  saveLayout: (projectId: number | string, document: DashboardLayoutDocument) =>
    api.put<DashboardLayoutResponse>('/api/dashboard/layout/', document, { params: { project_id: projectId } }),
};
