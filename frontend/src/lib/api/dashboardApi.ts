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

const BATCH_SIZE = 20;

export const DashboardAPI = {
  // Get dashboard summary with optional project filter
  getSummary: (params?: { project_id?: number | string }) =>
    api.get<DashboardSummary>("/api/dashboard/summary/", { params }),

  // Fetch rollup data for given project IDs and fields.
  // Batches project_ids in groups of BATCH_SIZE and fires requests concurrently.
  getRollup: async (
    projectIds: number[],
    fields: string[]
  ): Promise<RollupResponse> => {
    const batches: number[][] = [];
    for (let i = 0; i < projectIds.length; i += BATCH_SIZE) {
      batches.push(projectIds.slice(i, i + BATCH_SIZE));
    }

    const fieldsParam = fields.join(",");
    const responses = await Promise.all(
      batches.map((batch) =>
        api.get<RollupResponse>("/api/dashboard/rollup/", {
          params: { project_ids: batch.join(","), fields: fieldsParam },
        })
      )
    );

    const merged: RollupResponse = { results: [], errors: {} };
    for (const res of responses) {
      const raw = (res as any).data;
      if (Array.isArray(raw)) {
        // Backend returned a flat array of project results directly
        merged.results.push(...raw);
      } else {
        merged.results.push(...(Array.isArray(raw?.results) ? raw.results : []));
        Object.assign(merged.errors, raw?.errors ?? {});
      }
    }
    return merged;
  },
};
