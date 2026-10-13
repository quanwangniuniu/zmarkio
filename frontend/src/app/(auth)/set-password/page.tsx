'use client';

import { Suspense, useState, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { 
  FormContainer, 
  FormInput, 
  FormButton, 
  ErrorMessage
} from '@/components/form';
import toast from 'react-hot-toast';
import { authApi } from '@/lib/api/authApi';
import { useAuthStore } from '@/lib/authStore';

interface SetPasswordFormData {
  currentPassword: string;
  password: string;
  confirmPassword: string;
}

interface ValidationErrors {
  password?: string;
  confirmPassword?: string;
  general?: string;
}

function SetPasswordContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setUser = useAuthStore((s) => s.setUser);
  const setAuthToken = useAuthStore((s) => s.setToken);
  const setOrganizationAccessToken = useAuthStore((s) => s.setOrganizationAccessToken);
  const getUserTeams = useAuthStore((s) => s.getUserTeams);
  const [token, setToken] = useState<string>('');
  const isRotationMode = searchParams.get('rotation') === '1';
  const [formData, setFormData] = useState<SetPasswordFormData>({
    currentPassword: '',
    password: '',
    confirmPassword: ''
  });
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [loading, setLoading] = useState<boolean>(false);

  useEffect(() => {
    const tokenFromUrl = searchParams.get('token');
    if (isRotationMode) {
      return;
    }
    if (!tokenFromUrl) {
      toast.error('Invalid or missing token');
      router.push('/login');
      return;
    }
    setToken(tokenFromUrl);
  }, [searchParams, router, isRotationMode]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));

    if (errors[name as keyof ValidationErrors]) {
      setErrors(prev => ({
        ...prev,
        [name]: ''
      }));
    }
  };

  const validateForm = (): boolean => {
    const newErrors: ValidationErrors = {};

    if (isRotationMode && !formData.currentPassword) {
      newErrors.general = 'Current password is required';
    }

    if (!formData.password) {
      newErrors.password = 'Password is required';
    } else if (formData.password.length < 8) {
      newErrors.password = 'Password must be at least 8 characters';
    }

    if (!formData.confirmPassword) {
      newErrors.confirmPassword = 'Please confirm your password';
    } else if (formData.password !== formData.confirmPassword) {
      newErrors.confirmPassword = 'Passwords do not match';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!validateForm()) {
      toast.error('Please fix the form errors before submitting');
      return;
    }

    if (!isRotationMode && !token) {
      toast.error('Invalid token');
      return;
    }

    setLoading(true);

    try {
      const response = isRotationMode
        ? await authApi.changePassword(formData.currentPassword, formData.password)
        : await authApi.googleSetPassword({
            token: token,
            password: formData.password
          });

      const accessToken = 'token' in response ? response.token : undefined;
      const organization_access_token =
        'organization_access_token' in response ? response.organization_access_token : undefined;
      const { user } = response;

      if (accessToken) {
        setAuthToken(accessToken);
      }
      if (organization_access_token) {
        setOrganizationAccessToken(organization_access_token);
      }
      if (user) {
        setUser(user);
      }

      try {
        await getUserTeams();
      } catch (teamError) {
        console.warn('Failed to fetch user teams after password setup:', teamError);
      }

      toast.success(isRotationMode ? 'Password changed successfully.' : 'Password set successfully! Redirecting...');
      router.replace('/overview');
    } catch (error: any) {
      console.error('Set password error:', error);
      const errorData = error.response?.data;
      if (errorData?.details && Array.isArray(errorData.details)) {
        const detailedErrors = errorData.details.join('. ');
        setErrors({ password: detailedErrors });
        toast.error('Password does not meet requirements');
      } else {
        const errorMessage = errorData?.error || 'Failed to set password. Please try again.';
        setErrors({ general: errorMessage });
        toast.error(errorMessage);
      }
    } finally {
      setLoading(false);
    }
  };

  const formHasValidationErrors = Object.keys(errors).some(key => key !== 'general' && errors[key as keyof ValidationErrors]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-white to-cyan-50 py-12 px-4 sm:px-6 lg:px-8">
      <FormContainer
        title={isRotationMode ? "Change Your Password" : "Set Your Password"}
        subtitle={isRotationMode ? "Your admin password rotation period has expired" : "Complete your Google sign-up by setting a password"}
      >
        <form onSubmit={handleSubmit} className="space-y-6">
          {errors.general && <ErrorMessage message={errors.general} />}

          {isRotationMode && (
            <div className="space-y-2">
              <FormInput
                label="Current Password"
                type="password"
                name="currentPassword"
                value={formData.currentPassword}
                onChange={handleChange}
                required
                placeholder="Enter your current password"
              />
              <div className="text-right">
                <Link
                  href="/forgot-password"
                  className="text-sm font-medium text-blue-600 hover:text-blue-500 transition-colors"
                >
                  Forgot current password?
                </Link>
              </div>
            </div>
          )}

          <FormInput
            label={isRotationMode ? "New Password" : "Password"}
            type="password"
            name="password"
            value={formData.password}
            onChange={handleChange}
            error={errors.password}
            required
            placeholder={isRotationMode ? "Create a new password (min 8 characters)" : "Create a password (min 8 characters)"}
          />

          <FormInput
            label="Confirm Password"
            type="password"
            name="confirmPassword"
            value={formData.confirmPassword}
            onChange={handleChange}
            error={errors.confirmPassword}
            required
            placeholder="Confirm your password"
          />

          <FormButton
            type="submit"
            onClick={() => {}}
            loading={loading}
            disabled={loading || !!formHasValidationErrors}
          >
            {loading ? (isRotationMode ? 'Changing Password...' : 'Setting Password...') : (isRotationMode ? 'Change Password & Continue' : 'Set Password & Continue')}
          </FormButton>

          {!isRotationMode && (
            <div className="text-center">
              <Link
                href="/login"
                className="text-sm font-medium text-blue-600 hover:text-blue-500 transition-colors"
              >
                Back to Login
              </Link>
            </div>
          )}
        </form>
      </FormContainer>
    </div>
  );
}

export default function SetPasswordPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-cyan-50" />}>
      <SetPasswordContent />
    </Suspense>
  );
}
