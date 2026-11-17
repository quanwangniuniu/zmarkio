import type { TaskPriorityConfig } from '@/types/orgCustomization';

export const TASK_PRIORITY_VALUES = ['HIGHEST', 'HIGH', 'MEDIUM', 'LOW', 'LOWEST'] as const;

export type TaskPriorityValue = (typeof TASK_PRIORITY_VALUES)[number];

export interface TaskPriorityDefinition {
  label: string;
  dot: string;
}

const TASK_PRIORITY_DEFINITION_BY_VALUE: Record<TaskPriorityValue, TaskPriorityDefinition> = {
  HIGHEST: { label: 'Highest', dot: 'bg-rose-500' },
  HIGH: { label: 'High', dot: 'bg-orange-500' },
  MEDIUM: { label: 'Medium', dot: 'bg-amber-400' },
  LOW: { label: 'Low', dot: 'bg-sky-400' },
  LOWEST: { label: 'Lowest', dot: 'bg-gray-300' },
};

export const TASK_PRIORITY_BY_VALUE: Record<string, TaskPriorityDefinition> =
  TASK_PRIORITY_DEFINITION_BY_VALUE;

export const TASK_PRIORITY_OPTIONS: string[] = [...TASK_PRIORITY_VALUES];

export const getTaskPriorityDefinition = (priority?: string | null): TaskPriorityDefinition =>
  TASK_PRIORITY_DEFINITION_BY_VALUE[(priority as TaskPriorityValue) ?? 'MEDIUM'] ??
  TASK_PRIORITY_DEFINITION_BY_VALUE.MEDIUM;

const DEFAULT_TASK_PRIORITY_LABELS: Record<string, string> = Object.fromEntries(
  Object.entries(TASK_PRIORITY_DEFINITION_BY_VALUE).map(([value, definition]) => [value, definition.label]),
);

export const applyTaskPriorityConfig = (configs: TaskPriorityConfig[]): void => {
  configs.forEach((config) => {
    const definition = TASK_PRIORITY_DEFINITION_BY_VALUE[config.key as TaskPriorityValue];
    if (!definition) return;
    definition.label = config.label || DEFAULT_TASK_PRIORITY_LABELS[config.key];
  });
};
