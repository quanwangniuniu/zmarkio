import { fetchSystemConfig, saveSystemConfig as putSystemConfig } from '@/lib/api/orgCustomizationApi';
import { makeStore } from '@/store';
import {
  loadSystemConfig,
  saveSystemConfig,
  selectSystemConfig,
  selectSystemConfigSaving,
} from '@/store/modules/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchSystemConfig: jest.fn(), saveSystemConfig: jest.fn() }));

const fetchMock = fetchSystemConfig as jest.Mock;
const putMock = putSystemConfig as jest.Mock;
const config = (label: string) => ({ can_edit: true, navigation: { options: [{ label }] } });

describe('orgCustomization store module', () => {
  afterEach(() => jest.resetAllMocks());

  it('loads the system config for a project', async () => {
    fetchMock.mockResolvedValueOnce(config('Tasks'));
    const store = makeStore();

    await store.dispatch(loadSystemConfig(1));

    expect(fetchMock).toHaveBeenCalledWith(1);
    expect(selectSystemConfig(store.getState(), 1)).toEqual(config('Tasks'));
    expect(selectSystemConfig(store.getState(), 2)).toBeUndefined();
  });

  it('stores the saved config without loading it again', async () => {
    fetchMock.mockResolvedValueOnce(config('Tasks'));
    putMock.mockResolvedValueOnce(config('Jobs'));
    const store = makeStore();
    await store.dispatch(loadSystemConfig(1));

    const payload = { navigation: { options: [] } };
    const pending = store.dispatch(saveSystemConfig({ projectId: 1, config: payload as never }));
    expect(selectSystemConfigSaving(store.getState())).toBe(true);
    await pending;

    expect(putMock).toHaveBeenCalledWith(1, payload);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(selectSystemConfigSaving(store.getState())).toBe(false);
    expect(selectSystemConfig(store.getState(), 1)).toEqual(config('Jobs'));
  });

  it('keeps the stored config and returns the API message when a save fails', async () => {
    fetchMock.mockResolvedValueOnce(config('Tasks'));
    putMock.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 403, data: { detail: 'Organization admin or project owner access required.' } },
    });
    const store = makeStore();
    await store.dispatch(loadSystemConfig(1));

    await expect(store.dispatch(saveSystemConfig({ projectId: 1, config: {} as never })).unwrap()).rejects.toBe(
      'Organization admin or project owner access required.',
    );
    expect(selectSystemConfig(store.getState(), 1)).toEqual(config('Tasks'));
    expect(selectSystemConfigSaving(store.getState())).toBe(false);
  });
});
