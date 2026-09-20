import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import {
  authPersistStorage,
  authAPI,
  clearPersistedAuthState,
  readPersistedAuthState,
  LEGACY_AUTH_STORAGE_KEY,
  SESSION_ENDED_EVENT,
} from './api';
import { User } from '../types/auth';
import TeamAPI from './api/teamApi';
import { LOGIN_ERROR_MESSAGES, isNetworkError, isRetryableAuthError } from './authMessages';
import { useChatStore } from './chatStore';

// Authentication state interface
interface AuthState {
  // User data and authentication state
  user: User | null;
  token: string | null;
  refreshToken: string | null;
  organizationAccessToken: string | null;
  isAuthenticated: boolean;
  loading: boolean;
  initialized: boolean;

  // Team information
  userTeams: number[];
  selectedTeamId: number | null;
  hasHydrated: boolean;

  // Actions
  setUser: (user: User | null) => void;
  setToken: (token: string | null) => void;
  setRefreshToken: (refreshToken: string | null) => void;
  setOrganizationAccessToken: (token: string | null) => void;
  setLoading: (loading: boolean) => void;
  setInitialized: (initialized: boolean) => void;
  setUserTeams: (teams: number[]) => void;
  setSelectedTeamId: (teamId: number | null) => void;
  setHasHydrated: (hasHydrated: boolean) => void;
  
  // Authentication actions
  login: (email: string, password: string) => Promise<{
    success: boolean;
    error?: string;
    statusCode?: number;
    errorCode?: string;
    retry_after_seconds?: number;
    requires_captcha?: boolean;
    lockout_until?: string;
  }>;
  logout: () => Promise<void>;
  getCurrentUser: () => Promise<{ success: boolean; error?: string; retryable?: boolean }>;
  getUserTeams: () => Promise<{ success: boolean; error?: string }>;
  refreshOrganizationAccessToken: () => Promise<{ success: boolean; error?: string }>;
  
  // Initialize auth state on app startup
  initializeAuth: () => Promise<void>;
  
  // Clear all auth data
  clearAuth: () => void;
}

// Shared across all initializeAuth() calls (e.g. concurrent mounts) so they
// don't each fire their own /auth/token/refresh/ request.
let sharedInitAuthRefreshPromise: Promise<string | null> | null = null;

function getSharedInitAuthRefreshedToken(refreshToken: string): Promise<string | null> {
  if (!sharedInitAuthRefreshPromise) {
    sharedInitAuthRefreshPromise = authAPI.refreshToken(refreshToken);
  }
  return sharedInitAuthRefreshPromise.finally(() => {
    sharedInitAuthRefreshPromise = null;
  });
}

