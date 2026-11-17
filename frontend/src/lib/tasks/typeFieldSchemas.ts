/**
 * Per-task-type field schemas for /tasks/new.
 *
 * Each Task.type that has a dedicated Django sub-model (budget_approval,
 * asset, retrospective, policy, ...) gets an entry here. The schema drives:
 *   - dynamic field rendering in the create form
 *   - commit-readiness checklist on the right aside
 *   - two-step submit (create Task -> create sub-model -> link)
 *
 * Field set is intentionally the user-visible minimum that maps to each
 * backend model: all required columns plus the most common optional ones.
 * Advanced configuration (nested JSON metrics, per-step rollout data, etc.)
 * is deferred to each type's dedicated detail page.
 *
 * Execution (`type === 'execution'`) has no sub-model — tasks of that type
 * only carry base Task fields, so there is no schema here.
 */

import { getTaskTypeLabel } from '@/lib/tasks/taskTypes';
import type { TaskFieldOptionConfig } from '@/types/orgCustomization';

export type FieldKind =
  | 'text'
  | 'textarea'
  | 'select'
  | 'date'
  | 'url'
  | 'number'
  | 'tags';

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldDef {
  /** Payload key on the sub-model create endpoint. */
  key: string;
  /**
   * Key used in the linked object API response when it differs from `key`.
   * Used by FSMActionBar to check if a required field is satisfied on an existing linked object.
   */
  linkedKey?: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  conditionalRequired?: {
    dependsOn: string;
    values: string[];
  };
  placeholder?: string;
  /** Rows for textarea kind. */
  rows?: number;
  /** Static options for select kind. */
  options?: FieldOption[];
  /** Key into option-loader registry for dynamic options. */
  optionsLoader?: string;
  helpText?: string;
  /** If provided, the field is only shown (and included in payloads) when this returns true. */
  showWhen?: (formData: Record<string, unknown>) => boolean;
}

export interface TypeSchema {
  /** Matches Task.type value exactly. */
  type: string;
  /** Human-readable label from the shared task type registry. */
  label: string;
  /** Django ContentType.model value used by /api/tasks/{id}/link/. */
  contentType: string;
  fields: FieldDef[];
  /** If provided, used instead of `fields` when rendering the inline edit form in TaskTypeBlock. */
  editFields?: FieldDef[];
}

const taskTypeLabel = (type: string): string => getTaskTypeLabel(type) ?? type;

// ---------------------------------------------------------------------------
// Individual schemas
// ---------------------------------------------------------------------------

const BUDGET_BASE_FIELDS: FieldDef[] = [
  {
    key: 'budget_pool_composite',
    linkedKey: 'budget_pool',
    label: 'Budget pool',
    kind: 'select',
    required: true,
    optionsLoader: 'budget.pools',
    placeholder: 'Select a budget pool…',
    helpText: 'Selects the pool, ad channel, and currency automatically.',
  },
  {
    key: 'amount',
    label: 'Amount',
    kind: 'number',
    required: true,
    placeholder: 'Requested amount',
  },
  {
    key: 'notes',
    label: 'Notes',
    kind: 'textarea',
    required: false,
    rows: 2,
    placeholder: 'Context for this budget request',
  },
];

const BUDGET: TypeSchema = {
  type: 'budget',
  label: taskTypeLabel('budget'),
  contentType: 'budgetrequest',
  fields: BUDGET_BASE_FIELDS,
  editFields: BUDGET_BASE_FIELDS,
};

const ASSET: TypeSchema = {
  type: 'asset',
  label: taskTypeLabel('asset'),
  contentType: 'asset',
  fields: [
    {
      key: 'tags',
      label: 'Tags',
      kind: 'tags',
      required: true,
      placeholder: 'Comma-separated tags',
    },
    {
      key: 'team',
      label: 'Team id',
      kind: 'number',
      required: false,
      helpText: 'Optional team ownership. Leave blank for personal.',
    },
  ],
};

