'use client';

import React, { useEffect, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import AuthFields from './AuthFields';
import AuthSubmit from './AuthSubmit';
import { authAPI } from '@/lib/api';
import { MfaEnrollConfirmResponse, MfaEnrollStartResponse } from '@/types/auth';

type MfaSetupWizardProps = {
  // From the login step; omit when the admin is already signed in (profile page).
  mfaToken?: string;
  onComplete: (response: MfaEnrollConfirmResponse) => void;
  onExpired?: () => void;
  onAlreadyEnrolled?: () => void;
};

const RECOVERY_CODES_FILENAME = 'mediajira-recovery-codes.txt';

// MED-326: two screens — scan and confirm, then save the recovery codes.
export default function MfaSetupWizard({
  mfaToken,
  onComplete,
  onExpired,
  onAlreadyEnrolled,
}: MfaSetupWizardProps) {
  const [setup, setSetup] = useState<MfaEnrollStartResponse | null>(null);
  const [result, setResult] = useState<MfaEnrollConfirmResponse | null>(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);

  const handleRequestError = (err: any) => {
    const status = err?.response?.status;
    const errorCode = err?.response?.data?.errorCode;
    if (status === 401 && onExpired) {
      onExpired();
    } else if (errorCode === 'MFA_ALREADY_ENROLLED' && onAlreadyEnrolled) {
      onAlreadyEnrolled();
    } else if (errorCode === 'MFA_INVALID_CODE') {
      setError('That code is not correct. Check your authenticator app and try again.');
      setCode('');
    } else {
      setError('Something went wrong. Please try again.');
    }
  };

  useEffect(() => {
    authAPI
      .startMfaEnrollment(mfaToken)
      .then(setSetup)
      .catch(handleRequestError);
    // Start once per wizard; a new key is only issued when the wizard is opened again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleConfirm = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);
    setError(undefined);
    try {
      setResult(await authAPI.confirmMfaEnrollment(code, mfaToken));
    } catch (err) {
      handleRequestError(err);
    } finally {
      setLoading(false);
    }
  };

  const downloadCodes = () => {
    if (!result) return;
    const text = `MediaJira recovery codes\nEach code can be used once.\n\n${result.recovery_codes.join('\n')}\n`;
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = RECOVERY_CODES_FILENAME;
    link.click();
    URL.revokeObjectURL(url);
  };

  const copyCodes = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.recovery_codes.join('\n'));
      setError(undefined);
    } catch {
      setError('Could not copy. Please download the codes instead.');
    }
  };

  if (result) {
    return (
      <div className="space-y-6">
        <div role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">Save your recovery codes now.</p>
          <p className="mt-1 text-amber-800">
            If you lose your phone, each code lets you sign in once. They will not be shown again.
          </p>
        </div>

        <ul aria-label="Recovery codes" className="grid grid-cols-2 gap-2 rounded-md bg-gray-50 p-4 font-mono text-sm text-gray-800">
          {result.recovery_codes.map((recoveryCode) => (
            <li key={recoveryCode}>{recoveryCode}</li>
          ))}
        </ul>

        <div className="flex gap-3">
          <button
            type="button"
            onClick={downloadCodes}
            className="flex-1 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Download
          </button>
          <button
            type="button"
            onClick={copyCodes}
            className="flex-1 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            Copy
          </button>
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}

        <label className="flex items-center gap-2 text-sm text-gray-700">
          <input type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} />
          I have saved these recovery codes
        </label>

        <button
          type="button"
          disabled={!saved}
          onClick={() => onComplete(result)}
          className="w-full rounded-md bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Continue
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleConfirm} className="space-y-6">
      <p className="text-sm text-gray-600">
        Install an authenticator app such as Google Authenticator or Microsoft Authenticator on your
        phone, then scan this QR code with it.
      </p>

      {setup ? (
        <div className="flex flex-col items-center gap-3">
          <QRCodeSVG value={setup.otpauth_uri} size={176} aria-label="MFA QR code" />
          <p className="text-center text-xs text-gray-500">
            Can&apos;t scan it? Enter this key in the app instead:
            <br />
            <code data-testid="mfa-secret" className="break-all font-mono text-sm text-gray-800">
              {setup.secret}
            </code>
          </p>
        </div>
      ) : (
        !error && <p className="text-center text-sm text-gray-500">Loading…</p>
      )}

      <AuthFields
        fields={[
          {
            label: 'Verification code',
            type: 'text',
            name: 'code',
            value: code,
            onChange: (event) => {
              setCode(event.target.value);
              setError(undefined);
            },
            error,
            required: true,
            placeholder: '123456',
          },
        ]}
      />

      <AuthSubmit
        loading={loading}
        disabled={loading || !setup}
        onSubmitClick={() => {}}
        submitLabel="Verify and turn on"
        showDivider={false}
        showGoogle={false}
        onGoogleLogin={() => {}}
      />
    </form>
  );
}
