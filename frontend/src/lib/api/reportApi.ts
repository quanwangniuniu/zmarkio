import api from "../api";
import type {
  CustomKPI,
  CustomKPICreateRequest,
  CustomKPIUpdateRequest,
  KPIMetric,
  KPIPreviewRequest,
  KPIPreviewResponse,
  ReportTask,
  ReportTaskCreateRequest,
  ReportTaskUpdateRequest,
  ReportTaskKeyAction,
  ReportKeyActionCreateRequest,
  ReportKeyActionUpdateRequest,
} from "@/types/report";

const BASE = "/api/report/reports";
const KPI_BASE = "/api/report/kpis";

export const ReportAPI = {
  listReports: (params?: { task?: number }) =>
    api.get<ReportTask[]>(`${BASE}/`, { params }),

  createReport: (data: ReportTaskCreateRequest) =>
    api.post<ReportTask>(`${BASE}/`, data),

  getReport: (id: number) => api.get<ReportTask>(`${BASE}/${id}/`),

  updateReport: (id: string | number, data: ReportTaskUpdateRequest) =>
    api.patch<ReportTask>(`${BASE}/${id}/`, data),

  listKeyActions: (reportId: number) =>
    api.get<ReportTaskKeyAction[]>(`${BASE}/${reportId}/key-actions/`),

  createKeyAction: (reportId: number, data: ReportKeyActionCreateRequest) =>
    api.post<ReportTaskKeyAction>(`${BASE}/${reportId}/key-actions/`, data),

  getKeyAction: (reportId: number, actionId: number) =>
    api.get<ReportTaskKeyAction>(
      `${BASE}/${reportId}/key-actions/${actionId}/`
    ),

  updateKeyAction: (
    reportId: number,
    actionId: number,
    data: ReportKeyActionUpdateRequest
  ) =>
    api.patch<ReportTaskKeyAction>(
      `${BASE}/${reportId}/key-actions/${actionId}/`,
      data
    ),

  deleteKeyAction: (reportId: number, actionId: number) =>
    api.delete<void>(`${BASE}/${reportId}/key-actions/${actionId}/`),

  // --- Custom KPIs -------------------------------------------------------

  /** Metric names a formula may reference; drives the editor's autocomplete. */
  listKPIMetrics: () =>
    api.get<{ metrics: KPIMetric[] }>(`/api/report/kpi-metrics/`),

  /** Unpaginated. Values are computed server-side over the given window. */
  listKPIs: (params: {
    project: string;
    start_date?: string;
    end_date?: string;
  }) => api.get<CustomKPI[]>(`${KPI_BASE}/`, { params }),

  createKPI: (data: CustomKPICreateRequest) =>
    api.post<CustomKPI>(`${KPI_BASE}/`, data),

  getKPI: (id: number) => api.get<CustomKPI>(`${KPI_BASE}/${id}/`),

  updateKPI: (id: number, data: CustomKPIUpdateRequest) =>
    api.patch<CustomKPI>(`${KPI_BASE}/${id}/`, data),

  deleteKPI: (id: number) => api.delete<void>(`${KPI_BASE}/${id}/`),

  /** Evaluates an unsaved formula. Formula errors arrive as 200 + `error`. */
  previewKPI: (data: KPIPreviewRequest) =>
    api.post<KPIPreviewResponse>(`${KPI_BASE}/preview/`, data),
};

export default ReportAPI;
