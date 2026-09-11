import axios from 'axios';
import type { AxiosError, InternalAxiosRequestConfig } from 'axios';
import {
  LoginRequest,
  LoginResponse,
  RegisterRequest,
  RegisterResponse,
  User,
  AuthError,
  GoogleAuthResponse,
  SetPasswordRequest,
  ChangePasswordResponse,
} from '../types/auth';

const DEFAULT_API_BASE_URL = '';

const API_BASE_URL =
  (process.env.NEXT_PUBLIC_API_URL && process.env.NEXT_PUBLIC_API_URL.trim()) ||
  DEFAULT_API_BASE_URL;

// Backing state for getSharedRefreshedToken — do not read/write directly elsewhere.
let sharedRefreshPromise: Promise<string | null> | null = null;

/** Resolved API origin for browser and server; respects `NEXT_PUBLIC_API_URL` when set. */
export function resolveApiBaseUrl(): string {
  return API_BASE_URL;
}

// Create axios instance for API calls
// indexes: null => array params serialize as repeated keys (e.g. status=A&status=B)
// so Django QueryDict.getlist('status') works; default axios uses status[]=... which Django ignores.
const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/plain, */*',
  },
  paramsSerializer: {
    indexes: null,
  },
});

type RetriableRequestConfig = InternalAxiosRequestConfig & { _retry?: boolean };

const AUTH_STORAGE_KEY = 'auth-storage-v1';
/** Old key — read once for migration, never written. Removed by follow-up cleanup ticket. */
export const LEGACY_AUTH_STORAGE_KEY = 'auth-storage';
const AUTH_COOKIE_KEY = 'ms_auth';
const AUTH_COOKIE_MAX_AGE_SECONDS = 4 * 24 * 60 * 60;

type PersistedAuthState = {
  state?: {
    token?: string | null;
    refreshToken?: string | null;
    organizationAccessToken?: string | null;
    user?: User | null;
    isAuthenticated?: boolean;
    [key: string]: unknown;
  };
  version?: number;
};

function getCookieValue(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const encodedName = `${encodeURIComponent(name)}=`;
  const match = document.cookie
    .split('; ')
    .find((part) => part.startsWith(encodedName));
  return match ? decodeURIComponent(match.slice(encodedName.length)) : null;
}

