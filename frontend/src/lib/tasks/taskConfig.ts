import { fetchTaskConfig } from '@/lib/api/orgCustomizationApi';
import { applyTaskPriorityConfig } from '@/lib/tasks/taskPriorities';
import { applyTaskStatusConfig } from '@/lib/tasks/taskStatuses';
import { applyTaskTypeConfig } from '@/lib/tasks/taskTypes';
import { applyTaskFieldOptionConfig } from '@/lib/tasks/typeFieldSchemas';
import type { TaskConfig } from '@/types/orgCustomization';

let taskConfigRequest: Promise<void> | null = null;

export function applyTaskConfig(config: TaskConfig): void {
  applyTaskTypeConfig(config.task_types);
  applyTaskStatusConfig(config.task_statuses);
  applyTaskPriorityConfig(config.task_priorities);
  applyTaskFieldOptionConfig(config.task_field_options);
}

export function loadTaskConfig(): Promise<void> {
  if (!taskConfigRequest) {
    taskConfigRequest = fetchTaskConfig()
      .then(applyTaskConfig)
      .catch(() => {
        taskConfigRequest = null;
      });
  }
  return taskConfigRequest;
}
