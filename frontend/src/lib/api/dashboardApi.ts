import api from "../api";
import { DashboardSummary } from "@/types/dashboard";
import type { DashboardLayoutResponse, DashboardWidgetPosition } from '@/types/dashboardLayout';

export interface RollupProjectResult {
  project_id: number;
  project_name: string;
  [key: string]: number | string;
}

export interface RollupResponse {
  results: RollupProjectResult[];
  errors: Record<string, string>;
}

export const DashboardAPI = {
  // Get dashboard summary with optional project filter
  getSummary: (params?: { project_id?: number | string }) =>
    api.get<DashboardSummary>("/api/dashboard/summary/", { params }),
  getLayout: (projectId: number | string) =>
    api.get<DashboardLayoutResponse>('/api/dashboard/layout/', { params: { project_id: projectId } }),
  saveLayout: (projectId: number | string, widgets: DashboardWidgetPosition[]) =>
    api.put<DashboardLayoutResponse>('/api/dashboard/layout/', { widgets }, { params: { project_id: projectId } }),

  // Fetch rollup metrics for all projects the current user can access.
  // Project scope and fields are determined server-side.
  getRollup: async (): Promise<RollupResponse> => {
    const res = await api.get<RollupResponse>("/api/dashboard/rollup/");
    const raw = (res as any).data;
    if (Array.isArray(raw)) {
      return { results: raw, errors: {} };
    }
    return {
      results: Array.isArray(raw?.results) ? raw.results : [],
      errors: raw?.errors ?? {},
    };
  },
};
