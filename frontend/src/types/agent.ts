// Type definitions for AI Agent feature

// ==================== Session Types ====================

export interface AgentSession {
  id: string;
  project_id?: number;
  created_by?: number;
  title?: string | null;
  status?: string;
  approval_required?: boolean;
  is_pinned?: boolean;
  last_read_at?: string | null;
  has_unread?: boolean;
  created_at: string;
  updated_at?: string;
  message_count?: number;
}

export interface AgentSessionDetail extends AgentSession {
  messages: AgentMessage[];
  follow_up_available?: boolean;
  follow_up_started?: boolean;
}

export interface UpdateSessionRequest {
  title?: string;
  approval_required?: boolean;
  status?: string;
  is_pinned?: boolean;
  /** ISO datetime string or null to clear (mark unread when assistant messages exist). */
  last_read_at?: string | null;
}

export interface CreateSessionRequest {
  project_id?: number;
  approval_required?: boolean;
}

/** Appends a message to a session's transcript without invoking the agent pipeline. */
export interface AppendMessageRequest {
  role: AgentMessageRole;
  content: string;
  message_type?: string;
  data?: Record<string, unknown>;
}

// ==================== Message Types ====================

export type AgentMessageRole = 'user' | 'assistant';

export interface AgentMessage {
  id: string;
  session_id: number;
  role: AgentMessageRole;
  content: string;
  message_type?: string;
  created_at: string;
  data?: AgentMessageData | null;
}

export interface AgentMessageData {
  anomalies?: AnomalyItem[];
  reviewed_anomalies?: AnomalyItem[];
  anomalies_confirmed?: boolean;
  decision_id?: number;
  decision_ids?: number[];
  created_decisions?: Array<{
    ref: string;
    decision_id: number;
    title: string;
    layer: number;
  }>;
  recommended_decision_tree?: {
    nodes: Array<Record<string, unknown>>;
  };
  task_ids?: number[];
  created_tasks?: Array<{ index: number; task_id: number; summary: string }>;
  board_id?: string;
  board_slug?: string;
  event_type?: string;
  status?: string;
  recommended_tasks?: RecommendedTask[];
  approval_id?: string;
  kind?: string;
  draft?: Record<string, unknown>;
  file_id?: string;
  workflow_run_id?: string;
  session_id?: string;
  filename?: string;
  original_filename?: string;
  row_count?: number;
  column_count?: number;
  spreadsheet_id?: number;
  sheet_id?: number;
  project_id?: number;
  url?: string;
  generation_outputs?: GenerationOutputKey[];
  calendar_events?: SuggestedCalendarEvent[];
  step_order?: number;
  step_name?: string;
  total_steps?: number;
}

export interface SuggestedCalendarEvent {
  title: string;
  start_datetime: string;
  end_datetime: string;
  location?: string;
  description?: string;
}

// ==================== SSE Stream Types ====================

export type GenerationOutputKey =
  | 'recommended_tasks'
  | 'recommended_decision_tree'
  | 'miro_board';

export type SSEEventType =
  | 'text'
  | 'text_delta'
  | 'analysis'
  | 'spreadsheet_summary'
  | 'spreadsheet_anomalies'
  | 'confirmation_request'
  | 'approval_request'
  | 'follow_up_prompt'
  | 'decision_draft'
  | 'task_created'
  | 'miro_status'
  | 'file_uploaded'
  | 'calendar_invite'
  | 'calendar_events'
  | 'calendar_updated'
  | 'step_progress'
  | 'column_mapping'
  | 'anomalies_confirmed'
  | 'done'
  | 'error';

export interface SSEEvent {
  type: SSEEventType;
  content?: string;
  data?: AgentMessageData;
}

// ==================== Chat Request ====================

export type AgentAction =
  | 'analyze'
  | 'analyze_spreadsheet_insights'
  | 'create_decisions'
  | 'create_tasks'
  | 'generate_miro'
  | 'start_follow_up'
  | 'cancel_follow_up'
  | 'confirm_columns'
  | 'confirm_anomalies'
  | 'resume_workflow'
  | 'resolve_external_approval';

export interface CalendarContextPayload {
  type: 'calendar' | 'event';
  eventId?: string;
  eventTitle?: string;
  calendarId?: string;
  startDatetime?: string;
  endDatetime?: string;
  description?: string;
  calendarIds?: string[];
  currentView?: string;
  currentDate?: string;
  userTimezone?: string;
}

