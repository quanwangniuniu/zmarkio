'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import toast from 'react-hot-toast';
import api from '@/lib/api';
import { useAuthStore } from '@/lib/authStore';

function GoogleCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    setUser,
    setToken,
    setOrganizationAccessToken,
    getUserTeams
  } = useAuthStore();
  const [status, setStatus] = useState<'processing' | 'success' | 'error'>('processing');
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [errorDetails, setErrorDetails] = useState<string>('');
  const [solution, setSolution] = useState<string>('');

  useEffect(() => {
    const handleCallback = async () => {
      try {
        // Check if we received auth_data directly (from backend redirect for existing user)
        const authDataParam = searchParams.get('auth_data');
        
        if (authDataParam) {
          // Decode base64 auth data from backend
          try {
            const authDataJson = atob(authDataParam);
            const authData = JSON.parse(authDataJson);
            
            // Store tokens and user in Zustand
            if (authData.token) {
              setToken(authData.token);
            }
            if (authData.organization_access_token) {
              setOrganizationAccessToken(authData.organization_access_token);
            }
            if (authData.user) {
              setUser(authData.user);
            }

            try {
              await getUserTeams();
            } catch (teamError) {
              console.warn('Failed to fetch user teams:', teamError);
            }

            setStatus('success');
            toast.success('Login successful!');

            router.push('/overview');
            return;
            
          } catch (decodeError) {
            console.error('Failed to decode auth data:', decodeError);
            setStatus('error');
            setErrorMessage('Failed to process authentication data');
            setTimeout(() => router.push('/login'), 3000);
            return;
          }
        }
        
        // Original flow: Get authorization code from URL (for new user or password setup)
        const code = searchParams.get('code');
        const state = searchParams.get('state');
        const error = searchParams.get('error');

        // Check for OAuth errors
        if (error) {
          setStatus('error');
          setErrorMessage(`Google OAuth error: ${error}`);
          toast.error(`Authentication failed: ${error}`);
          setTimeout(() => router.push('/login'), 3000);
          return;
        }

        if (!code) {
          setStatus('error');
          setErrorMessage('No authorization code or auth data received');
          toast.error('Authentication failed');
          setTimeout(() => router.push('/login'), 3000);
          return;
        }

        if (!state) {
          setStatus('error');
          setErrorMessage('No OAuth state received');
          toast.error('Authentication failed');
          setTimeout(() => router.push('/login'), 3000);
          return;
        }

        // If we reach here with a code, it means backend hasn't processed it yet
        // This shouldn't happen with the new flow, but redirect to backend for processing
        const callbackParams = new URLSearchParams({ code });
        if (state) {
          callbackParams.set('state', state);
        }
        window.location.href = `${process.env.NEXT_PUBLIC_API_URL || ''}/auth/google/callback/?${callbackParams.toString()}`;
        return;

      } catch (error: any) {
        console.error('Google OAuth callback error:', error);
        setStatus('error');
        
        // Extract detailed error information from backend response
        const errorData = error.response?.data || {};
        const errorMsg = errorData.error || 'Authentication failed';
        const details = errorData.details || '';
        const solutionMsg = errorData.solution || 'Please try again';
        
        setErrorMessage(errorMsg);
        setErrorDetails(details);
        setSolution(solutionMsg);
        
        // Show user-friendly toast message
        if (errorMsg.includes('invalid') || errorMsg.includes('already been used')) {
          toast.error('Please try signing in with Google again');
        } else {
          toast.error(errorMsg);
        }
        
        // Redirect to login after showing error
        setTimeout(() => router.push('/login'), 5000);
      }
    };

    handleCallback();
  }, [
    searchParams,
    router,
    getUserTeams,
    setOrganizationAccessToken,
    setToken,
    setUser,
  ]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-white to-cyan-50">
      <div className="max-w-md w-full space-y-8 text-center">
        {status === 'processing' && (
          <div>
            <div className="flex justify-center">
              <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-blue-600"></div>
            </div>
            <h2 className="mt-6 text-2xl font-bold text-gray-900">
              Processing Google Sign-in
            </h2>
            <p className="mt-2 text-sm text-gray-600">
              Please wait while we authenticate your account...
            </p>
          </div>
        )}

        {status === 'success' && (
          <div>
            <div className="flex justify-center">
              <svg className="h-16 w-16 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h2 className="mt-6 text-2xl font-bold text-gray-900">
              Success!
            </h2>
            <p className="mt-2 text-sm text-gray-600">
              Redirecting to campaigns...
            </p>
          </div>
        )}

        {status === 'error' && (
          <div>
            <div className="flex justify-center">
              <svg className="h-16 w-16 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            </div>
            <h2 className="mt-6 text-2xl font-bold text-gray-900">
              Authentication Failed
            </h2>
            <p className="mt-2 text-sm text-red-600 font-medium">
              {errorMessage}
            </p>
            {errorDetails && (
              <p className="mt-2 text-xs text-gray-500">
                {errorDetails}
              </p>
            )}
            {solution && (
              <div className="mt-4 p-3 bg-blue-50 rounded-lg">
                <p className="text-sm text-blue-800">
                  💡 {solution}
                </p>
              </div>
            )}
            <button
              onClick={() => router.push('/login')}
              className="mt-6 inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
            >
              Back to Login
            </button>
            <p className="mt-4 text-xs text-gray-500">
              Auto-redirecting in 5 seconds...
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

export default function GoogleCallbackPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gradient-to-br from-blue-50 via-white to-cyan-50" />}>
      <GoogleCallbackContent />
    </Suspense>
  );
}
