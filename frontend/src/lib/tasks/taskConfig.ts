import { fetchTaskConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyTaskPriorityConfig } from '@/lib/tasks/taskPriorities';
import { applyTaskStatusConfig } from '@/lib/tasks/taskStatuses';
import { applyTaskTypeConfig } from '@/lib/tasks/taskTypes';
import { applyTaskFieldOptionConfig } from '@/lib/tasks/typeFieldSchemas';
import type { TaskConfig } from '@/types/orgCustomization';

export function applyTaskConfig(config: TaskConfig): void {
  applyTaskTypeConfig(config.task_types);
  applyTaskStatusConfig(config.task_statuses);
  applyTaskPriorityConfig(config.task_priorities);
  applyTaskFieldOptionConfig(config.task_field_options);
}

const taskConfigLoader = createConfigLoader((projectId) => fetchTaskConfig(projectId), applyTaskConfig);

export const loadTaskConfig = taskConfigLoader.load;
export const storeTaskConfig = taskConfigLoader.store;
