'use client';

import React, { useState } from 'react';
import toast from 'react-hot-toast';
import AuthFields from './AuthFields';
import AuthSubmit from './AuthSubmit';
import { authAPI } from '@/lib/api';
import { useAuthStore } from '@/lib/authStore';

type MfaVerifyStepProps = {
  mfaToken: string;
  onSuccess: () => void;
  onRestart: () => void;
};

// MED-326: second sign-in step for org admins who have set up MFA.
export default function MfaVerifyStep({ mfaToken, onSuccess, onRestart }: MfaVerifyStepProps) {
  const completeLogin = useAuthStore((state) => state.completeLogin);
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);

  const toggleMode = () => {
    setUseRecoveryCode((prev) => !prev);
    setValue('');
    setError(undefined);
  };

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);
    setError(undefined);
    try {
      const response = await authAPI.verifyMfa(
        useRecoveryCode
          ? { mfa_token: mfaToken, recovery_code: value }
          : { mfa_token: mfaToken, code: value },
      );
      await completeLogin(response);
      onSuccess();
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 401) {
        toast.error('Your sign-in has expired. Please sign in again.', { position: 'top-center' });
        onRestart();
        return;
      }
      const invalid = useRecoveryCode
        ? 'That recovery code is not valid or has already been used.'
        : 'That code is not correct. Check your authenticator app and try again.';
      const wait = err?.response?.data?.retry_after_seconds;
      setError(
        status === 429
          ? `Too many incorrect attempts.${wait ? ` Try again in ${wait} seconds.` : ''}`
          : err?.response?.data?.errorCode === 'MFA_INVALID_CODE'
            ? invalid
            : 'Something went wrong. Please try again.',
      );
      setValue('');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <p className="text-sm text-gray-600">
        {useRecoveryCode
          ? 'Enter one of the recovery codes you saved when you set up two-step verification. Each code works once.'
          : 'Open your authenticator app and enter the 6-digit code for this account.'}
      </p>

      <AuthFields
        fields={[
          {
            label: useRecoveryCode ? 'Recovery code' : 'Verification code',
            type: 'text',
            name: useRecoveryCode ? 'recovery_code' : 'code',
            value,
            onChange: (event) => {
              setValue(event.target.value);
              setError(undefined);
            },
            error,
            required: true,
            placeholder: useRecoveryCode ? 'xxxxx-xxxxx' : '123456',
          },
        ]}
        footer={
          <button
            type="button"
            onClick={toggleMode}
            className="text-sm font-medium text-blue-600 hover:text-blue-500 transition-colors"
          >
            {useRecoveryCode ? 'Use your authenticator app instead' : 'Use a recovery code'}
          </button>
        }
        footerAlign="end"
      />

      <AuthSubmit
        loading={loading}
        disabled={loading}
        onSubmitClick={() => {}}
        submitLabel="Verify"
        showDivider={false}
        showGoogle={false}
        onGoogleLogin={() => {}}
        auxiliary={
          <button
            type="button"
            onClick={onRestart}
            className="text-sm font-medium text-gray-600 hover:text-gray-500 transition-colors"
          >
            Back to sign in
          </button>
        }
      />
    </form>
  );
}
