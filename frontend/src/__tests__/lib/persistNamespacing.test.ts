/**
 * Tests for Zustand persist key namespacing in authStore and projectStore.
 * Environment: jsdom (default). localStorage is provided by jsdom.
 */
import { useAuthStore } from '@/lib/authStore';
import { useProjectStore } from '@/lib/projectStore';

beforeEach(() => {
  localStorage.clear();
  // Reset both stores to initial state between tests
  useAuthStore.setState({
    user: null,
    token: null,
    organizationAccessToken: null,
    isAuthenticated: false,
    loading: false,
    initialized: false,
    userTeams: [],
    selectedTeamId: null,
    hasHydrated: false,
  });
  useProjectStore.setState({
    projects: [],
    activeProject: null,
    activeProjectIds: [],
    inactiveProjectIds: [],
    completedProjectIds: [],
    hasHydrated: false,
    loading: false,
    error: null,
  });
});

// ---------------------------------------------------------------------------
// 1. Keys are distinct — catches accidental future collision
// ---------------------------------------------------------------------------
it('authStore and projectStore use distinct persist keys', () => {
  const authKey = useAuthStore.persist.getOptions().name;
  const projectKey = useProjectStore.persist.getOptions().name;
  expect(authKey).toBe('auth-storage-v1');
  expect(projectKey).toBe('project-storage-v1');
  expect(authKey).not.toBe(projectKey);
});

// ---------------------------------------------------------------------------
// 2. Logout clears only the auth key, leaves projectStore untouched
// ---------------------------------------------------------------------------
it('clearAuth removes auth-storage-v1 and leaves project-storage-v1 intact', () => {
  // Seed both keys as if they were previously written by Zustand persist
  localStorage.setItem('auth-storage-v1', JSON.stringify({ state: { token: 'tok' }, version: 0 }));
  localStorage.setItem('project-storage-v1', JSON.stringify({ state: { activeProjectIds: [1, 2] }, version: 0 }));

  useAuthStore.getState().clearAuth();

  // Zustand re-writes the auth key with the cleared state after set() — the key
  // exists but must have null auth data, proving the clear was scoped to auth only.
  const authAfter = JSON.parse(localStorage.getItem('auth-storage-v1') ?? 'null');
  expect(authAfter?.state?.token).toBeNull();
  expect(authAfter?.state?.user).toBeNull();

  // Project key must be completely untouched
  expect(localStorage.getItem('project-storage-v1')).not.toBeNull();
  const project = JSON.parse(localStorage.getItem('project-storage-v1')!);
  expect(project.state.activeProjectIds).toEqual([1, 2]);
});

// ---------------------------------------------------------------------------
// 3. Migration: old-key data seeds new key on first load
// ---------------------------------------------------------------------------
it('authStore migrates data from legacy auth-storage key when new key is absent', async () => {
  const legacyData = {
    state: {
      token: 'legacy-tok',
      refreshToken: 'legacy-ref',
      organizationAccessToken: null,
      user: { id: 1, email: 'a@b.com' },
      isAuthenticated: true,
      userTeams: [10],
      selectedTeamId: 10,
    },
    version: 0,
  };
  localStorage.setItem('auth-storage', JSON.stringify(legacyData));
  // New key must be absent so migration triggers
  localStorage.removeItem('auth-storage-v1');

  // Trigger rehydration (simulates first page load after deploy)
  await useAuthStore.persist.rehydrate();

  const state = useAuthStore.getState();
  expect(state.token).toBe('legacy-tok');
  // The refresh token now lives only in the server's HttpOnly cookie.
  expect('refreshToken' in state).toBe(false);
  expect(state.userTeams).toEqual([10]);
});

// ---------------------------------------------------------------------------
// 4. Migration: malformed old-key data — no throw, store starts empty
// ---------------------------------------------------------------------------
it('authStore migration swallows malformed legacy data without crashing', async () => {
  localStorage.setItem('auth-storage', 'not valid json {{{{');
  localStorage.removeItem('auth-storage-v1');

  await expect(useAuthStore.persist.rehydrate()).resolves.not.toThrow();

  // Store should have empty/initial values
  expect(useAuthStore.getState().token).toBeNull();
  expect(useAuthStore.getState().user).toBeNull();
});

// ---------------------------------------------------------------------------
// 5. Cross-tab: StorageEvent for auth key does not affect projectStore state
// ---------------------------------------------------------------------------
it('storage event for auth-storage-v1 does not reset projectStore state', () => {
  useProjectStore.setState({ activeProjectIds: [42], activeProject: null });

  // Simulate Tab B logging out — it removes the auth key
  window.dispatchEvent(
    new StorageEvent('storage', {
      key: 'auth-storage-v1',
      oldValue: JSON.stringify({ state: { token: 'tok' }, version: 0 }),
      newValue: null,
      storageArea: localStorage,
    })
  );

  // projectStore state must be unaffected
  expect(useProjectStore.getState().activeProjectIds).toEqual([42]);
});

// ---------------------------------------------------------------------------
// 6. Migration idempotency: if new key already exists, old key is not read
// ---------------------------------------------------------------------------
it('migration is skipped when new key already exists', async () => {
  // New key has real data
  localStorage.setItem(
    'auth-storage-v1',
    JSON.stringify({ state: { token: 'current-tok', refreshToken: 'current-ref' }, version: 0 })
  );
  // Old key has different data — should NOT override
  localStorage.setItem(
    'auth-storage',
    JSON.stringify({ state: { token: 'stale-tok', refreshToken: 'stale-ref' }, version: 0 })
  );

  await useAuthStore.persist.rehydrate();

  expect(useAuthStore.getState().token).not.toBe('stale-tok');
});
