import { fetchSystemConfig, saveSystemConfig } from '@/lib/api/orgCustomizationApi';
import { baseApi } from '@/store/api/baseApi';
import { fromApi } from '@/store/api/baseQuery';
import type { SystemConfig, SystemConfigUpdate } from '@/types/orgCustomization';

export const orgCustomizationApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    getSystemConfig: build.query<SystemConfig, number | string>({
      queryFn: (projectId) => fromApi(() => fetchSystemConfig(projectId)),
    }),
    saveSystemConfig: build.mutation<SystemConfig, { projectId: number | string; config: SystemConfigUpdate }>({
      queryFn: ({ projectId, config }) => fromApi(() => saveSystemConfig(projectId, config)),
      async onQueryStarted({ projectId }, { dispatch, queryFulfilled }) {
        const { data } = await queryFulfilled.catch(() => ({ data: undefined }));
        if (data) dispatch(orgCustomizationApi.util.updateQueryData('getSystemConfig', projectId, () => data));
      },
    }),
  }),
});

export const { useGetSystemConfigQuery, useSaveSystemConfigMutation } = orgCustomizationApi;
