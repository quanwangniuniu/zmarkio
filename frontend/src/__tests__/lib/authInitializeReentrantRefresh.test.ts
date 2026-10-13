import { authAPI, persistAuthTokens, clearPersistedAuthState } from '@/lib/api';
import TeamAPI from '@/lib/api/teamApi';
import { useAuthStore } from '@/lib/authStore';

describe('initializeAuth re-entrant refresh guard', () => {
  const initialState = useAuthStore.getState();

  beforeEach(() => {
    // persist middleware writes the store's current state to the same
    // localStorage key persistAuthTokens uses, so reset the store FIRST —
    // otherwise this setState would fire after persistAuthTokens and wipe
    // the seeded session back to null.
    useAuthStore.setState({
      ...initialState,
      token: null,
      user: null,
      initialized: false,
    });
    persistAuthTokens({
      token: 'old-access-token',
      user: { id: 1, roles: [] } as any,
    });
  });

  afterEach(() => {
    clearPersistedAuthState();
    jest.restoreAllMocks();
  });

  // Scenario: initializeAuth() is invoked twice concurrently (e.g. two
  // components mounting at the same time). Without a guard, each call
  // independently refreshes the token. This proves both calls share one
  // in-flight refresh instead of firing two.
  it('two concurrent initializeAuth() calls trigger exactly one refresh call', async () => {
    const refreshSpy = jest
      .spyOn(authAPI, 'refreshToken')
      .mockResolvedValue('new-access-token');
    jest
      .spyOn(authAPI, 'getCurrentUser')
      .mockResolvedValue({ id: 1, roles: [] } as any);
    jest
      .spyOn(TeamAPI, 'getUserTeams')
      .mockResolvedValue({ team_ids: [] } as any);

    await Promise.all([
      useAuthStore.getState().initializeAuth(),
      useAuthStore.getState().initializeAuth(),
    ]);

    expect(refreshSpy).toHaveBeenCalledTimes(1);
  });
});
