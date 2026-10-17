import api from "../api";
import type { CampaignPacingForecast } from "@/types/campaign";

export interface Optimization {
  id: number;
  task: number;
  affected_entity_ids?: {
    campaign_ids?: string[];
    ad_set_ids?: string[];
  } | null;
  triggered_metrics?: Record<string, any> | null;
  baseline_metrics?: Record<string, any> | null;
  observed_metrics?: Record<string, any> | null;
  action_type: "pause" | "scale" | "duplicate" | "edit";
  planned_action?: string;
  execution_status: "detected" | "planned" | "executed" | "monitoring" | "completed" | "cancelled";
  executed_at?: string | null;
  monitored_at?: string | null;
  outcome_notes?: string;
  created_at: string;
  updated_at: string;
}

export interface OptimizationCreateRequest {
  task: number;
  affected_entity_ids?: {
    campaign_ids?: string[];
    ad_set_ids?: string[];
  };
  triggered_metrics?: Record<string, any>;
  baseline_metrics?: Record<string, any>;
  observed_metrics?: Record<string, any>;
  action_type?: "pause" | "scale" | "duplicate" | "edit";
  planned_action?: string;
  execution_status?: "detected" | "planned" | "executed" | "monitoring" | "completed" | "cancelled";
  executed_at?: string;
  monitored_at?: string;
  outcome_notes?: string;
}

export interface OptimizationUpdateRequest {
  affected_entity_ids?: {
    campaign_ids?: string[];
    ad_set_ids?: string[];
  } | null;
  triggered_metrics?: Record<string, any> | null;
  baseline_metrics?: Record<string, any> | null;
  observed_metrics?: Record<string, any> | null;
  action_type?: "pause" | "scale" | "duplicate" | "edit";
  planned_action?: string;
  execution_status?: "detected" | "planned" | "executed" | "monitoring" | "completed" | "cancelled";
  executed_at?: string | null;
  monitored_at?: string | null;
  outcome_notes?: string;
}

export const OptimizationAPI = {
  listOptimizations: (params?: {
    task_id?: number;
    execution_status?: string;
    action_type?: string;
  }) => {
    const response = api.get<Optimization[]>("/api/optimization/optimizations/", { params });
    return response;
  },

  createOptimization: (data: OptimizationCreateRequest) =>
    api.post<Optimization>("/api/optimization/optimizations/", data),

  getOptimization: (id: number) =>
    api.get<Optimization>(`/api/optimization/optimizations/${id}/`),

  updateOptimization: (id: string | number, data: OptimizationUpdateRequest) =>
    api.patch<Optimization>(`/api/optimization/optimizations/${id}/`, data),

  deleteOptimization: (id: number) =>
    api.delete(`/api/optimization/optimizations/${id}/`),

  // ==================== BUDGET PACING ====================

  /** Latest pacing forecast for a campaign, computed on first read if missing. */
  getCampaignPacing: (slug: string) =>
    api.get<CampaignPacingForecast>(`/api/optimization/campaigns/${slug}/pacing/`),

  /** Force a fresh forecast rather than waiting for the nightly recomputation. */
  recomputeCampaignPacing: (slug: string) =>
    api.post<CampaignPacingForecast>(
      `/api/optimization/campaigns/${slug}/pacing/recompute/`
    ),
};

