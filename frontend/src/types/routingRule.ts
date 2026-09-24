// Routing rules & sandbox (CSM-S03-05). Mirrors backend csm/services/routing_engine.py.

export type RoutingMatchMode = 'all' | 'any';

export type RoutingConditionField =
  | 'latest_message'
  | 'any_message'
  | 'subject'
  | 'support_channel'
  | 'channel_type'
  | 'channel_status'
  | 'customer_organisation'
  | 'message_count';

export type RoutingValueKind =
  | 'keywords'
  | 'text'
  | 'none'
  | 'channel_ids'
  | 'channel_types'
  | 'channel_status'
  | 'organisation_ids'
  | 'integer';

export type RoutingConditionValue = string | number | string[] | number[] | null;

export interface RoutingCondition {
  field: RoutingConditionField;
  operator: string;
  value: RoutingConditionValue;
}

export interface RoutingRule {
  id: number;
  experience_group: number;
  name: string;
  position: number;
  is_enabled: boolean;
  match_mode: RoutingMatchMode;
  conditions: RoutingCondition[];
  action_type: 'route_to_queue';
  target_queue: number | null;
  target_queue_name: string | null;
  target_queue_is_active: boolean | null;
  add_tags: string[];
  created_at: string;
  updated_at: string;
}

export interface RoutingRulePayload {
  experience_group: number;
  name: string;
  is_enabled: boolean;
  match_mode: RoutingMatchMode;
  conditions: RoutingCondition[];
  target_queue: number | null;
  add_tags: string[];
}

export interface RoutingVocabularyOperator {
  operator: string;
  label: string;
  value_kind: RoutingValueKind;
}

export interface RoutingVocabularyField {
  field: RoutingConditionField;
  label: string;
  operators: RoutingVocabularyOperator[];
}

export interface RoutingVocabulary {
  fields: RoutingVocabularyField[];
  channel_types: string[];
  channel_statuses: string[];
  limits: { conditions: number; keywords: number; tags: number };
}

// ── Sandbox ──────────────────────────────────────────────────────────────

export interface RoutingSandboxRequest {
  experience_group: number;
  messages: string[];
  subject?: string;
  support_channel?: number | null;
  customer_organisation?: number | null;
  simulated_at?: string | null;
  evaluate_each_prefix?: boolean;
}

export type RuleStepStatus =
  | 'matched'
  | 'not_matched'
  | 'disabled'
  | 'invalid_action'
  | 'not_reached';

export interface ConditionResult {
  index: number;
  field: string;
  operator: string;
  expected: RoutingConditionValue;
  actual: string | number | null;
  passed: boolean;
  detail: string;
}

export interface RuleStepAction {
  type: 'route_to_queue';
  queue_id: number | null;
  queue_name: string | null;
  add_tags: string[];
}

export interface RuleStep {
  rule_id: number;
  rule_name: string;
  position: number;
  status: RuleStepStatus;
  match_mode: RoutingMatchMode;
  conditions: ConditionResult[];
  action: RuleStepAction | null;
  note: string;
}

export type FallbackSource = 'channel_default_queue' | 'first_organisation_queue' | 'none';

export interface FallbackStep {
  source: FallbackSource;
  queue_id: number | null;
  queue_name: string | null;
  reason: string;
}

export interface RoutingOutcome {
  decided_by: 'rule' | 'fallback' | 'none';
  rule_id: number | null;
  rule_name: string | null;
  queue_id: number | null;
  queue_name: string | null;
  tags: string[];
}

export interface RoutingTrace {
  evaluated_at: string;
  message_count: number;
  steps: RuleStep[];
  fallback: FallbackStep | null;
  outcome: RoutingOutcome;
  warnings: string[];
}

export interface RoutingSandboxResult {
  experience_group: { id: number; name: string; status: string };
  support_channel: {
    id: number;
    display_name: string;
    channel_type: string;
    is_online: boolean;
    offline_reason: string | null;
  } | null;
  rule_count: number;
  traces: RoutingTrace[];
  warnings: string[];
}