const RETROSPECTIVE: TypeSchema = {
  type: 'retrospective',
  label: taskTypeLabel('retrospective'),
  contentType: 'retrospectivetask',
  fields: [
    {
      key: 'scheduled_at',
      label: 'Scheduled at',
      kind: 'date',
      required: false,
      helpText: 'Defaults to today if left blank.',
    },
    {
      key: 'decision',
      label: 'Decision summary',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Summarise the decision under review',
    },
    {
      key: 'primary_assumption',
      label: 'Primary assumption',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Key assumption the decision relied on',
    },
    {
      key: 'key_risk_ignore',
      label: 'Key risk ignored',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Risk knowingly not mitigated',
    },
    {
      key: 'confidence_level',
      label: 'Confidence level',
      kind: 'select',
      required: false,
      options: [
        { value: '1', label: '1 – Very low' },
        { value: '2', label: '2 – Low' },
        { value: '3', label: '3 – Moderate' },
        { value: '4', label: '4 – High' },
        { value: '5', label: '5 – Very high' },
      ],
      helpText: 'How confident are you in the original decision (1–5)?',
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'scheduled',    label: 'Scheduled' },
        { value: 'in_progress',  label: 'In Progress' },
        { value: 'completed',    label: 'Completed' },
        { value: 'reported',     label: 'Reported' },
        { value: 'cancelled',    label: 'Cancelled' },
      ],
      helpText: 'Defaults to Scheduled if left blank.',
    },
  ],
  editFields: [
    {
      key: 'scheduled_at',
      label: 'Scheduled at',
      kind: 'date',
      required: false,
    },
    {
      key: 'decision',
      label: 'Decision summary',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Summarise the decision under review',
    },
    {
      key: 'primary_assumption',
      label: 'Primary assumption',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Key assumption the decision relied on',
    },
    {
      key: 'key_risk_ignore',
      label: 'Key risk ignored',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Risk knowingly not mitigated',
    },
    {
      key: 'confidence_level',
      label: 'Confidence level',
      kind: 'select',
      required: false,
      options: [
        { value: '1', label: '1 – Very low' },
        { value: '2', label: '2 – Low' },
        { value: '3', label: '3 – Moderate' },
        { value: '4', label: '4 – High' },
        { value: '5', label: '5 – Very high' },
      ],
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'scheduled',    label: 'Scheduled' },
        { value: 'in_progress',  label: 'In Progress' },
        { value: 'completed',    label: 'Completed' },
        { value: 'reported',     label: 'Reported' },
        { value: 'cancelled',    label: 'Cancelled' },
      ],
    },
    {
      key: 'outcome_compared_to_expectation',
      label: 'Outcome vs expectation',
      kind: 'select',
      required: false,
      options: [
        { value: 'better',      label: 'Better than expected' },
        { value: 'as_expected', label: 'As expected' },
        { value: 'worse',       label: 'Worse than expected' },
      ],
    },
    {
      key: 'biggest_wrong_assumption',
      label: 'Biggest wrong assumption',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'What assumption proved incorrect',
    },
    {
      key: 'would_make_same_decision_again',
      label: 'Would make same decision again',
      kind: 'select',
      required: false,
      options: [
        { value: 'yes', label: 'Yes' },
        { value: 'no',  label: 'No' },
      ],
    },
    {
      key: 'report_url',
      label: 'Report URL',
      kind: 'url',
      required: false,
      placeholder: 'https://…',
    },
  ],
};

const REPORT: TypeSchema = {
  type: 'report',
  label: taskTypeLabel('report'),
  contentType: 'reporttask',
  fields: [
    {
      key: 'audience_type',
      label: 'Audience type',
      kind: 'select',
      required: true,
      options: [
        { value: 'client',        label: 'Client' },
        { value: 'manager',       label: 'Manager' },
        { value: 'internal_team', label: 'Internal Team' },
        { value: 'self',          label: 'Self' },
        { value: 'other',         label: 'Other' },
      ],
    },
    {
      key: 'audience_details',
      label: 'Audience details',
      kind: 'textarea',
      required: false,
      conditionalRequired: {
        dependsOn: 'audience_type',
        values: ['other'],
      },
      rows: 2,
      placeholder: 'Names, roles, or distribution list',
      helpText: 'Required when audience type is "Other"', 
    },
    {
      key: 'outcome_summary',
      label: 'Outcome summary',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Top-line outcome for the reporting period',
    },
    {
      key: 'narrative_explanation',
      label: 'Narrative explanation',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Why the outcome happened',
    },
  ],
};

