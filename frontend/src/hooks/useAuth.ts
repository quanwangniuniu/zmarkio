import { useAuthStore } from '../lib/authStore';
import { useRouter } from 'next/navigation';
import toast from 'react-hot-toast';
import { LoginRequest, RegisterRequest, RegisterResponse, ApiResponse } from '../types/auth';
import { authAPI } from '../lib/api';
import { LOGIN_ERROR_MESSAGES, isNetworkError } from '../lib/authMessages';
import { buildUrl } from '../lib/buildUrl';

// Enhanced useAuth hook that uses Zustand store for state management
export default function useAuth() {
  const {
    user,
    loading,
    isAuthenticated,
    login: storeLogin,
    logout: storeLogout,
    getCurrentUser: storeGetCurrentUser
  } = useAuthStore();
  
  const router = useRouter();

  // Login function with enhanced, normalized error handling
  const login = async (credentials: LoginRequest): Promise<ApiResponse<void>> => {
    // Dismiss any toasts left over from a previous logout so the
    // "Logged out successfully" banner doesn't linger into the new session.
    toast.dismiss();
    try {
      const result = await storeLogin(credentials.email, credentials.password);

      if (result.success) {
        router.push(buildUrl('/overview'));
        return { success: true };
      }

      // Normalize store-level errors into the shared ApiResponse shape
      return {
        success: false,
        error: result.error,
        statusCode: result.statusCode,
        errorCode: result.errorCode,
        retry_after_seconds: result.retry_after_seconds,
        requires_captcha: result.requires_captcha,
        lockout_until: result.lockout_until,
      };
    } catch (error: any) {
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
  };

  const register = async (userData: RegisterRequest): Promise<ApiResponse<RegisterResponse>> => {
    try {
      await authAPI.register(userData);

      // Auto-login via the store's login action (same path as manual login)
      const loginResult = await storeLogin(userData.email, userData.password);

      if (loginResult.success) {
        router.push(buildUrl('/overview'));
        return { success: true };
      }

      return { success: false, error: loginResult.error };
    } catch (error: any) {
      let message = 'Registration failed';
      const passwordError = error.response?.status === 400 &&
        error.response?.data?.error === 'Password validation failed';

      if (error.response?.status === 400) {
        const errorMsg = error.response.data?.error || '';

        if (passwordError) {
          const details = error.response.data.details;
          message = Array.isArray(details) && details.length > 0
            ? details.join(' ')
            : errorMsg;
        } else if (errorMsg.includes('Missing fields')) {
          message = 'Please fill in all required fields (username, email, and password)';
        } else if (errorMsg.includes('Password too short')) {
          message = 'Password must be at least 8 characters long';
        } else if (errorMsg.includes('Email already registered')) {
          message = 'An account with this email already exists';
        } else if (errorMsg.includes('Organization not found')) {
          message = 'Invalid organization ID. Please check and try again.';
        } else {
          message = errorMsg || 'Invalid input data';
        }
      } else {
        message = error.response?.data?.error || 'Registration failed';
      }

      toast.error(message);
      return {
        success: false,
        error: message,
        ...(passwordError ? { fieldErrors: { password: message } } : {}),
      };
    }
  };

  // Get current user function
  const getCurrentUser = async (): Promise<ApiResponse<any>> => {
    try {
      const result = await storeGetCurrentUser();
      return { success: result.success, data: user, error: result.error };
    } catch (error: any) {
      const message = error.response?.data?.error || 'Failed to get user info';
      return { success: false, error: message };
    }
  };

  // Email verification function (unchanged from original)
  const verifyEmail = async (token: string): Promise<ApiResponse<void>> => {
    try {
      const response = await authAPI.verifyEmail(token);
      toast.success(response.message || 'Email verified successfully!');
      return { success: true };
    } catch (error: any) {
      let message = 'Email verification failed';
      
      if (error.response?.status === 400) {
        message = error.response.data?.error || 'Invalid verification token';
      } else {
        message = error.response?.data?.error || 'Email verification failed';
      }
      
      toast.error(message);
      return { success: false, error: message };
    }
  };

  // Logout function
  const logout = async (): Promise<ApiResponse<void>> => {
    try {
      await storeLogout();
      toast.success('Logged out successfully');
      router.push('/login');
      return { success: true };
    } catch (error: any) {
      const message = 'Logout failed';
      toast.error(message);
      return { success: false, error: message };
    }
  };

  // Check if user has specific roles
  const hasRole = (roles: string[]): boolean => {
    if (!user || !user.roles) return false;
    return roles.some(role => user.roles.includes(role));
  };

  // Check if user has any of the specified roles
  const hasAnyRole = (roles: string[]): boolean => {
    if (!user || !user.roles) return false;
    return user.roles.some(role => roles.includes(role));
  };

  // Refresh user data (useful after profile updates)
  const refreshUser = async (): Promise<ApiResponse<void>> => {
    try {
      const result = await storeGetCurrentUser();
      if (result.success) {
        return { success: true };
      }
      return { success: false, error: result.error };
    } catch (error: any) {
      const message = error.response?.data?.error || 'Failed to refresh user data';
      return { success: false, error: message };
    }
  };

  return {
    user,
    loading,
    isAuthenticated,
    login,
    register,
    verifyEmail,
    logout,
    getCurrentUser,
    refreshUser,
    hasRole,
    hasAnyRole
  };
}
