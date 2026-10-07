'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import AuthFormWrapper from '@/components/auth/AuthFormWrapper';
import AuthFeedback from '@/components/auth/AuthFeedback';
import AuthFields from '@/components/auth/AuthFields';
import AuthSubmit from '@/components/auth/AuthSubmit';
import useAuth from '@/hooks/useAuth';
import { useAuthStore } from '@/lib/authStore';
import { validateLoginForm, hasValidationErrors } from '@/utils/validation';
import { LoginRequest, FormValidation } from '@/types/auth';
import { LOGIN_ERROR_MESSAGES, isNetworkError } from '@/lib/authMessages';
import toast from 'react-hot-toast';

const SAVED_LOGIN_EMAIL_KEY = 'saved-login-email';

type LoginSecurityNotice = {
  message: string;
  retryAfterSeconds?: number;
  lockoutUntil?: string;
  requiresCaptcha?: boolean;
};

function LoginPageContent() {
  const router = useRouter();
  const { login } = useAuth();
  const { initialized, loading: authLoading, isAuthenticated } = useAuthStore();
  const [formData, setFormData] = useState<LoginRequest>({
    email: '',
    password: ''
  });
  const [errors, setErrors] = useState<FormValidation>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [showEmailVerificationHelp, setShowEmailVerificationHelp] = useState<boolean>(false);
  const [securityNotice, setSecurityNotice] = useState<LoginSecurityNotice | null>(null);

  useEffect(() => {
    try {
      const savedEmail = localStorage.getItem(SAVED_LOGIN_EMAIL_KEY);
      if (savedEmail) {
        setFormData((prev: LoginRequest) => ({ ...prev, email: savedEmail }));
      }
    } catch {
      // ignore storage failures (private mode, disabled storage, etc.)
    }
  }, []);

  useEffect(() => {
    try {
      if (sessionStorage.getItem('session_evicted')) {
        sessionStorage.removeItem('session_evicted');
        toast.error('Your session has been revoked. Please log in again.', {
          position: 'top-center',
          duration: 4000,
          style: { background: '#1e293b', color: '#fff', fontWeight: 500, minWidth: '320px' },
        });
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (!initialized || authLoading) return;
    if (isAuthenticated) {
      router.replace('/overview');
    }
  }, [authLoading, initialized, isAuthenticated, router]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev: LoginRequest) => ({
      ...prev,
      [name]: value
    }));

    if (name === 'email') {
      try {
        localStorage.setItem(SAVED_LOGIN_EMAIL_KEY, value);
      } catch {
        // ignore storage failures
      }
    }
    
    // Clear error when user starts typing
    if (errors[name as keyof FormValidation]) {
      setErrors((prev: FormValidation) => ({
        ...prev,
        [name]: ''
      }));
    }

    if (securityNotice) {
      setSecurityNotice(null);
    }
    
    // Clear email verification help when user starts typing
    if (showEmailVerificationHelp) {
      setShowEmailVerificationHelp(false);
    }
  };

  const validateForm = (): boolean => {
    const newErrors = validateLoginForm(formData);
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    
    if (!validateForm()) return;
    setSecurityNotice(null);
    setLoading(true);

    try {
      const result = await login(formData);
      
      if (result.success) {
        try {
          localStorage.removeItem(SAVED_LOGIN_EMAIL_KEY);
        } catch {
          // ignore storage failures
        }
        toast.success('Login successful!', {
          duration: 2000,
          position: 'top-center',
        });
      } else {
        handleLoginError(result);
      }
    } catch (error: any) {
      console.error('Login error:', error);
      const message = isNetworkError(error) ? LOGIN_ERROR_MESSAGES.NETWORK : LOGIN_ERROR_MESSAGES.GENERIC;
      toast.error(message, { position: 'top-center' });
    } finally {
      setLoading(false);
    }
  };

  const handleLoginError = (result: any) => {
    const { errorCode, statusCode } = result;
    const message = result.error;

    if (errorCode === 'TOO_MANY_ATTEMPTS' || errorCode === 'LOGIN_LOCKED' || result.requires_captcha) {
      const noticeMessage =
        errorCode === 'LOGIN_LOCKED'
          ? message || LOGIN_ERROR_MESSAGES.LOGIN_LOCKED
          : message || LOGIN_ERROR_MESSAGES.TOO_MANY_ATTEMPTS;
      setSecurityNotice({
        message: noticeMessage,
        retryAfterSeconds: result.retry_after_seconds,
        lockoutUntil: result.lockout_until,
        requiresCaptcha: result.requires_captcha,
      });
      toast.error(noticeMessage, {
        duration: 5000,
        position: 'top-center',
      });
      setFormData((prev: LoginRequest) => ({
        ...prev,
        password: '',
      }));
      setErrors({});
      return;
    }

    if (errorCode === 'EMAIL_NOT_VERIFIED' || message?.includes('not verified')) {
      setShowEmailVerificationHelp(true);
      toast.error(LOGIN_ERROR_MESSAGES.EMAIL_NOT_VERIFIED, {
        duration: 4000,
        position: 'top-center',
      });
      return;
    }

    if (errorCode === 'NETWORK_ERROR') {
      toast.error(LOGIN_ERROR_MESSAGES.NETWORK, {
        duration: 4000,
        position: 'top-center',
      });
    } else if (errorCode === 'USER_NOT_FOUND' || statusCode === 404) {
      toast.error(LOGIN_ERROR_MESSAGES.EMAIL_NOT_REGISTERED, {
        duration: 4000,
        position: 'top-center',
      });
      // For invalid/unregistered email, clear both email and password fields
      setFormData((prev: LoginRequest) => ({
        ...prev,
        email: '',
        password: '',
      }));
      try {
        localStorage.removeItem(SAVED_LOGIN_EMAIL_KEY);
      } catch {
        // ignore storage failures
      }
    } else if (errorCode === 'INVALID_PASSWORD' || statusCode === 401) {
      toast.error(LOGIN_ERROR_MESSAGES.INVALID_PASSWORD, {
        duration: 4000,
        position: 'top-center',
      });
      // For invalid password, keep email but clear password so user retypes it
      setFormData((prev: LoginRequest) => ({
        ...prev,
        password: '',
      }));
    } else if (errorCode === 'PASSWORD_NOT_SET' || (statusCode === 403 && message?.toLowerCase().includes('password not set'))) {
      toast.error(LOGIN_ERROR_MESSAGES.PASSWORD_NOT_SET, {
        duration: 4000,
        position: 'top-center',
      });
    } else if (statusCode === 400) {
      toast.error(message || LOGIN_ERROR_MESSAGES.VALIDATION, {
        duration: 4000,
        position: 'top-center',
      });
    } else if (statusCode === 500) {
      toast.error(LOGIN_ERROR_MESSAGES.SERVER, {
        duration: 4000,
        position: 'top-center',
      });
    } else {
      toast.error(message || LOGIN_ERROR_MESSAGES.GENERIC, {
        duration: 4000,
        position: 'top-center',
      });
    }
    setErrors({});
  };

  const handleGoogleLogin = async (): Promise<void> => {
    try {
      // Call backend to get Google OAuth URL
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || '';
      const response = await fetch(`${apiUrl}/auth/google/start/`);
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        const message = data.details || data.error || 'Failed to initiate Google sign-in';
        toast.error(message);
        return;
      }

      if (data.authorization_url) {
        if (data.state) {
          try {
            window.sessionStorage.setItem('google_oauth_state', data.state);
          } catch {
            // The signed state in the callback URL remains the source of truth.
          }
        }
        // Redirect to Google OAuth page
        window.location.href = data.authorization_url;
      } else {
        toast.error('Failed to initiate Google sign-in');
      }
    } catch (error) {
      console.error('Google login error:', error);
      toast.error('Failed to initiate Google sign-in');
    }
  };

  // Disable submit button if form has validation errors (excluding general errors)
  const formHasValidationErrors = hasValidationErrors(errors);

  if (initialized && isAuthenticated) {
    return null;
  }

  return (
    <>
    <AuthFormWrapper title="Sign In">
      <form onSubmit={handleSubmit} className="space-y-6">
        <AuthFeedback
          generalError={errors.general}
          showEmailVerificationHelp={showEmailVerificationHelp}
          onDismissEmailVerification={() => setShowEmailVerificationHelp(false)}
        />

        {securityNotice && (
          <div
            role="alert"
            className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
          >
            <p className="font-medium">{securityNotice.message}</p>
            {securityNotice.retryAfterSeconds !== undefined && (
              <p className="mt-1 text-amber-800">
                Try again in {securityNotice.retryAfterSeconds} seconds.
              </p>
            )}
            {securityNotice.lockoutUntil && (
              <p className="mt-1 text-amber-800">
                Locked until {new Date(securityNotice.lockoutUntil).toLocaleString()}.
              </p>
            )}
            {securityNotice.requiresCaptcha && (
              <div
                data-testid="captcha-placeholder"
                className="mt-3 rounded-md border border-dashed border-amber-300 bg-white/70 px-3 py-2 text-amber-800"
              >
                {LOGIN_ERROR_MESSAGES.CAPTCHA_PLACEHOLDER}
              </div>
            )}
          </div>
        )}

        <AuthFields
          fields={[
            {
              label: 'Email',
              type: 'email',
              name: 'email',
              value: formData.email,
              onChange: handleChange,
              error: errors.email,
              required: true,
              placeholder: 'Enter your email',
            },
            {
              label: 'Password',
              type: 'password',
              name: 'password',
              value: formData.password,
              onChange: handleChange,
              error: errors.password,
              required: true,
              placeholder: 'Enter your password',
            },
          ]}
          footer={
            <Link
              href="/forgot-password"
              className="text-sm font-medium text-gray-600 hover:text-gray-500 transition-colors"
            >
              Forgot password?
            </Link>
          }
          footerAlign="end"
        />

        <AuthSubmit
          loading={loading}
          disabled={loading || formHasValidationErrors}
          onSubmitClick={() => {}}
          submitLabel="Sign in"
          auxiliary={
            <>
              <span className="text-gray-600">Don't have an account? </span>
              <Link
                href="/register"
                className="text-sm font-medium text-blue-600 hover:text-blue-500 transition-colors"
              >
                Sign up
              </Link>
            </>
          }
          dividerText="Or continue with"
          googleLabel="Sign in with Google"
          onGoogleLogin={handleGoogleLogin}
        />
      </form>

      {/* Customer portal link */}
      <div className="mt-6 text-center">
        <p className="text-xs text-gray-400">
          Are you a customer?{' '}
          <Link href="/portal/login" className="text-[#3CCED7] hover:underline font-medium">
            Go to Support Portal →
          </Link>
        </p>
      </div>
    </AuthFormWrapper>
    </>
  );
}

export default LoginPageContent;
