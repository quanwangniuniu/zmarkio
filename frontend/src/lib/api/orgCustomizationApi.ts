import api from '../api';
import type { TaskConfig, TaskConfigUpdate } from '@/types/orgCustomization';

const TASK_CONFIG_URL = '/api/org-customization/task-config/';

export async function fetchTaskConfig(projectId: number | string): Promise<TaskConfig> {
  const response = await api.get<TaskConfig>(TASK_CONFIG_URL, { params: { project_id: projectId } });
  return response.data;
}

export async function saveTaskConfig(projectId: number | string, config: TaskConfigUpdate): Promise<TaskConfig> {
  const response = await api.put<TaskConfig>(TASK_CONFIG_URL, config, { params: { project_id: projectId } });
  return response.data;
}
