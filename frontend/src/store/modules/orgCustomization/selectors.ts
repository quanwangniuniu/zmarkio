import type { RootState } from '@/store';

export const selectSystemConfig = (state: RootState, projectId?: number | string) =>
  projectId === undefined ? undefined : state.orgCustomization.systemConfigs[String(projectId)];

export const selectSystemConfigSaving = (state: RootState) => state.orgCustomization.saving;