function writeCookieValue(name: string, value: string, maxAgeSeconds = AUTH_COOKIE_MAX_AGE_SECONDS) {
  if (typeof document === 'undefined') return;
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}; Path=/; SameSite=Lax`;
}

function clearCookieValue(name: string) {
  if (typeof document === 'undefined') return;
  document.cookie = `${encodeURIComponent(name)}=; Max-Age=0; Path=/; SameSite=Lax`;
}

function canUseLocalStorage(): boolean {
  if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
    return false;
  }
  try {
    const testKey = '__marketing_simplified_storage_test__';
    window.localStorage.setItem(testKey, '1');
    window.localStorage.removeItem(testKey);
    return true;
  } catch {
    return false;
  }
}

function canUseSessionStorage(): boolean {
  if (typeof window === 'undefined' || typeof window.sessionStorage === 'undefined') {
    return false;
  }
  try {
    const testKey = '__marketing_simplified_session_storage_test__';
    window.sessionStorage.setItem(testKey, '1');
    window.sessionStorage.removeItem(testKey);
    return true;
  } catch {
    return false;
  }
}

function readAuthSessionStorage(): PersistedAuthState | null {
  if (!canUseSessionStorage()) return null;
  try {
    const raw = window.sessionStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (error) {
    console.warn('Failed to read auth session storage:', error);
    return null;
  }
}

function writeAuthSessionStorage(authData: PersistedAuthState) {
  if (!canUseSessionStorage()) return;
  try {
    window.sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(authData));
  } catch (error) {
    console.warn('Failed to persist auth session storage:', error);
  }
}

function clearAuthSessionStorage() {
  if (!canUseSessionStorage()) return;
  try {
    window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
  } catch (error) {
    console.warn('Failed to clear auth session storage:', error);
  }
}

function readAuthCookie(): PersistedAuthState | null {
  const raw = getCookieValue(AUTH_COOKIE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (error) {
    console.warn('Failed to parse auth cookie:', error);
    return null;
  }
}

function writeAuthCookie(authData: PersistedAuthState) {
  const state = authData.state;
  if (!state?.token && !state?.refreshToken) {
    clearCookieValue(AUTH_COOKIE_KEY);
    return;
  }
  writeCookieValue(
    AUTH_COOKIE_KEY,
    JSON.stringify({
      state: {
        token: state.token ?? null,
        refreshToken: state.refreshToken ?? null,
        organizationAccessToken: state.organizationAccessToken ?? null,
      },
      version: authData.version ?? 0,
    }),
  );
}

export function readPersistedAuthState() {
  if (typeof window === 'undefined') return null;
  if (canUseLocalStorage()) {
    try {
      const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
      if (raw) {
        return JSON.parse(raw);
      }
    } catch (error) {
      console.warn('Failed to read auth storage:', error);
    }
  }
  const sessionAuth = readAuthSessionStorage();
  if (sessionAuth) return sessionAuth;
  return readAuthCookie();
}

export function persistAuthTokens(tokens: {
  token?: string | null;
  refreshToken?: string | null;
  organizationAccessToken?: string | null;
  user?: User | null;
}) {
  const authData: PersistedAuthState = readPersistedAuthState() ?? { state: {}, version: 0 };
  authData.state = {
    ...(authData.state ?? {}),
    token: tokens.token ?? authData.state?.token ?? null,
    refreshToken: tokens.refreshToken ?? authData.state?.refreshToken ?? null,
    organizationAccessToken:
      tokens.organizationAccessToken ?? authData.state?.organizationAccessToken ?? null,
    user: tokens.user !== undefined ? tokens.user : authData.state?.user ?? null,
    isAuthenticated: Boolean(
      (tokens.token ?? authData.state?.token) &&
        (tokens.user !== undefined ? tokens.user : authData.state?.user),
    ),
  };
  if (canUseLocalStorage()) {
    try {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(authData));
    } catch (error) {
      console.warn('Failed to persist auth storage:', error);
    }
  }
  writeAuthSessionStorage(authData);
  writeAuthCookie(authData);
}

export function clearPersistedAuthState() {
  if (canUseLocalStorage()) {
    try {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
    } catch (error) {
      console.warn('Failed to clear auth storage:', error);
    }
  }
  clearAuthSessionStorage();
  clearCookieValue(AUTH_COOKIE_KEY);
}

export const authPersistStorage = {
  getItem: (name: string): string | null => {
    if (canUseLocalStorage()) {
      try {
        const value = window.localStorage.getItem(name);
        if (value) return value;
      } catch (error) {
        console.warn('Failed to read persisted auth item:', error);
      }
    }
    if (canUseSessionStorage()) {
      try {
        const value = window.sessionStorage.getItem(name);
        if (value) return value;
      } catch (error) {
        console.warn('Failed to read persisted auth session item:', error);
      }
    }
    return name === AUTH_STORAGE_KEY ? getCookieValue(AUTH_COOKIE_KEY) : null;
  },
  setItem: (name: string, value: string): void => {
    if (canUseLocalStorage()) {
      try {
        window.localStorage.setItem(name, value);
      } catch (error) {
        console.warn('Failed to write persisted auth item:', error);
      }
    }
    if (canUseSessionStorage()) {
      try {
        window.sessionStorage.setItem(name, value);
      } catch (error) {
        console.warn('Failed to write persisted auth session item:', error);
      }
    }
    // Always write cookie (not just as localStorage fallback) so SSR requests
    // have an up-to-date token without needing a separate persistAuthTokens call.
    if (name !== AUTH_STORAGE_KEY) return;
    try {
      writeAuthCookie(JSON.parse(value));
    } catch (error) {
      console.warn('Failed to persist auth cookie:', error);
    }
  },
  removeItem: (name: string): void => {
    if (canUseLocalStorage()) {
      try {
        window.localStorage.removeItem(name);
      } catch (error) {
        console.warn('Failed to remove persisted auth item:', error);
      }
    }
    if (canUseSessionStorage()) {
      try {
        window.sessionStorage.removeItem(name);
      } catch (error) {
        console.warn('Failed to remove persisted auth session item:', error);
      }
    }
    if (name === AUTH_STORAGE_KEY) {
      clearCookieValue(AUTH_COOKIE_KEY);
    }
  },
};

export function updatePersistedAccessToken(accessToken: string, refreshToken?: string) {
  const authData: PersistedAuthState = readPersistedAuthState() ?? { state: {}, version: 0 };
  authData.state = authData.state ?? {};
  authData.state.token = accessToken;
  if (refreshToken) {
    authData.state.refreshToken = refreshToken;
  }
  if (canUseLocalStorage()) {
    try {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(authData));
    } catch (error) {
      console.warn('Failed to update persisted auth token:', error);
    }
  }
  writeAuthSessionStorage(authData);
  writeAuthCookie(authData);
}

export async function refreshAccessToken(refreshToken: string): Promise<string | null> {
  try {
    const response = await axios.post(
      `${API_BASE_URL}/auth/token/refresh/`,
      { refresh: refreshToken },
      {
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json, text/plain, */*',
        },
      },
    );
    const accessToken = response.data?.access || response.data?.token;
    if (!accessToken) return null;
    updatePersistedAccessToken(accessToken, response.data?.refresh);
    return accessToken;
  } catch (error) {
    console.warn('Failed to refresh auth token:', error);
    return null;
  }
}

// Shared across every caller (this file's interceptor, other axios instances
// like preferencesApi.ts) so concurrent 401s trigger exactly one refresh call.
export function getSharedRefreshedToken(refreshToken: string): Promise<string | null> {
  if (!sharedRefreshPromise) {
    sharedRefreshPromise = refreshAccessToken(refreshToken);
  }
  return sharedRefreshPromise.finally(() => {
    sharedRefreshPromise = null;
  });
}

// Prevent duplicate banners when the interceptor fires more than once for the
// same eviction event (original request + one retry).
let _sessionEvictedPending = false;

function notifySessionEvicted() {
  if (_sessionEvictedPending) return;
  _sessionEvictedPending = true;

  try { localStorage.removeItem(AUTH_STORAGE_KEY); } catch (_) {}
  clearAuthSessionStorage();
  clearCookieValue(AUTH_COOKIE_KEY);
  try { sessionStorage.setItem('session_evicted', '1'); } catch (_) {}

  window.location.href = '/login';
}

// Request interceptor to add auth token to requests
api.interceptors.request.use(
  (config) => {
    let parsedToken = null;
    let userData = null;
    let organizationToken = null;
    const authData = readPersistedAuthState();
    parsedToken = authData?.state?.token;
    userData = authData?.state?.user;
    organizationToken = authData?.state?.organizationAccessToken;
    
    if (parsedToken) {
      config.headers.Authorization = `Bearer ${parsedToken}`;
    }
    
    // Add organization access token if available
    if (organizationToken) {
      config.headers['X-Organization-Token'] = organizationToken;
    }
    
    // Add user role header if available
    if (userData && userData.roles && userData.roles.length > 0) {
      // Use the first role as the primary role
      config.headers['x-user-role'] = userData.roles[0];
    }
    
    // Add team ID header if user has a team
    // Note: This is a placeholder - you may need to get team info from user data
    // For now, we'll set it to null or get it from user data if available
    if (userData && userData.team_id) {
      config.headers['x-team-id'] = userData.team_id.toString();
    }
    
    // Allow multipart/form-data to set its own Content-Type with boundary
    if (config.data instanceof FormData) {
      // axios will set the correct Content-Type when data is FormData
      delete (config.headers as any)['Content-Type'];
    } else {
      (config.headers as any)['Content-Type'] = 'application/json';
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor to handle auth errors globally
api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const status = error.response?.status;
    const config = error.config as RetriableRequestConfig | undefined;
    const url = config?.url;
    const responseData = error.response?.data as any;

    const isGoogleDocsUrl = typeof url === 'string' && url.startsWith('/api/google-docs/');
    const googleErrorMessage = responseData?.error;
    const googleErrorCode = responseData?.error_code;
    const isGoogleIntegrationTokenError =
      googleErrorCode === 'google_token_expired' ||
      googleErrorCode === 'google_unauthorized' ||
      (typeof googleErrorMessage === 'string' && googleErrorMessage.toLowerCase().includes('google session expired'));
    const shouldBypassGlobalLogout = isGoogleDocsUrl && isGoogleIntegrationTokenError;

    const isAuthEndpoint =
      url === '/auth/login/' ||
      url === '/auth/token/refresh/' ||
      url === '/auth/logout/';

    if (status === 401 && !isAuthEndpoint && !shouldBypassGlobalLogout) {
      if (typeof window !== 'undefined' && config && !config._retry) {
        const authData = readPersistedAuthState();
        const refreshToken = authData?.state?.refreshToken;
        const accessToken = refreshToken ? await getSharedRefreshedToken(refreshToken) : null;

        // Mark the request as retried so we don't end up in an infinite loop.
        config._retry = true;
        if (accessToken) {
          config.headers.Authorization = `Bearer ${accessToken}`;
          return api(config);
        }
      }

      // Session was explicitly revoked or exceeded concurrent limit.
      // Show a top banner on the current page, then redirect to login.
      // Return a never-resolving promise so the component's catch block never
      // fires — this keeps the current page visible while the toast is shown.
      if (typeof window !== 'undefined' && responseData?.code === 'session_evicted') {
        notifySessionEvicted();
        return new Promise(() => {});
      }
    }

    const quotaCode = responseData?.code;
    if (
      typeof window !== 'undefined' &&
      status === 403 &&
      responseData?.errorCode === 'PASSWORD_ROTATION_REQUIRED'
    ) {
      const currentPath = window.location.pathname;
      if (!currentPath.startsWith('/set-password')) {
        window.location.href = '/set-password?rotation=1';
      }
    }

    if (
      typeof window !== 'undefined' &&
      (status === 402 || status === 409 || status === 422) &&
      (quotaCode === 'TOKEN_QUOTA_EXCEEDED' ||
        quotaCode === 'SINGLE_CALL_TOO_LARGE' ||
        quotaCode === 'PROJECT_HAS_NO_ORG')
    ) {
      window.dispatchEvent(new CustomEvent('quota:error', { detail: { code: quotaCode } }));
    }

    return Promise.reject(error);
  }
);

// Authentication API functions - connected to Django backend
export const authAPI = {
  login: async (credentials: LoginRequest): Promise<LoginResponse> => {
    const response = await api.post('/auth/login/', credentials);
    return response.data;
  },
  
  register: async (userData: RegisterRequest): Promise<RegisterResponse> => {
    const response = await api.post('/auth/register/', userData);
    return response.data;
  },
  
  // Email verification endpoint
  verifyEmail: async (token: string): Promise<{ message: string }> => {
    const response = await api.get(`/auth/verify/?token=${token}`);
    return response.data;
  },
  
  getCurrentUser: async (): Promise<User> => {
    const response = await api.get('/auth/me/');
    return response.data;
  },
  
  logout: async (refreshToken?: string | null): Promise<{ message: string }> => {
    const response = await api.post('/auth/logout/', refreshToken ? { refresh_token: refreshToken } : {});
    return response.data;
  },
  
  // Google OAuth endpoints
  googleSetPassword: async (data: SetPasswordRequest): Promise<GoogleAuthResponse> => {
    const response = await api.post('/auth/google/set-password/', data);
    return response.data;
  },

  refreshOrganizationToken: async (): Promise<{ organization_access_token: string }> => {
    const response = await api.post('/auth/organization-token/refresh/');
    return response.data;
  },

  refreshToken: async (refreshToken: string): Promise<string | null> => {
    return refreshAccessToken(refreshToken);
  },

  // Profile update endpoint (handles both JSON and FormData for avatar uploads)
  updateProfile: async (profileData: { username?: string; first_name?: string; last_name?: string; job?: string; department?: string; location?: string } | FormData): Promise<User> => {
    const config = profileData instanceof FormData
      ? { headers: { 'Content-Type': 'multipart/form-data' } }
      : {};
    const response = await api.patch('/auth/me/', profileData, config);
    return response.data;
  },

  getMyProjects: async (): Promise<{ project_id: number; project_name: string; role: string }[]> => {
    const response = await api.get('/auth/me/projects/');
    return response.data;
  },

  // Password reset endpoints
  forgotPassword: async(email: string):Promise<{ message:string }> =>{
    const response = await api.post('/auth/forgot-password/', { email });
    return response.data;
  },

  resetPassword: async(token: string, new_password: string):Promise<{ message:string }> =>{
    const response = await api.post('/auth/reset-password/', { token, new_password });
    return response.data;
  },

  changePassword: async(current_password: string, new_password: string): Promise<ChangePasswordResponse> => {
    const response = await api.post('/auth/change-password/', { current_password, new_password });
    return response.data;
  },

  deleteAccount: async (refreshToken: string): Promise<{ message: string }> => {
    const response = await api.delete('/auth/me/delete/', {
      data: { confirm: 'DELETE MY ACCOUNT', refresh_token: refreshToken },
    });
    return response.data;
  },

  getSessions: async (): Promise<{ jti: string; ip: string; user_agent: string; created_at: string }[]> => {
    const response = await api.get('/auth/sessions/');
    return response.data;
  },

  revokeSession: async (jti: string): Promise<{ message: string }> => {
    const response = await api.delete(`/auth/sessions/${jti}/`);
    return response.data;
  },
};

export type CreateDecisionFromMeetingPayload = {
  title?: string;
  contextSummary?: string;
  context_summary?: string;
};

export type DecisionOriginResponse = {
  decisionId: number;
  meeting: {
    id: number;
    title: string;
  };
  originTimestamp: string;
  createdBy: number;
  creationContext: Record<string, unknown>;
};

export const decisionCaptureAPI = {
  createDecisionFromMeeting: async (
    projectId: number | string,
    meetingId: number | string,
    payload: CreateDecisionFromMeetingPayload
  ) => {
    const response = await api.post(
      `/api/projects/${projectId}/meetings/${meetingId}/decisions/`,
      payload
    );
    return response.data;
  },

  getMeetingDecisions: async (
    projectId: number | string,
    meetingId: number | string
  ) => {
    const response = await api.get(
      `/api/projects/${projectId}/meetings/${meetingId}/decisions/`
    );
    return response.data;
  },

  getDecisionOrigin: async (
    decisionId: number | string,
    projectId?: number | string
  ): Promise<DecisionOriginResponse> => {
    const response = await api.get(
      `/api/decisions/${decisionId}/origin/`,
      {
        params: projectId ? { project_id: projectId } : undefined,
      }
    );
    return response.data;
  },
};


export default api;
