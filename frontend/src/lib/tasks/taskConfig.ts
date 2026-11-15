import { fetchTaskConfig } from '@/lib/api/orgCustomizationApi';
import { applyTaskPriorityConfig } from '@/lib/tasks/taskPriorities';
import { applyTaskStatusConfig } from '@/lib/tasks/taskStatuses';
import { applyTaskTypeConfig } from '@/lib/tasks/taskTypes';
import { applyTaskFieldOptionConfig } from '@/lib/tasks/typeFieldSchemas';
import type { TaskConfig } from '@/types/orgCustomization';

const taskConfigRequests = new Map<string, Promise<TaskConfig | null>>();
let currentProjectKey: string | null = null;

export function applyTaskConfig(config: TaskConfig): void {
  applyTaskTypeConfig(config.task_types);
  applyTaskStatusConfig(config.task_statuses);
  applyTaskPriorityConfig(config.task_priorities);
  applyTaskFieldOptionConfig(config.task_field_options);
}

export function loadTaskConfig(projectId: number | string): Promise<TaskConfig | null> {
  const key = String(projectId);
  currentProjectKey = key;
  let request = taskConfigRequests.get(key);
  if (!request) {
    request = fetchTaskConfig(projectId).catch(() => {
      taskConfigRequests.delete(key);
      return null;
    });
    taskConfigRequests.set(key, request);
  }
  return request.then((config) => {
    if (config && currentProjectKey === key) applyTaskConfig(config);
    return config;
  });
}

export function storeTaskConfig(projectId: number | string, config: TaskConfig): void {
  taskConfigRequests.set(String(projectId), Promise.resolve(config));
  if (currentProjectKey === String(projectId)) applyTaskConfig(config);
}