const SCALING: TypeSchema = {
  type: 'scaling',
  label: taskTypeLabel('scaling'),
  contentType: 'scalingplan',
  fields: [
    {
      key: 'strategy',
      label: 'Strategy',
      kind: 'select',
      required: false,
      options: [
        { value: 'horizontal', label: 'Horizontal' },
        { value: 'vertical', label: 'Vertical' },
      ],
      helpText: 'Defaults to horizontal.',
    },
    {
      key: 'scaling_target',
      label: 'Scaling target',
      kind: 'textarea',
      required: true,
      rows: 2,
      placeholder: 'What you want to scale (campaign, ad set, budget)',
    },
    {
      key: 'risk_considerations',
      label: 'Risk considerations',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Main risks to watch',
    },
    {
      key: 'max_scaling_limit',
      label: 'Max scaling limit',
      kind: 'text',
      required: false,
      placeholder: 'e.g. 2x current daily spend',
    },
    {
      key: 'stop_conditions',
      label: 'Stop conditions',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Thresholds that would pause scaling',
    },
    {
      key: 'expected_outcomes',
      label: 'Expected outcomes',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Metrics you expect to see move',
    },
    {
      key: 'affected_entities',
      label: 'Affected entities',
      kind: 'tags',
      required: false,
      placeholder: 'Comma-separated campaign/ad set names',
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'planned',     label: 'Planned' },
        { value: 'in_progress', label: 'In Progress' },
        { value: 'completed',   label: 'Completed' },
        { value: 'cancelled',   label: 'Cancelled' },
      ],
      helpText: 'Defaults to Planned if left blank.',
    },
  ],
  editFields: [
    {
      key: 'strategy',
      label: 'Strategy',
      kind: 'select',
      required: false,
      options: [
        { value: 'horizontal', label: 'Horizontal' },
        { value: 'vertical',   label: 'Vertical' },
      ],
    },
    {
      key: 'scaling_target',
      label: 'Scaling target',
      kind: 'textarea',
      required: true,
      rows: 2,
      placeholder: 'What you want to scale',
    },
    {
      key: 'risk_considerations',
      label: 'Risk considerations',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Main risks to watch',
    },
    {
      key: 'max_scaling_limit',
      label: 'Max scaling limit',
      kind: 'text',
      required: false,
      placeholder: 'e.g. 2x current daily spend',
    },
    {
      key: 'stop_conditions',
      label: 'Stop conditions',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Thresholds that would pause scaling',
    },
    {
      key: 'expected_outcomes',
      label: 'Expected outcomes',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Metrics you expect to see move',
    },
    {
      key: 'affected_entities',
      label: 'Affected entities',
      kind: 'tags',
      required: false,
      placeholder: 'Comma-separated campaign/ad set names',
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'planned',     label: 'Planned' },
        { value: 'in_progress', label: 'In Progress' },
        { value: 'completed',   label: 'Completed' },
        { value: 'cancelled',   label: 'Cancelled' },
      ],
    },
    {
      key: 'review_summary',
      label: 'Review summary',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Overall result of the scaling run',
    },
    {
      key: 'review_lessons_learned',
      label: 'Lessons learned',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'What went well and what did not',
    },
    {
      key: 'review_future_actions',
      label: 'Future actions',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Next steps based on this run',
    },
  ],
};

