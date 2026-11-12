export interface TaskTypeConfig {
  key: string;
  default_label: string;
  label: string | null;
  short_label: string | null;
  color: string | null;
  enabled: boolean;
  order: number;
}

export interface TaskStatusConfig {
  key: string;
  default_label: string;
  label: string | null;
  default_category: string;
  category: string | null;
}

export interface TaskPriorityConfig {
  key: string;
  default_label: string;
  label: string | null;
}

export interface TaskFieldOptionConfig {
  task_type: string;
  field: string;
  value: string;
  label: string | null;
  enabled: boolean;
  order: number;
}

export interface StatusCategory {
  key: string;
  label: string;
}

export interface TaskConfig {
  task_types: TaskTypeConfig[];
  task_statuses: TaskStatusConfig[];
  task_priorities: TaskPriorityConfig[];
  task_field_options: TaskFieldOptionConfig[];
  status_categories: StatusCategory[];
}

export type TaskConfigUpdate = Partial<
  Pick<TaskConfig, 'task_types' | 'task_statuses' | 'task_priorities' | 'task_field_options'>
>;
