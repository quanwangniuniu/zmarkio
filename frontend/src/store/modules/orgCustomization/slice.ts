import { createAsyncThunk, createSlice } from '@reduxjs/toolkit';
import { fetchSystemConfig, saveSystemConfig as putSystemConfig } from '@/lib/api/orgCustomizationApi';
import { getApiErrorDetail } from '@/lib/api/errorMessage';
import type { SystemConfig, SystemConfigUpdate } from '@/types/orgCustomization';

type ProjectId = number | string;

export interface OrgCustomizationState {
  systemConfigs: Record<string, SystemConfig>;
  loading: boolean;
  saving: boolean;
}

const initialState: OrgCustomizationState = { systemConfigs: {}, loading: false, saving: false };

export const loadSystemConfig = createAsyncThunk<SystemConfig, ProjectId, { rejectValue: string }>(
  'orgCustomization/loadSystemConfig',
  async (projectId, { rejectWithValue }) => {
    try {
      return await fetchSystemConfig(projectId);
    } catch (error) {
      return rejectWithValue(getApiErrorDetail(error, 'Failed to load system configuration'));
    }
  },
);

export const saveSystemConfig = createAsyncThunk<
  SystemConfig,
  { projectId: ProjectId; config: SystemConfigUpdate },
  { rejectValue: string }
>('orgCustomization/saveSystemConfig', async ({ projectId, config }, { rejectWithValue }) => {
  try {
    return await putSystemConfig(projectId, config);
  } catch (error) {
    return rejectWithValue(getApiErrorDetail(error, 'Failed to save system configuration'));
  }
});

const orgCustomizationSlice = createSlice({
  name: 'orgCustomization',
  initialState,
  reducers: {},
  extraReducers: (builder) =>
    builder
      .addCase(loadSystemConfig.pending, (state) => {
        state.loading = true;
      })
      .addCase(loadSystemConfig.fulfilled, (state, action) => {
        state.loading = false;
        state.systemConfigs[String(action.meta.arg)] = action.payload;
      })
      .addCase(loadSystemConfig.rejected, (state) => {
        state.loading = false;
      })
      .addCase(saveSystemConfig.pending, (state) => {
        state.saving = true;
      })
      .addCase(saveSystemConfig.fulfilled, (state, action) => {
        state.saving = false;
        state.systemConfigs[String(action.meta.arg.projectId)] = action.payload;
      })
      .addCase(saveSystemConfig.rejected, (state) => {
        state.saving = false;
      }),
});

export default orgCustomizationSlice.reducer;