const ALERT: TypeSchema = {
  type: 'alert',
  label: taskTypeLabel('alert'),
  contentType: 'alerttask',
  fields: [
    {
      key: 'alert_type',
      label: 'Alert type',
      kind: 'select',
      required: true,
      options: [
        { value: 'spend_spike',       label: 'Spend spike' },
        { value: 'policy_violation',  label: 'Policy violation' },
        { value: 'performance_drop',  label: 'Performance drop' },
        { value: 'delivery_issue',    label: 'Delivery issue' },
        { value: 'other',             label: 'Other' },
      ],
    },
    {
      key: 'severity',
      label: 'Severity',
      kind: 'select',
      required: true,
      options: [
        { value: 'low', label: 'Low' },
        { value: 'medium', label: 'Medium' },
        { value: 'high', label: 'High' },
        { value: 'critical', label: 'Critical' },
      ],
    },
    {
      key: 'investigation_notes',
      label: 'Investigation notes',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'What you know so far',
    },
    {
      key: 'resolution_steps',
      label: 'Resolution steps',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Planned mitigation steps',
    },
    {
      key: 'metric_key',
      label: 'Metric',
      kind: 'select',
      required: false,
      options: [
        { value: 'spend',       label: 'Spend' },
        { value: 'cpa',         label: 'CPA' },
        { value: 'ctr',         label: 'CTR' },
        { value: 'roas',        label: 'ROAS' },
        { value: 'impressions', label: 'Impressions' },
        { value: 'clicks',      label: 'Clicks' },
        { value: 'conversions', label: 'Conversions' },
      ],
      helpText: 'Which metric triggered the alert.',
    },
    {
      key: 'change_type',
      label: 'Change type',
      kind: 'select',
      required: false,
      options: [
        { value: 'percent',  label: 'Percent' },
        { value: 'absolute', label: 'Absolute' },
      ],
    },
    {
      key: 'change_window',
      label: 'Change window',
      kind: 'select',
      required: false,
      options: [
        { value: 'hourly',  label: 'Hourly' },
        { value: 'daily',   label: 'Daily' },
        { value: 'weekly',  label: 'Weekly' },
        { value: 'monthly', label: 'Monthly' },
      ],
    },
    {
      key: 'change_value',
      label: 'Change value',
      kind: 'number',
      required: false,
      placeholder: 'e.g. 25 for 25% spike',
    },
    {
      key: 'current_value',
      label: 'Current value',
      kind: 'number',
      required: false,
      placeholder: 'Current metric value',
    },
    {
      key: 'previous_value',
      label: 'Previous value',
      kind: 'number',
      required: false,
      placeholder: 'Previous metric value',
    },
    {
      key: 'affected_entities',
      label: 'Affected entities',
      kind: 'tags',
      required: false,
      placeholder: 'Comma-separated campaign/ad set names',
    },
  ],
  editFields: [
    {
      key: 'alert_type',
      label: 'Alert type',
      kind: 'select',
      required: true,
      options: [
        { value: 'spend_spike',       label: 'Spend spike' },
        { value: 'policy_violation',  label: 'Policy violation' },
        { value: 'performance_drop',  label: 'Performance drop' },
        { value: 'delivery_issue',    label: 'Delivery issue' },
        { value: 'other',             label: 'Other' },
      ],
    },
    {
      key: 'severity',
      label: 'Severity',
      kind: 'select',
      required: true,
      options: [
        { value: 'low',      label: 'Low' },
        { value: 'medium',   label: 'Medium' },
        { value: 'high',     label: 'High' },
        { value: 'critical', label: 'Critical' },
      ],
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'open',          label: 'Open' },
        { value: 'acknowledged',  label: 'Acknowledged' },
        { value: 'in_progress',   label: 'In Progress' },
        { value: 'mitigated',     label: 'Mitigated' },
        { value: 'resolved',      label: 'Resolved' },
        { value: 'closed',        label: 'Closed' },
      ],
    },
    {
      key: 'investigation_notes',
      label: 'Investigation notes',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'What you know so far',
    },
    {
      key: 'resolution_steps',
      label: 'Resolution steps',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Steps taken to resolve',
    },
    {
      key: 'metric_key',
      label: 'Metric',
      kind: 'select',
      required: false,
      options: [
        { value: 'spend',       label: 'Spend' },
        { value: 'cpa',         label: 'CPA' },
        { value: 'ctr',         label: 'CTR' },
        { value: 'roas',        label: 'ROAS' },
        { value: 'impressions', label: 'Impressions' },
        { value: 'clicks',      label: 'Clicks' },
        { value: 'conversions', label: 'Conversions' },
      ],
    },
    {
      key: 'change_type',
      label: 'Change type',
      kind: 'select',
      required: false,
      options: [
        { value: 'percent',  label: 'Percent' },
        { value: 'absolute', label: 'Absolute' },
      ],
    },
    {
      key: 'change_window',
      label: 'Change window',
      kind: 'select',
      required: false,
      options: [
        { value: 'hourly',  label: 'Hourly' },
        { value: 'daily',   label: 'Daily' },
        { value: 'weekly',  label: 'Weekly' },
        { value: 'monthly', label: 'Monthly' },
      ],
    },
    {
      key: 'change_value',
      label: 'Change value',
      kind: 'number',
      required: false,
      placeholder: 'e.g. 25 for 25% spike',
    },
    {
      key: 'current_value',
      label: 'Current value',
      kind: 'number',
      required: false,
      placeholder: 'Current metric value',
    },
    {
      key: 'previous_value',
      label: 'Previous value',
      kind: 'number',
      required: false,
      placeholder: 'Previous metric value',
    },
    {
      key: 'postmortem_root_cause',
      label: 'Root cause',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'What caused this alert',
    },
    {
      key: 'postmortem_prevention',
      label: 'Prevention',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'How to prevent this in future',
    },
  ],
};

