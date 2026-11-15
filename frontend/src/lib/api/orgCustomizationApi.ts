import api from '../api';
import type { OptionConfig, OptionModuleConfig, TaskConfig, TaskConfigUpdate } from '@/types/orgCustomization';

const TASK_CONFIG_URL = '/api/org-customization/task-config/';

export async function fetchTaskConfig(projectId: number | string): Promise<TaskConfig> {
  const response = await api.get<TaskConfig>(TASK_CONFIG_URL, { params: { project_id: projectId } });
  return response.data;
}

export async function saveTaskConfig(projectId: number | string, config: TaskConfigUpdate): Promise<TaskConfig> {
  const response = await api.put<TaskConfig>(TASK_CONFIG_URL, config, { params: { project_id: projectId } });
  return response.data;
}

const optionConfigUrl = (module: string) => `/api/org-customization/${module}-config/`;

export async function fetchOptionConfig(module: string, projectId: number | string): Promise<OptionModuleConfig> {
  const response = await api.get<OptionModuleConfig>(optionConfigUrl(module), { params: { project_id: projectId } });
  return response.data;
}

export async function saveOptionConfig(
  module: string,
  projectId: number | string,
  options: OptionConfig[],
): Promise<OptionModuleConfig> {
  const response = await api.put<OptionModuleConfig>(
    optionConfigUrl(module),
    { options },
    { params: { project_id: projectId } },
  );
  return response.data;
}
