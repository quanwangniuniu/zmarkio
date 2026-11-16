import type { TaskStatusConfig } from '@/types/orgCustomization';

export const TASK_STATUS_VALUES = [
  'DRAFT',
  'SUBMITTED',
  'UNDER_REVIEW',
  'APPROVED',
  'REJECTED',
  'LOCKED',
  'CANCELLED',
] as const;

export type TaskStatusValue = (typeof TASK_STATUS_VALUES)[number];

export interface TaskStatusDefinition {
  label: string;
  classes: string;
  /** Kanban mini-card top bar (`border-t-*`). */
  cardTopBorder: string;
  /** Title row text + tint; driven by API `status`, not task `type`. */
  summaryStrip: string;
}

const TASK_STATUS_DEFINITION_BY_VALUE: Record<TaskStatusValue, TaskStatusDefinition> = {
  DRAFT: {
    label: 'Draft',
    classes: 'bg-gray-100 text-gray-700',
    cardTopBorder: 'border-t-gray-500',
    summaryStrip: 'bg-gray-100 text-gray-900',
  },
  SUBMITTED: {
    label: 'Submitted',
    classes: 'bg-sky-50 text-sky-700',
    cardTopBorder: 'border-t-sky-500',
    summaryStrip: 'bg-sky-50 text-sky-800',
  },
  UNDER_REVIEW: {
    label: 'In review',
    classes: 'bg-amber-50 text-amber-700',
    cardTopBorder: 'border-t-amber-500',
    summaryStrip: 'bg-amber-50 text-amber-900',
  },
  APPROVED: {
    label: 'Approved',
    classes: 'bg-emerald-50 text-emerald-700',
    cardTopBorder: 'border-t-emerald-500',
    summaryStrip: 'bg-emerald-50 text-emerald-900',
  },
  REJECTED: {
    label: 'Rejected',
    classes: 'bg-rose-50 text-rose-700',
    cardTopBorder: 'border-t-rose-500',
    summaryStrip: 'bg-rose-50 text-rose-900',
  },
  LOCKED: {
    label: 'Locked',
    classes: 'bg-violet-50 text-violet-700',
    cardTopBorder: 'border-t-violet-500',
    summaryStrip: 'bg-violet-50 text-violet-900',
  },
  CANCELLED: {
    label: 'Cancelled',
    classes: 'bg-gray-100 text-gray-500 line-through',
    cardTopBorder: 'border-t-gray-400',
    summaryStrip: 'bg-gray-100 text-gray-600',
  },
};

export const TASK_STATUS_BY_VALUE: Record<string, TaskStatusDefinition> =
  TASK_STATUS_DEFINITION_BY_VALUE;

export const TASK_STATUS_OPTIONS: string[] = [...TASK_STATUS_VALUES];

export const getTaskStatusDefinition = (status?: string | null): TaskStatusDefinition =>
  TASK_STATUS_DEFINITION_BY_VALUE[(status as TaskStatusValue) ?? 'DRAFT'] ??
  TASK_STATUS_DEFINITION_BY_VALUE.DRAFT;

const DEFAULT_TASK_STATUS_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(TASK_STATUS_DEFINITION_BY_VALUE).map(([value, definition]) => [value, definition.label]),
);

export const applyTaskStatusConfig = (configs: TaskStatusConfig[]): void => {
  configs.forEach((config) => {
    const definition = TASK_STATUS_DEFINITION_BY_VALUE[config.key as TaskStatusValue];
    if (!definition) return;
    definition.label = config.label || DEFAULT_TASK_STATUS_LABELS[config.key];
  });
};