const EXPERIMENT: TypeSchema = {
  type: 'experiment',
  label: taskTypeLabel('experiment'),
  contentType: 'experiment',
  fields: [
    {
      key: 'hypothesis',
      label: 'Hypothesis',
      kind: 'textarea',
      required: true,
      rows: 3,
      placeholder: 'If we change X, then Y will happen because …',
    },
    {
      key: 'expected_outcome',
      label: 'Expected outcome',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'What success looks like',
    },
    {
      key: 'success_metric',
      label: 'Success metric',
      kind: 'text',
      required: false,
      placeholder: 'e.g. CPA, ROAS, conversion rate',
    },
    {
      key: 'description',
      label: 'Description',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Further context for the experiment',
    },
    {
      key: 'constraints',
      label: 'Constraints',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Budget, time, or data constraints',
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'draft',     label: 'Draft' },
        { value: 'running',   label: 'Running' },
        { value: 'paused',    label: 'Paused' },
        { value: 'completed', label: 'Completed' },
      ],
      helpText: 'Defaults to Draft if left blank.',
    },
  ],
  editFields: [
    {
      key: 'hypothesis',
      label: 'Hypothesis',
      kind: 'textarea',
      required: true,
      rows: 3,
      placeholder: 'If we change X, then Y will happen because …',
    },
    {
      key: 'expected_outcome',
      label: 'Expected outcome',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'What success looks like',
    },
    {
      key: 'success_metric',
      label: 'Success metric',
      kind: 'text',
      required: false,
      placeholder: 'e.g. CPA, ROAS, conversion rate',
    },
    {
      key: 'description',
      label: 'Description',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Further context for the experiment',
    },
    {
      key: 'constraints',
      label: 'Constraints',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Budget, time, or data constraints',
    },
    {
      key: 'status',
      label: 'Status',
      kind: 'select',
      required: false,
      options: [
        { value: 'draft',     label: 'Draft' },
        { value: 'running',   label: 'Running' },
        { value: 'paused',    label: 'Paused' },
        { value: 'completed', label: 'Completed' },
      ],
    },
    {
      key: 'experiment_outcome',
      label: 'Outcome',
      kind: 'select',
      required: false,
      options: [
        { value: 'win',          label: 'Win' },
        { value: 'lose',         label: 'Lose' },
        { value: 'inconclusive', label: 'Inconclusive' },
      ],
      helpText: 'Can only be set when status is Completed.',
      showWhen: (data) => data['status'] === 'completed',
    },
    {
      key: 'outcome_notes',
      label: 'Outcome notes',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Summarise learnings and conclusions from the experiment',
      showWhen: (data) => data['status'] === 'completed',
    },
  ],
};