export interface AgentChatRequest {
  message: string;
  spreadsheet_id?: number;
  sheet_id?: number;
  csv_filename?: string;
  action?: AgentAction;
  calendar_context?: CalendarContextPayload;
  /** Draft → Agent context (read-only): { draftId, title, ... }. */
  draft_context?: Record<string, unknown>;
  workflow_id?: string;
  column_mapping?: Record<string, string>;
  approval_id?: string;
  approval_decision?: 'approve' | 'reject';
  approval_draft?: Record<string, unknown>;
  user_context?: string;
  reviewed_anomalies?: ReviewedAnomaly[];
}

// ==================== Analysis Types ====================

export type AnomalySeverity = 'critical' | 'warning' | 'info';

export interface AnomalyLocation {
  row: number;
  col: number;
  a1?: string;
  sheet_id?: number;
}

export interface AnomalyItem {
  id: string;
  metric: string;
  movement: string;
  severity: AnomalySeverity;
  current_value: number | string;
  previous_value: number | string;
  change_percent: number;
  campaign?: string | null;
  ad_set?: string | null;
  description: string;
  /** Spreadsheet insights: short label for the anomaly. */
  title?: string;
  /** Spreadsheet insights: cell references for highlighting. */
  locations?: AnomalyLocation[];
  /** Present on reviewed anomalies after confirmation. */
  included?: boolean;
}

/** Per-anomaly review decision sent to the backend on confirm_anomalies. */
export interface ReviewedAnomaly {
  id: string;
  included: boolean;
  severity: AnomalySeverity;
  description: string;
}

// ==================== Spreadsheet Selector ====================

export interface AgentSpreadsheet {
  id: number;
  name: string;
  project_id: number;
  created_at: string;
  updated_at: string;
}

// ==================== UI State Types ====================

// ==================== Column Detection Types ====================

export interface ColumnDetectionData {
  schema_key: string | null
  schema_name: string
  source: 'rule' | 'llm' | 'none'
  confidence: number
  mappings: Record<string, string>
  categories: Record<string, string>
  unrecognized: string[]
  column_confidences: Record<string, number>
}

// ==================== Imported CSV File ====================

export interface ImportedCSVFile {
  id: string;
  filename: string;
  original_filename: string;
  row_count: number;
  column_count: number;
  file_size: number;
  created_at: string;
}

// ==================== Analysis Result Types ====================

export interface RecommendedDecisionTreeNode {
  ref: string;
  layer: number;
  title: string;
  parent_refs: string[];
  context_summary?: string;
  reasoning?: string;
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH';
  confidence?: number;
  topic?: string;
}

export interface AnalysisResult {
  anomalies: AnomalyItem[];
  reviewed_anomalies?: AnomalyItem[];
  anomalies_confirmed?: boolean;
  recommended_tasks?: RecommendedTask[];
  recommended_decision_tree?: {
    nodes: RecommendedDecisionTreeNode[];
  };
}

export interface RecommendedTask {
  type: string;
  summary: string;
  description?: string;
  priority: 'HIGH' | 'MEDIUM' | 'LOW';
}

// ==================== Workflow Step State ====================

export interface WorkflowStepState {
  analysisComplete: boolean;
  anomaliesConfirmed: boolean;
  tasksCreated: boolean;
  decisionsCreated: boolean;
}

// ==================== Workflow Types ====================

// Trigger Types
export type TriggerType = 'polling' | 'instant' | 'scheduled' | 'manual';

export type TriggerStatus = 'triggered' | 'skipped' | 'failed';

export type PollingExternalService = 'zoom' | 'google_sheets' | 'linear' | 'notion';

export interface WorkflowTriggerConfig {
  trigger_type: TriggerType;
  polling?: {
    interval_minutes: 5 | 15 | 30 | 60;
    /** External services to monitor. Requires the service to be connected in Integrations. */
    external_services: PollingExternalService[];
  };
  instant?: {
    event_types: string[];
    webhook_enabled: boolean;
    webhook_secret?: string;
    webhook_url?: string;
    filters?: Record<string, unknown>;
  };
  scheduled?: {
    cron_expression: string;
    timezone: string;
    enabled: boolean;
  };
  manual?: {
    require_confirmation: boolean;
  };
}

