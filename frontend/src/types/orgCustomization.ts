export interface TaskTypeConfig {
  key: string;
  default_label: string;
  label: string | null;
  enabled: boolean;
  order: number;
}

export interface TaskStatusConfig {
  key: string;
  default_label: string;
  label: string | null;
  color: string | null;
  order: number;
}

export interface TaskConfig {
  task_types: TaskTypeConfig[];
  task_statuses: TaskStatusConfig[];
}
