import type { TaskTypeConfig } from '@/types/orgCustomization';

// Task type domain definitions shared by task views, meeting conversion, and forms.
// Order matches /api/task-types/ and drives kanban column ordering.
export const TASK_TYPE_VALUES = [
  'budget',
  'asset',
  'retrospective',
  'report',
  'execution',
  'scaling',
  'alert',
  'experiment',
  'optimization',
  'communication',
  'platform_policy_update',
] as const;

export type TaskTypeValue = (typeof TASK_TYPE_VALUES)[number];

export interface TaskTypeDefinition {
  value: TaskTypeValue;
  label: string;
  shortLabel: string;
  /** Donut and column accent color. */
  hex: string;
}

export const TASK_TYPE_DEFINITIONS: readonly TaskTypeDefinition[] = [
  { value: 'budget', label: 'Budget', shortLabel: 'Budget', hex: '#3CCED7' },
  { value: 'asset', label: 'Asset', shortLabel: 'Asset', hex: '#A6E661' },
  { value: 'retrospective', label: 'Retrospective', shortLabel: 'Retro', hex: '#8B5CF6' },
  { value: 'report', label: 'Report', shortLabel: 'Report', hex: '#F59E0B' },
  { value: 'execution', label: 'Execution', shortLabel: 'Exec', hex: '#10B981' },
  { value: 'scaling', label: 'Scaling', shortLabel: 'Scaling', hex: '#EC4899' },
  { value: 'alert', label: 'Alert', shortLabel: 'Alert', hex: '#EF4444' },
  { value: 'experiment', label: 'Experiment', shortLabel: 'Exp', hex: '#6366F1' },
  { value: 'optimization', label: 'Optimization', shortLabel: 'Opt', hex: '#14B8A6' },
  { value: 'communication', label: 'Client Communication', shortLabel: 'Comm', hex: '#F97316' },
  { value: 'platform_policy_update', label: 'Platform Policy Update', shortLabel: 'Policy', hex: '#64748B' },
];

export const TASK_TYPE_BY_VALUE: Record<TaskTypeValue, TaskTypeDefinition> =
  TASK_TYPE_DEFINITIONS.reduce(
    (acc, definition) => {
      acc[definition.value] = definition;
      return acc;
    },
    {} as Record<TaskTypeValue, TaskTypeDefinition>,
  );

export const TASK_TYPE_SHORT_LABEL_BY_VALUE: Record<TaskTypeValue, string> =
  TASK_TYPE_DEFINITIONS.reduce(
    (acc, definition) => {
      acc[definition.value] = definition.shortLabel;
      return acc;
    },
    {} as Record<TaskTypeValue, string>,
  );

export const TASK_TYPE_ORDER_INDEX: Record<TaskTypeValue, number> =
  TASK_TYPE_DEFINITIONS.reduce(
    (acc, definition, index) => {
      acc[definition.value] = index;
      return acc;
    },
    {} as Record<TaskTypeValue, number>,
  );

export const getTaskTypeDefinition = (value?: string | null): TaskTypeDefinition | undefined => {
  if (!value) return undefined;
  return TASK_TYPE_BY_VALUE[value as TaskTypeValue];
};

export const getTaskTypeLabel = (value?: string | null): string | undefined =>
  getTaskTypeDefinition(value)?.label;

export const getTaskTypeShortLabel = (value?: string | null): string | undefined =>
  getTaskTypeDefinition(value)?.shortLabel;

const DEFAULT_TASK_TYPE_DEFINITIONS: Record<string, TaskTypeDefinition> = Object.fromEntries(
  TASK_TYPE_DEFINITIONS.map((definition) => [definition.value, { ...definition }]),
);

export const applyTaskTypeConfig = (configs: TaskTypeConfig[]): void => {
  configs.forEach((config) => {
    const definition = TASK_TYPE_BY_VALUE[config.key as TaskTypeValue];
    const defaults = DEFAULT_TASK_TYPE_DEFINITIONS[config.key];
    if (!definition || !defaults) return;
    definition.label = config.label || defaults.label;
    definition.shortLabel = config.short_label || defaults.shortLabel;
    definition.hex = config.color || defaults.hex;
    TASK_TYPE_SHORT_LABEL_BY_VALUE[definition.value] = definition.shortLabel;
  });
};