export interface WorkflowTriggerLog {
  id: string;
  workflow: string;
  trigger_type: TriggerType;
  status: TriggerStatus;
  trigger_context: Record<string, unknown>;
  workflow_run?: string;
  error_message?: string;
  execution_time_ms?: number;
  created_at: string;
}

export interface WorkflowTriggerState {
  id: string;
  workflow: string;
  last_successful_trigger?: string;
  last_polling_check?: string;
  next_scheduled_run?: string;
  last_trigger_type?: TriggerType;
  trigger_count_last_hour?: number;
  trigger_count_reset_at?: string;
  last_checked_data_hash?: string;
  created_at: string;
  updated_at: string;
}

export type WorkflowStepType =
  | 'analyze_data'
  /** @deprecated Executor returns error if stepped; legacy DB records may still reference this value. */
  | 'call_dify'
  | 'call_llm'
  /** @deprecated No longer created from the UI; legacy workflows may still list this step. */
  | 'create_decision'
  | 'create_tasks'
  | 'custom_api'
  | 'await_confirmation'
  | 'detect_columns'
  | 'normalize_data'
  | 'generate_criteria'
  | 'generate_miro_snapshot'
  | 'create_miro_board'
  // Flow control — rendered on canvas; no runtime execution yet
  | 'if_else'
  | 'merge'
  | 'loop';

export interface AgentWorkflowStep {
  id: string;
  name: string;
  step_type: WorkflowStepType;
  order: number;
  config: Record<string, unknown>;
  description?: string;
  created_at?: string;
}

export interface AgentWorkflowDefinition {
  id: string;
  slug: string;
  name: string;
  description: string;
  is_default: boolean;
  is_system: boolean;
  status: 'active' | 'draft' | 'archived';
  step_count?: number;
  /** Ordered list of step_type strings for each active step (list endpoint only). */
  step_types?: WorkflowStepType[];
  steps?: AgentWorkflowStep[];
  trigger_config?: WorkflowTriggerConfig;
  trigger_state?: WorkflowTriggerState;
  created_at: string;
  updated_at?: string;
}

export type StepExecutionStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'awaiting';

export interface AgentStepExecution {
  id: string;
  step_order: number;
  step_name: string;
  status: StepExecutionStatus;
  input_data?: Record<string, unknown>;
  output_data?: Record<string, unknown>;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
}

export interface AgentWorkflowRun {
  id: string;
  session: string;
  workflow_definition?: string;
  status: string;
  current_step_order?: number;
  analysis_result?: Record<string, unknown>;
  created_tasks?: unknown[];
  error_message?: string;
  step_executions?: AgentStepExecution[];
  created_at: string;
  updated_at: string;
}

// ==================== Template Types ====================

export type TemplateCategory = 'review' | 'optimization' | 'analysis' | 'reporting' | 'other';

export interface TemplateProjectInfo {
  id: number;
  name: string;
}

export interface AgentWorkflowTemplate {
  id: string;
  slug: string;
  name: string;
  description?: string;
  category: TemplateCategory;
  /** Template's own steps configuration (fully independent from any workflow). */
  steps_config?: AgentWorkflowStep[];
  workflow_step_count?: number;
  /** Ordered list of step_type strings for each step in the template. */
  workflow_step_types?: WorkflowStepType[];
  created_by: string;
  /** Set when shared at org level; all org members can see this template. */
  organization?: string;
  organization_name?: string;
  /** List of projects this template is shared with (M2M). */
  project_list?: TemplateProjectInfo[];
  applied_project_count?: number;
  /** Example phrases / scenarios describing when to use this template. */
  use_cases?: string[];
  /** True if this template is shared to the current active project (annotated field). */
  is_shared_to_current_project?: boolean;
  created_at: string;
  updated_at?: string;
}

export interface CreateTemplateRequest {
  source_workflow_id: string;
  name: string;
  description?: string;
  category: TemplateCategory;
  /** UUID of organization to share with (optional). */
  organization_id?: string | null;
  /** List of project IDs (integers) to share with. Empty array clears all. */
  project_ids?: number[];
  use_cases?: string[];
}

export interface UpdateTemplateRequest {
  name?: string;
  description?: string;
  category?: TemplateCategory;
  /** Pass null to remove org sharing, UUID to set. */
  organization_id?: string | null;
  /** Pass new list to replace. Empty array clears all. */
  project_ids?: number[];
  use_cases?: string[];
}
