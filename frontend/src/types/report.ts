// Report Task types matching backend /api/report/reports/

export type ReportAudienceType =
  | "client"
  | "manager"
  | "internal_team"
  | "self"
  | "other";

export interface PromptTemplateDefinition {
  version: string;
  tone: string;
  section_prompts: {
    context: string;
    key_actions: string;
    outcome_summary: string;
    narrative_explanation: string;
  };
  suggested_key_actions?: string[];
}

export interface ReportTaskKeyAction {
  id: number;
  report_task: number;
  order_index: number;
  action_text: string;
  created_at: string;
  updated_at: string;
}

export interface ReportContext {
  reporting_period?: {
    type: "last_week" | "this_month" | "custom" | null;
    text: string;
    start_date?: string;
    end_date?: string;
  } | null;
  situation: string;
  what_changed: string;
}

export interface ReportTask {
  id: number;
  task: number;
  audience_type: ReportAudienceType;
  audience_details: string;
  audience_prompt_version?: string;
  prompt_template?: PromptTemplateDefinition;
  context: ReportContext;
  outcome_summary: string;
  narrative_explanation: string;
  key_actions: ReportTaskKeyAction[];
  is_complete: boolean;
  created_at: string;
  updated_at: string;
}

export interface ReportTaskCreateRequest {
  task: number;
  audience_type: ReportAudienceType;
  audience_details?: string;
  context: ReportContext;
  outcome_summary: string;
  narrative_explanation?: string;
  /** When creating a report, key actions can be sent as strings; backend creates ReportTaskKeyAction records. */
  key_actions?: string[];
}

export interface ReportTaskUpdateRequest {
  audience_type?: ReportAudienceType;
  audience_details?: string;
  context?: ReportContext;
  outcome_summary?: string;
  narrative_explanation?: string;
}

export interface ReportKeyActionCreateRequest {
  order_index: number;
  action_text: string;
}

export interface ReportKeyActionUpdateRequest {
  order_index?: number;
  action_text?: string;
}

// Custom KPI types matching backend /api/report/kpis/

export type KPIDisplayFormat = "number" | "currency" | "percent";

/** A warehouse metric a formula may reference by name. */
export interface KPIMetric {
  key: string;
  label: string;
  unit: "currency" | "count";
}

export interface KPIFormulaError {
  /** Spreadsheet-style code, e.g. "#DIV/0!" or "#NAME?". */
  code: string;
  message: string;
}

export interface CustomKPI {
  id: number;
  /** Project slug. */
  project: string;
  project_id: number;
  name: string;
  description: string;
  formula: string;
  display_format: KPIDisplayFormat;
  /** Decimal serialized as a string; null when `error` is set, or when the
   * request did not ask for values. */
  value: string | null;
  error: KPIFormulaError | null;
  created_at: string;
  updated_at: string;
}

export interface CustomKPICreateRequest {
  /** Project slug or numeric id. */
  project: string;
  name: string;
  formula: string;
  description?: string;
  display_format?: KPIDisplayFormat;
}

export interface CustomKPIUpdateRequest {
  name?: string;
  formula?: string;
  description?: string;
  display_format?: KPIDisplayFormat;
}

export interface KPIPreviewRequest {
  project: string;
  formula: string;
  start_date?: string;
  end_date?: string;
}

/** A formula error is a successful response, not a failed request. */
export interface KPIPreviewResponse {
  value: string | null;
  error: KPIFormulaError | null;
}
