import {
  clearSlackOAuthState,
  persistSlackOAuthState,
  readSlackOAuthState,
  slackApi,
  SLACK_OAUTH_STATE_STORAGE_KEY,
} from '@/lib/api/slackApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    patch: jest.fn(),
  },
}));

import api from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

describe('slack OAuth state helpers', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  it('persists, reads, and clears oauth state', () => {
    persistSlackOAuthState('abc');
    expect(sessionStorage.getItem(SLACK_OAUTH_STATE_STORAGE_KEY)).toBe('abc');
    expect(readSlackOAuthState()).toBe('abc');
    sessionStorage.removeItem(SLACK_OAUTH_STATE_STORAGE_KEY);
    expect(readSlackOAuthState()).toBe('abc');
    clearSlackOAuthState();
    expect(readSlackOAuthState()).toBeNull();
  });
});

describe('slackApi', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockApi.get.mockResolvedValue({ data: { url: 'u', state: 's' } } as any);
    mockApi.post.mockResolvedValue({ data: { success: true } } as any);
    mockApi.patch.mockResolvedValue({ data: { id: 1 } } as any);
  });

  it('covers oauth, status, preferences, and channels', async () => {
    await slackApi.initOAuth({ projectId: 1 });
    await slackApi.initOAuth({ organizationId: 2 });
    await slackApi.initOAuth();
    await slackApi.handleCallback('code', 'state');
    await slackApi.getStatus({ projectId: 1 });
    await slackApi.disconnect({ organizationId: 2 });
    await slackApi.getPreferences();
    await slackApi.updatePreference(1, { is_active: true }, { projectId: 1 });
    await slackApi.createPreference({ is_active: true });
    await slackApi.getChannels({ projectId: 3 });

    expect(mockApi.get).toHaveBeenCalled();
    expect(mockApi.post).toHaveBeenCalled();
    expect(mockApi.patch).toHaveBeenCalled();
  });
});