const OPTIMIZATION: TypeSchema = {
  type: 'optimization',
  label: taskTypeLabel('optimization'),
  contentType: 'optimization',
  fields: [
    {
      key: 'action_type',
      label: 'Action type',
      kind: 'select',
      required: true,
      options: [
        { value: 'pause', label: 'Pause' },
        { value: 'scale', label: 'Scale' },
        { value: 'duplicate', label: 'Duplicate' },
        { value: 'edit', label: 'Edit' },
      ],
    },
    {
      key: 'rationale',
      label: 'Rationale',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Why this optimization is needed',
    },
    {
      key: 'execution_status',
      label: 'Execution status',
      kind: 'select',
      required: false,
      options: [
        { value: 'detected',   label: 'Detected' },
        { value: 'planned',    label: 'Planned' },
        { value: 'executed',   label: 'Executed' },
        { value: 'monitoring', label: 'Monitoring' },
        { value: 'completed',  label: 'Completed' },
        { value: 'cancelled',  label: 'Cancelled' },
      ],
    },
    {
      key: 'planned_action',
      label: 'Planned action',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'What will be changed and how',
    },
    {
      key: 'outcome_notes',
      label: 'Outcome notes',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Result and learnings from this optimization',
    },
  ],
  editFields: [
    {
      key: 'action_type',
      label: 'Action type',
      kind: 'select',
      required: true,
      options: [
        { value: 'pause',     label: 'Pause' },
        { value: 'scale',     label: 'Scale' },
        { value: 'duplicate', label: 'Duplicate' },
        { value: 'edit',      label: 'Edit' },
      ],
    },
    {
      key: 'rationale',
      label: 'Rationale',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Why this optimization is needed',
    },
    {
      key: 'execution_status',
      label: 'Execution status',
      kind: 'select',
      required: false,
      options: [
        { value: 'detected',   label: 'Detected' },
        { value: 'planned',    label: 'Planned' },
        { value: 'executed',   label: 'Executed' },
        { value: 'monitoring', label: 'Monitoring' },
        { value: 'completed',  label: 'Completed' },
        { value: 'cancelled',  label: 'Cancelled' },
      ],
    },
    {
      key: 'planned_action',
      label: 'Planned action',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'What will be changed and how',
    },
    {
      key: 'outcome_notes',
      label: 'Outcome notes',
      kind: 'textarea',
      required: false,
      rows: 3,
      placeholder: 'Result and learnings from this optimization',
    },
  ],
};

const COMMUNICATION: TypeSchema = {
  type: 'communication',
  label: taskTypeLabel('communication'),
  contentType: 'clientcommunication',
  fields: [
    {
      key: 'communication_type',
      label: 'Communication type',
      kind: 'select',
      required: true,
      options: [
        { value: 'budget_change',      label: 'Budget change' },
        { value: 'creative_approval',  label: 'Creative approval' },
        { value: 'kpi_update',         label: 'KPI update' },
        { value: 'targeting_change',   label: 'Targeting change' },
        { value: 'other',              label: 'Other' },
      ],
    },
    {
      key: 'impacted_areas',
      label: 'Impacted areas',
      kind: 'tags',
      required: true,
      placeholder: 'budget, creative, kpi, targeting',
      helpText: 'Comma-separated. Valid values: budget, creative, kpi, targeting.',
    },
    {
      key: 'stakeholders',
      label: 'Stakeholders',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Names or roles to notify',
    },
    {
      key: 'required_actions',
      label: 'Required actions',
      kind: 'textarea',
      required: true,
      rows: 2,
      placeholder: 'What you need the client to do',
    },
    {
      key: 'client_deadline',
      label: 'Client deadline',
      kind: 'date',
      required: false,
    },
    {
      key: 'notes',
      label: 'Notes',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Additional context',
    },
  ],
};

const PLATFORM_POLICY_UPDATE: TypeSchema = {
  type: 'platform_policy_update',
  label: taskTypeLabel('platform_policy_update'),
  contentType: 'platformpolicyupdate',
  fields: [
    {
      key: 'platform',
      label: 'Platform',
      kind: 'select',
      required: true,
      optionsLoader: 'policy.platforms',
      placeholder: 'Select a platform',
    },
    {
      key: 'policy_change_type',
      label: 'Policy change type',
      kind: 'select',
      required: true,
      optionsLoader: 'policy.change_types',
      placeholder: 'Select a policy change type',
    },
    {
      key: 'policy_description',
      label: 'Policy description',
      kind: 'textarea',
      required: true,
      rows: 3,
      placeholder: 'Describe the policy change and its implications',
    },
    {
      key: 'immediate_actions_required',
      label: 'Immediate actions required',
      kind: 'textarea',
      required: true,
      rows: 3,
      placeholder: 'Describe immediate actions that need to be taken',
    },
    {
      key: 'policy_reference_url',
      label: 'Policy reference URL',
      kind: 'url',
      required: false,
      placeholder: 'https://example.com/policy-update',
    },
    {
      key: 'effective_date',
      label: 'Effective date',
      kind: 'date',
      required: false,
    },
    {
      key: 'action_deadline',
      label: 'Action deadline',
      kind: 'date',
      required: false,
    },
    {
      key: 'affected_campaigns',
      label: 'Affected campaigns',
      kind: 'tags',
      required: false,
      placeholder: 'Comma-separated campaign names',
    },
    {
      key: 'affected_ad_sets',
      label: 'Affected ad sets',
      kind: 'tags',
      required: false,
      placeholder: 'Comma-separated ad set names',
    },
    {
      key: 'affected_assets',
      label: 'Affected assets',
      kind: 'tags',
      required: false,
      placeholder: 'Comma-separated asset names',
    },
    {
      key: 'performance_impact',
      label: 'Performance impact',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Describe potential impact on performance metrics',
    },
    {
      key: 'budget_impact',
      label: 'Budget impact',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Describe potential impact on budget or spend',
    },
    {
      key: 'compliance_risk',
      label: 'Compliance risk',
      kind: 'textarea',
      required: false,
      rows: 2,
      placeholder: 'Describe risk if the policy change is not addressed',
    },
  ],
};

