import { fetchSystemConfig, saveSystemConfig } from '@/lib/api/orgCustomizationApi';
import { makeStore } from '@/store';
import { getQueryErrorDetail } from '@/store/api/baseQuery';
import { orgCustomizationApi } from '@/store/modules/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchSystemConfig: jest.fn(), saveSystemConfig: jest.fn() }));

const fetchMock = fetchSystemConfig as jest.Mock;
const saveMock = saveSystemConfig as jest.Mock;
const config = (label: string) => ({ can_edit: true, navigation: { options: [{ label }] } });

describe('orgCustomizationApi', () => {
  afterEach(() => jest.resetAllMocks());

  it('loads the system config for a project', async () => {
    fetchMock.mockResolvedValueOnce(config('Tasks'));
    const store = makeStore();

    const result = await store.dispatch(orgCustomizationApi.endpoints.getSystemConfig.initiate(1));

    expect(fetchMock).toHaveBeenCalledWith(1);
    expect(result.data).toEqual(config('Tasks'));
  });

  it('puts the saved config into the cache without a refetch', async () => {
    fetchMock.mockResolvedValueOnce(config('Tasks'));
    saveMock.mockResolvedValueOnce(config('Jobs'));
    const store = makeStore();
    await store.dispatch(orgCustomizationApi.endpoints.getSystemConfig.initiate(1));

    const payload = { navigation: { options: [] } };
    await store.dispatch(orgCustomizationApi.endpoints.saveSystemConfig.initiate({ projectId: 1, config: payload as never }));

    expect(saveMock).toHaveBeenCalledWith(1, payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(orgCustomizationApi.endpoints.getSystemConfig.select(1)(store.getState()).data).toEqual(config('Jobs'));
  });

  it('keeps the cached config and returns the API error when a save fails', async () => {
    fetchMock.mockResolvedValueOnce(config('Tasks'));
    saveMock.mockRejectedValueOnce({ response: { status: 403, data: { detail: 'Organization admin or project owner access required.' } } });
    const store = makeStore();
    await store.dispatch(orgCustomizationApi.endpoints.getSystemConfig.initiate(1));

    const result = await store.dispatch(
      orgCustomizationApi.endpoints.saveSystemConfig.initiate({ projectId: 1, config: {} as never }),
    );

    expect('error' in result && getQueryErrorDetail(result.error, 'Failed')).toBe('Organization admin or project owner access required.');
    expect(orgCustomizationApi.endpoints.getSystemConfig.select(1)(store.getState()).data).toEqual(config('Tasks'));
  });
});
