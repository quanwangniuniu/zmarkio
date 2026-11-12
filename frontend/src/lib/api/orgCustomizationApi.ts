import api from '../api';
import type { TaskConfig, TaskConfigUpdate } from '@/types/orgCustomization';

const TASK_CONFIG_URL = '/api/org-customization/task-config/';

export async function fetchTaskConfig(): Promise<TaskConfig> {
  const response = await api.get<TaskConfig>(TASK_CONFIG_URL);
  return response.data;
}

export async function saveTaskConfig(config: TaskConfigUpdate): Promise<TaskConfig> {
  const response = await api.put<TaskConfig>(TASK_CONFIG_URL, config);
  return response.data;
}
