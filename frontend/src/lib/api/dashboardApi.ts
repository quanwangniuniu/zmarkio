import api from "../api";
import { DashboardSummary } from "@/types/dashboard";

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
