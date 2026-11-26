'use client';

import React, { useState } from 'react';
import toast from 'react-hot-toast';
import MfaSetupWizard from '@/components/auth/MfaSetupWizard';
import { useAuthStore } from '@/lib/authStore';

type MfaProfileSectionProps = {
  autoStart?: boolean;
};

// MED-326: "Security" tab of the profile page, shown to org admins.
export default function MfaProfileSection({ autoStart = false }: MfaProfileSectionProps) {
  const completeLogin = useAuthStore((state) => state.completeLogin);
  const [view, setView] = useState<'intro' | 'setup' | 'enabled'>(autoStart ? 'setup' : 'intro');

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500">Two-step verification</h2>

      {view === 'intro' && (
        <>
          <p className="text-sm text-gray-600">
            Organization admins must use an authenticator app as a second step when signing in.
          </p>
          <button
            type="button"
            onClick={() => setView('setup')}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Set up two-step verification
          </button>
        </>
      )}

      {view === 'setup' && (
        <div className="max-w-md">
          <MfaSetupWizard
            onComplete={async (response) => {
              await completeLogin(response);
              toast.success('Two-step verification is on.');
              setView('enabled');
            }}
            onAlreadyEnrolled={() => setView('enabled')}
          />
        </div>
      )}

      {view === 'enabled' && (
        <p className="text-sm text-gray-600">Two-step verification is on for this account.</p>
      )}
    </section>
  );
}