export const TYPE_SCHEMAS: Record<string, TypeSchema> = {
  [BUDGET.type]: BUDGET,
  [ASSET.type]: ASSET,
  [RETROSPECTIVE.type]: RETROSPECTIVE,
  [REPORT.type]: REPORT,
  [SCALING.type]: SCALING,
  [ALERT.type]: ALERT,
  [EXPERIMENT.type]: EXPERIMENT,
  [OPTIMIZATION.type]: OPTIMIZATION,
  [COMMUNICATION.type]: COMMUNICATION,
  [PLATFORM_POLICY_UPDATE.type]: PLATFORM_POLICY_UPDATE,
};

const schemaSelectFields = (schema: TypeSchema): FieldDef[] =>
  [...schema.fields, ...(schema.editFields ?? [])].filter((field) => field.options);

const DEFAULT_FIELD_OPTIONS = new Map<FieldDef, FieldOption[]>(
  Object.values(TYPE_SCHEMAS).flatMap((schema) =>
    schemaSelectFields(schema).map((field) => [field, field.options ?? []] as [FieldDef, FieldOption[]]),
  ),
);

export interface DefaultFieldOptions {
  taskType: string;
  field: string;
  fieldLabel: string;
  options: FieldOption[];
}

export function getDefaultFieldOptions(): DefaultFieldOptions[] {
  const seen = new Set<string>();
  return Object.values(TYPE_SCHEMAS).flatMap((schema) =>
    schemaSelectFields(schema).flatMap((field) => {
      const id = `${schema.type}.${field.key}`;
      if (seen.has(id)) return [];
      seen.add(id);
      return [{
        taskType: schema.type,
        field: field.key,
        fieldLabel: field.label,
        options: DEFAULT_FIELD_OPTIONS.get(field) ?? [],
      }];
    }),
  );
}

export function applyTaskFieldOptionConfig(rows: TaskFieldOptionConfig[]): void {
  Object.values(TYPE_SCHEMAS).forEach((schema) => {
    schemaSelectFields(schema).forEach((field) => {
      const defaults = DEFAULT_FIELD_OPTIONS.get(field) ?? [];
      const byValue = new Map(
        rows
          .filter((row) => row.task_type === schema.type && row.field === field.key)
          .map((row) => [row.value, row]),
      );
      const orderOf = (option: FieldOption) => byValue.get(option.value)?.order ?? defaults.indexOf(option);
      field.options = defaults
        .filter((option) => byValue.get(option.value)?.enabled !== false)
        .sort((a, b) => orderOf(a) - orderOf(b))
        .map((option) => ({ ...option, label: byValue.get(option.value)?.label || option.label }));
    });
  });
}

export function getTypeSchema(type: string | null | undefined): TypeSchema | null {
  if (!type) return null;
  return TYPE_SCHEMAS[type] ?? null;
}

/** Return just the required-field keys that are currently unfilled. */
export function getUnfilledRequiredKeys(
  schema: TypeSchema,
  formState: Record<string, string>,
): string[] {
  return schema.fields
    .filter((f) => f.required)
    .filter((f) => !(formState[f.key] ?? '').toString().trim())
    .map((f) => f.key);
}
