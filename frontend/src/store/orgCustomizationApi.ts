import { createApi } from '@reduxjs/toolkit/query/react';
import type { SystemConfig, SystemConfigUpdate } from '@/types/orgCustomization';
import { axiosBaseQuery } from './baseQuery';

const SYSTEM_CONFIG_URL = '/api/org-customization/system-config/';

export const orgCustomizationApi = createApi({
  reducerPath: 'orgCustomizationApi',
  baseQuery: axiosBaseQuery(),
  endpoints: (build) => ({
    getSystemConfig: build.query<SystemConfig, number | string>({
      query: (projectId) => ({ url: SYSTEM_CONFIG_URL, params: { project_id: projectId } }),
    }),
    saveSystemConfig: build.mutation<SystemConfig, { projectId: number | string; config: SystemConfigUpdate }>({
      query: ({ projectId, config }) => ({ url: SYSTEM_CONFIG_URL, method: 'PUT', data: config, params: { project_id: projectId } }),
      async onQueryStarted({ projectId }, { dispatch, queryFulfilled }) {
        const { data } = await queryFulfilled.catch(() => ({ data: undefined }));
        if (data) dispatch(orgCustomizationApi.util.updateQueryData('getSystemConfig', projectId, () => data));
      },
    }),
  }),
});

export const { useGetSystemConfigQuery, useSaveSystemConfigMutation } = orgCustomizationApi;