// Create the auth store with persistence
export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      // Initial state
      user: null,
      token: null,
      refreshToken: null,
      organizationAccessToken: null,
      isAuthenticated: false,
      loading: false,
      initialized: false,
      userTeams: [],
      selectedTeamId: null,
      hasHydrated: false,

      // State setters
      setUser: (user) => set({ user, isAuthenticated: !!user }),
      setToken: (token) => set({ token }),
      setRefreshToken: (refreshToken) => set({ refreshToken }),
      setOrganizationAccessToken: (organizationAccessToken) => set({ organizationAccessToken }),
      setLoading: (loading) => set({ loading }),
      setInitialized: (initialized) => set({ initialized }),
      setUserTeams: (userTeams) => set({ userTeams }),
      setSelectedTeamId: (selectedTeamId) => set({ selectedTeamId }),
      setHasHydrated: (hasHydrated) => set({ hasHydrated }),

      // Login action
      login: async (email: string, password: string) => {
        set({ loading: true });
        try {
          const response = await authAPI.login({ email, password });
          const { token, refresh, user, organization_access_token } = response;

          // Persist auth data immediately so downstream requests include the token
          set({
            user,
            token,
            refreshToken: refresh,
            organizationAccessToken: organization_access_token || null,
            isAuthenticated: true,
          });

          // Get user teams after successful login
          let userTeams: number[] = [];
          let selectedTeamId: number | null = null;

          try {
            const teamsResponse = await TeamAPI.getUserTeams();
            userTeams = teamsResponse.team_ids || [];
            // Select the first team by default, or null if no teams
            selectedTeamId = userTeams.length > 0 ? userTeams[0] : null;
          } catch (teamError) {
            console.warn('Failed to fetch user teams:', teamError);
            // Continue with login even if team fetch fails
          }

          set({
            userTeams,
            selectedTeamId,
            loading: false,
          });

          // Refresh user data to get latest avatar and profile info
          try {
            await get().getCurrentUser();
          } catch (error) {
            console.warn('Failed to refresh user data after login:', error);
            // Don't fail login if refresh fails
          }

          return { success: true };
        } catch (error: any) {
          set({ loading: false });

          // Network/connection failure – show network message only
          if (isNetworkError(error)) {
            return {
              success: false,
              error: LOGIN_ERROR_MESSAGES.NETWORK,
              statusCode: undefined,
              errorCode: 'NETWORK_ERROR',
            };
          }

          const statusCode = error?.response?.status;
          const errorData = error?.response?.data;
          const errorCode = errorData?.errorCode;
          const backendMessage = errorData?.error;
          const retryAfterSeconds = errorData?.retry_after_seconds;
          const requiresCaptcha = errorData?.requires_captcha;
          const lockoutUntil = errorData?.lockout_until;

          let message: string = LOGIN_ERROR_MESSAGES.GENERIC;

          if (statusCode === 401) {
            message = LOGIN_ERROR_MESSAGES.INVALID_PASSWORD;
          } else if (statusCode === 429) {
            if (errorCode === 'LOGIN_LOCKED') {
              message = backendMessage || LOGIN_ERROR_MESSAGES.LOGIN_LOCKED;
            } else if (errorCode === 'TOO_MANY_ATTEMPTS') {
              message = backendMessage || LOGIN_ERROR_MESSAGES.TOO_MANY_ATTEMPTS;
            } else {
              message = backendMessage || LOGIN_ERROR_MESSAGES.TOO_MANY_ATTEMPTS;
            }
          } else if (statusCode === 403) {
            if (errorCode === 'EMAIL_NOT_VERIFIED' || backendMessage?.toLowerCase().includes('not verified')) {
              message = LOGIN_ERROR_MESSAGES.EMAIL_NOT_VERIFIED;
            } else if (errorCode === 'PASSWORD_NOT_SET' || backendMessage?.toLowerCase().includes('password not set')) {
              message = LOGIN_ERROR_MESSAGES.PASSWORD_NOT_SET;
            }
          } else if (statusCode === 400) {
            message = backendMessage || LOGIN_ERROR_MESSAGES.VALIDATION;
          } else if (statusCode === 404) {
            message = LOGIN_ERROR_MESSAGES.EMAIL_NOT_REGISTERED;
          }

          return {
            success: false,
            error: message,
            statusCode,
            errorCode,
            retry_after_seconds: retryAfterSeconds,
            requires_captcha: requiresCaptcha,
            lockout_until: lockoutUntil,
          };
        }
      },

      // Logout action
      logout: async () => {
        try {
          // Try to call logout API (optional)
          await authAPI.logout(get().refreshToken);
        } catch (error) {
          // Ignore logout API errors
          console.warn('Logout API call failed:', error);
        }
        
        // Clear all auth data
        get().clearAuth();
      },

      // Get current user from API
      getCurrentUser: async () => {
        try {
          const user = await authAPI.getCurrentUser();
          let persistedToken = get().token;
          let persistedRefreshToken = get().refreshToken;
          let persistedOrganizationToken = get().organizationAccessToken;
          const authData = readPersistedAuthState();
          persistedToken = authData?.state?.token ?? persistedToken;
          persistedRefreshToken = authData?.state?.refreshToken ?? persistedRefreshToken;
          persistedOrganizationToken =
            authData?.state?.organizationAccessToken ?? persistedOrganizationToken;
          set({
            user,
            token: persistedToken,
            refreshToken: persistedRefreshToken,
            organizationAccessToken: persistedOrganizationToken,
            isAuthenticated: true,
          });
          return { success: true };
        } catch (error: any) {
          return {
            success: false,
            error: 'Failed to get user info',
            retryable: isRetryableAuthError(error),
          };
        }
      },

      // Get user teams from API
      getUserTeams: async () => {
        try {
          const teamsResponse = await TeamAPI.getUserTeams();
          const userTeams = teamsResponse.team_ids || [];
          
          set({ userTeams });
          
          // If no team is currently selected and user has teams, select the first one
          const { selectedTeamId } = get();
          if (!selectedTeamId && userTeams.length > 0) {
            set({ selectedTeamId: userTeams[0] });
          }
          
          return { success: true };
        } catch (error: any) {
          console.error('Failed to fetch user teams:', error);
          return { success: false, error: 'Failed to get user teams' };
        }
      },

      // Refresh organization access token
      refreshOrganizationAccessToken: async () => {
        try {
          const response = await authAPI.refreshOrganizationToken();
          const token = response.organization_access_token || null;
          set({ organizationAccessToken: token });
          return { success: true };
        } catch (error: any) {
          const message =
            error?.response?.data?.error ||
            error?.response?.data?.detail ||
            error?.message ||
            'Failed to refresh organization token';
          return { success: false, error: message };
        }
      },
      

      // Initialize authentication state on app startup
      initializeAuth: async () => {
        let { token, refreshToken, user: persistedUser } = get();
        const persistedAuth = readPersistedAuthState();
        token = token ?? persistedAuth?.state?.token ?? null;
        refreshToken = refreshToken ?? persistedAuth?.state?.refreshToken ?? null;
        persistedUser = persistedUser ?? persistedAuth?.state?.user ?? null;
        const organizationAccessToken =
          get().organizationAccessToken ??
          persistedAuth?.state?.organizationAccessToken ??
          null;
        if (token || refreshToken || organizationAccessToken || persistedUser) {
          set({
            token,
            refreshToken,
            organizationAccessToken,
            user: persistedUser,
            isAuthenticated: Boolean(token && persistedUser),
          });
        }

        if (!token && !refreshToken) {
          set({ initialized: true });
          return;
        }

        set({ loading: true });
 
        try {
          if (refreshToken) {
            const refreshedToken = await getSharedInitAuthRefreshedToken(refreshToken);
            if (refreshedToken) {
              token = refreshedToken;
              set({
                token: refreshedToken,
                isAuthenticated: Boolean(refreshedToken && (get().user || persistedUser)),
              });
            }
          }

          if (!token) {
            return;
          }

          // Validate token by calling /auth/me
          let userResult = await get().getCurrentUser();

          if (!userResult.success && refreshToken && !userResult.retryable) {
            const refreshedToken = await getSharedInitAuthRefreshedToken(refreshToken);
            if (refreshedToken) {
              token = refreshedToken;
              set({ token: refreshedToken });
              userResult = await get().getCurrentUser();
            }
          }

          if (!userResult.success) {
            if (userResult.retryable) {
              // Backend unavailable — keep persisted session instead of forcing re-login.
              set({
                isAuthenticated: Boolean(token && (get().user || persistedUser)),
              });
              return;
            }
            get().clearAuth();
            return;
          }

          await get().getUserTeams();
        } catch (error) {
          console.error('Auth initialization failed:', error);
          if (isRetryableAuthError(error)) {
            set({
              isAuthenticated: Boolean(token && (get().user || persistedUser)),
            });
          } else {
            get().clearAuth();
          }
        } finally {
          set({ loading: false, initialized: true });
        }
      },

      // Clear all authentication data
      clearAuth: () => {
        // Dismiss any open chat/widget so mounted ChatWindow components unmount
        // before they can fire unauthenticated API calls.
        const chatStore = useChatStore.getState();
        chatStore.setCurrentChat(null);
        chatStore.setWidgetChat(null);
        // Reset all per-user in-memory chat state (unread counts, cached chats,
        // messages). Without this, the stale "localCount=0" values in unreadCounts
        // survive the logout and cause setChatsForProject to ignore the backend's
        // real unread_count on the next login — the root cause of badges and the
        // "New messages" divider not appearing without a page refresh.
        chatStore.clearUserState();
        clearPersistedAuthState();

        set({
          user: null,
          token: null,
          refreshToken: null,
          organizationAccessToken: null,
          isAuthenticated: false,
          loading: false,
          userTeams: [],
          selectedTeamId: null
        });
      }
    }),
    {
      name: 'auth-storage-v1',
      storage: createJSONStorage(() => authPersistStorage),
      onRehydrateStorage: () => (state) => {
        state?.setHasHydrated(true);
        // One-time migration from old key. Idempotent: skipped if new key already
        // has real data. Old key is left in place for multi-tab safety; the
        // follow-up cleanup ticket will remove it.
        if (typeof window === 'undefined') return;
        // Guard: skip if new key already has real auth data.
        // We cannot check key existence alone — Zustand writes the empty initial
        // state to the new key before onRehydrateStorage fires.
        try {
          const newRaw = window.localStorage.getItem('auth-storage-v1');
          if (newRaw) {
            const newParsed = JSON.parse(newRaw);
            if (newParsed?.state?.token || newParsed?.state?.user) return;
          }
        } catch { /* malformed new key — fall through to migration */ }
        try {
          const raw = window.localStorage.getItem(LEGACY_AUTH_STORAGE_KEY);
          if (!raw) return;
          const parsed = JSON.parse(raw);
          const old = parsed?.state;
          if (!old) return;
          useAuthStore.setState({
            token: old.token ?? null,
            refreshToken: old.refreshToken ?? null,
            organizationAccessToken: old.organizationAccessToken ?? null,
            user: old.user ?? null,
            isAuthenticated: old.isAuthenticated ?? false,
            userTeams: old.userTeams ?? [],
            selectedTeamId: old.selectedTeamId ?? null,
          });
        } catch (e) {
          console.warn('[authStore migration] Failed to migrate legacy persist key:', e);
        }
      },
      partialize: (state) => ({
        // Only persist these fields to localStorage
        token: state.token,
        refreshToken: state.refreshToken,
        organizationAccessToken: state.organizationAccessToken,
        user: state.user,
        isAuthenticated: !!state.token && !!state.user,
        userTeams: state.userTeams,
        selectedTeamId: state.selectedTeamId
      })
    }
  )
);

// A session ended by the API client (refresh token rejected) must also reset
// in-memory auth state, the same as a normal logout.
if (typeof window !== 'undefined') {
  window.addEventListener(SESSION_ENDED_EVENT, () => {
    useAuthStore.getState().clearAuth();
  });
}
