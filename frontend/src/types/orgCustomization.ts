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
  can_edit: boolean;
}

export type TaskConfigUpdate = Partial<
  Pick<TaskConfig, 'task_types' | 'task_statuses' | 'task_priorities' | 'task_field_options'>
>;

export interface OptionConfig {
  field: string;
  value: string;
  default_label: string;
  label: string | null;
  color: string | null;
  enabled: boolean;
  order: number;
  is_default: boolean;
  editable: string[];
}

export interface OptionModuleConfig {
  options: OptionConfig[];
  can_edit: boolean;
}

export interface SystemConfig {
  task: Omit<TaskConfig, 'can_edit'>;
  csm: Omit<OptionModuleConfig, 'can_edit'>;
  decisions: Omit<OptionModuleConfig, 'can_edit'>;
  campaigns: Omit<OptionModuleConfig, 'can_edit'>;
  navigation: Omit<OptionModuleConfig, 'can_edit'>;
  tasks: Omit<OptionModuleConfig, 'can_edit'>;
  can_edit: boolean;
}

export interface SystemConfigUpdate {
  task?: TaskConfigUpdate;
  csm?: { options: OptionConfig[] };
  decisions?: { options: OptionConfig[] };
  campaigns?: { options: OptionConfig[] };
  navigation?: { options: OptionConfig[] };
  tasks?: { options: OptionConfig[] };
}
