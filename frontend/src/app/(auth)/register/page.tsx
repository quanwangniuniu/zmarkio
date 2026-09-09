'use client';

import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import AuthFormWrapper from '@/components/auth/AuthFormWrapper';
import AuthFeedback from '@/components/auth/AuthFeedback';
import AuthFields from '@/components/auth/AuthFields';
import AuthSubmit from '@/components/auth/AuthSubmit';
import RegisterSuccessMessage from '@/components/auth/RegisterSuccessMessage';
import PasswordRequirements from '@/components/auth/PasswordRequirements';
import useAuth from '@/hooks/useAuth';
import { useAuthStore } from '@/lib/authStore';
import { validateRegistrationForm, hasValidationErrors } from '@/utils/validation';
import { RegisterRequest, FormValidation } from '@/types/auth';
import toast from 'react-hot-toast';

export default function RegisterPage() {
  const router = useRouter();
  const { register } = useAuth();
  const storeLogin = useAuthStore((s) => s.login);
  const [formData, setFormData] = useState<{ 
    username: string;
    email: string;
    password: string;
    confirmPassword: string;
  }>({
    username: '',
    email: '',
    password: '',
    confirmPassword: ''
  });
  const [errors, setErrors] = useState<FormValidation>({});
  const [loading, setLoading] = useState<boolean>(false);
  const [registrationSuccess, setRegistrationSuccess] = useState<boolean>(false);
  const [registrationMessage, setRegistrationMessage] = useState<string>('');
  const inputVersion = useRef(0);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    if (name === 'password' || name === 'username' || name === 'email') {
      inputVersion.current += 1;
    }
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
    
    // Clear error when user starts typing
    if (errors[name as keyof FormValidation] ||
      ((name === 'username' || name === 'email') && errors.password)) {
      setErrors(prev => ({
        ...prev,
        [name]: '',
        ...((name === 'username' || name === 'email') ? { password: '' } : {}),
      }));
    }
  };

  const validateForm = (): boolean => {
    const newErrors = validateRegistrationForm(formData);
    setErrors(newErrors);
    return !hasValidationErrors(newErrors);
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    
    if (!validateForm()) {
      toast.error('Please fix the form errors before submitting');
      return;
    }
    
    setLoading(true);
    
    // Prepare request data according to backend API requirements
    const requestData: RegisterRequest = {
      username: formData.username,
      email: formData.email,
      password: formData.password
    };
    
    console.log('Submitting registration data:', { ...requestData, password: '[HIDDEN]' });
    
    const submittedVersion = inputVersion.current;
    const result = await register(requestData);

    if (result.success) {
      const loginResult = await storeLogin(formData.email, formData.password);
      setLoading(false);
      if (loginResult.success) {
        toast.success('Account created! Redirecting...');
        router.replace('/overview');
        return;
      }
      setRegistrationSuccess(true);
      setRegistrationMessage(result.data?.message || 'Registration successful! Your account is ready to use.');
    } else {
      setLoading(false);
      if (!result.fieldErrors?.password || submittedVersion === inputVersion.current) {
        setErrors(result.fieldErrors || { general: result.error });
      }
    }
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

  // Show success message if registration was successful
  if (registrationSuccess) {
    return (
      <AuthFormWrapper title="Check Your Email">
        <RegisterSuccessMessage
          message={registrationMessage}
          onRegisterAnother={() => {
            setRegistrationSuccess(false);
            setFormData({
              username: '',
              email: '',
              password: '',
              confirmPassword: '',
            });
            setErrors({});
          }}
        />
      </AuthFormWrapper>
    );
  }

  return (
    <AuthFormWrapper title="Create Account">
      <form onSubmit={handleSubmit} className="space-y-6">
        <AuthFeedback generalError={errors.general} />

        <AuthFields
          fields={[
            {
              label: 'Username',
              type: 'text',
              name: 'username',
              value: formData.username,
              onChange: handleChange,
              error: errors.username,
              required: true,
              placeholder: 'Enter your username',
            },
            {
              label: 'Email',
              type: 'email',
              name: 'email',
              value: formData.email,
              onChange: handleChange,
              error: errors.email,
              required: true,
              placeholder: 'Enter your email address',
            },
            {
              label: 'Password',
              type: 'password',
              name: 'password',
              value: formData.password,
              onChange: handleChange,
              error: errors.password,
              description: (
                <PasswordRequirements
                  password={formData.password}
                  username={formData.username}
                  email={formData.email}
                />
              ),
              required: true,
              placeholder: 'Create a password (min 8 characters)',
            },
            {
              label: 'Confirm Password',
              type: 'password',
              name: 'confirmPassword',
              value: formData.confirmPassword,
              onChange: handleChange,
              error: errors.confirmPassword,
              required: true,
              placeholder: 'Confirm your password',
            },
          ]}
          footer={
            <>
              <span className="text-gray-600">Already have an account? </span>
              <Link
                href="/login"
                className="text-sm font-medium text-blue-600 hover:text-blue-500 transition-colors"
              >
                Sign in
              </Link>
            </>
          }
        />

        <AuthSubmit
          loading={loading}
          disabled={loading || formHasValidationErrors}
          onSubmitClick={() => {}} // Empty onClick for submit buttons
          submitLabel="Create Account"
          loadingLabel="Creating Account..."
          dividerText="Or sign up with"
          googleLabel="Sign up with Google"
          googleDisabled={loading}
          onGoogleLogin={handleGoogleLogin}
        />
      </form>
    </AuthFormWrapper>
  );
}
