import api from '../api';
import type {
  OptionModuleConfig,
  SystemConfig,
  SystemConfigUpdate,
  TaskConfig,
  TaskConfigUpdate,
} from '@/types/orgCustomization';

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

const SYSTEM_CONFIG_URL = '/api/org-customization/system-config/';

export async function fetchSystemConfig(projectId: number | string): Promise<SystemConfig> {
  const response = await api.get<SystemConfig>(SYSTEM_CONFIG_URL, { params: { project_id: projectId } });
  return response.data;
}

export async function saveSystemConfig(projectId: number | string, config: SystemConfigUpdate): Promise<SystemConfig> {
  const response = await api.put<SystemConfig>(SYSTEM_CONFIG_URL, config, { params: { project_id: projectId } });
  return response.data;
}
