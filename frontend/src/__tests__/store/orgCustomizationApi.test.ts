import api from '@/lib/api';
import { makeStore } from '@/store';
import { getQueryErrorDetail } from '@/store/baseQuery';
import { orgCustomizationApi } from '@/store/orgCustomizationApi';

jest.mock('@/lib/api', () => ({ __esModule: true, default: { request: jest.fn() } }));

const request = api.request as jest.Mock;
const URL = '/api/org-customization/system-config/';
const config = (label: string) => ({ can_edit: true, navigation: { options: [{ label }] } });

describe('orgCustomizationApi', () => {
  afterEach(() => request.mockReset());

  it('loads the system config for a project', async () => {
    request.mockResolvedValueOnce({ data: config('Tasks') });
    const store = makeStore();

    const result = await store.dispatch(orgCustomizationApi.endpoints.getSystemConfig.initiate(1));

    expect(request).toHaveBeenCalledWith({ url: URL, params: { project_id: 1 } });
    expect(result.data).toEqual(config('Tasks'));
  });

  it('puts the saved config into the cache without a refetch', async () => {
    request.mockResolvedValueOnce({ data: config('Tasks') }).mockResolvedValueOnce({ data: config('Jobs') });
    const store = makeStore();
    await store.dispatch(orgCustomizationApi.endpoints.getSystemConfig.initiate(1));

    const payload = { navigation: { options: [] } };
    await store.dispatch(orgCustomizationApi.endpoints.saveSystemConfig.initiate({ projectId: 1, config: payload as never }));

    expect(request).toHaveBeenLastCalledWith({ url: URL, method: 'PUT', data: payload, params: { project_id: 1 } });
    expect(request).toHaveBeenCalledTimes(2);
    expect(orgCustomizationApi.endpoints.getSystemConfig.select(1)(store.getState()).data).toEqual(config('Jobs'));
  });

  it('keeps the cached config and returns the API error when a save fails', async () => {
    request
      .mockResolvedValueOnce({ data: config('Tasks') })
      .mockRejectedValueOnce({ response: { status: 403, data: { detail: 'Organization admin or project owner access required.' } } });
    const store = makeStore();
    await store.dispatch(orgCustomizationApi.endpoints.getSystemConfig.initiate(1));

    const result = await store.dispatch(
      orgCustomizationApi.endpoints.saveSystemConfig.initiate({ projectId: 1, config: {} as never }),
    );

    expect('error' in result && getQueryErrorDetail(result.error, 'Failed')).toBe('Organization admin or project owner access required.');
    expect(orgCustomizationApi.endpoints.getSystemConfig.select(1)(store.getState()).data).toEqual(config('Tasks'));
  });
});
