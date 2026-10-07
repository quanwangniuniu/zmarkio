'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertCircle, CheckCircle2, Loader2, Mail } from 'lucide-react';
import toast from 'react-hot-toast';
import { ProjectAPI } from '@/lib/api/projectApi';
import { useAuthStore } from '@/lib/authStore';

const getErrorMessage = (err: any): string =>
  // `any` retained because axios errors expose err.response.data.{error,detail,message}
  // which is not surfaced through unknown without narrowing
  err?.response?.data?.error ||
  err?.response?.data?.detail ||
  err?.response?.data?.message ||
  err?.message ||
  'Unable to accept invitation. The link may be invalid or expired.';

function AcceptInvitationContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams?.get('token') || '';

  const currentUser = useAuthStore((state) => state.user);
  const setToken = useAuthStore((state) => state.setToken);
  const setRefreshToken = useAuthStore((state) => state.setRefreshToken);
  const setUser = useAuthStore((state) => state.setUser);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [username, setUsername] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<{ projectName: string; userCreated: boolean } | null>(null);

  const authenticatedMode = isAuthenticated && !!currentUser;

  const disabledReason = useMemo(() => {
    if (!token) return 'Missing invitation token.';
    return null;
  }, [token]);

  useEffect(() => {
    if (!success) return;
    const timer = setTimeout(() => {
      router.push('/overview');
    }, 1500);
    return () => clearTimeout(timer);
  }, [success, router]);

  const handleAccept = async () => {
    setError(null);

    if (disabledReason) {
      setError(disabledReason);
      return;
    }

    if (!authenticatedMode) {
      if (!password || password.length < 8) {
        setError('Password must be at least 8 characters.');
        return;
      }
      if (password !== confirmPassword) {
        setError('Passwords do not match.');
        return;
      }
    }

    setSubmitting(true);
    try {
      const response = await ProjectAPI.acceptInvitation(
        token,
        authenticatedMode
          ? undefined
          : { password, username: username.trim() || undefined }
      );

      const newJwt: string | undefined = response?.token;
      const newRefresh: string | undefined = response?.refresh;
      const createdUser = !!response?.user_created;
      const projectName: string = response?.project?.name || 'your project';

      if (createdUser && newJwt) {
        setToken(newJwt);
        if (newRefresh) setRefreshToken(newRefresh);
        if (response?.user) setUser(response.user);
      }

      toast.success('Invitation accepted');
      setSuccess({ projectName, userCreated: createdUser });
    } catch (err) {
      const message = getErrorMessage(err);
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  if (success) {
    return (
      <div className="flex items-center gap-3">
        <CheckCircle2 className="h-5 w-5 text-emerald-600" />
        <div>
          <p className="text-sm font-medium text-gray-900">
            You are now a member of {success.projectName}.
          </p>
          <p className="text-xs text-gray-500">Redirecting to overview…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
          <Mail className="h-5 w-5 text-white" />
        </div>
        <div className="min-w-0">
          <h1 className="text-lg font-semibold text-gray-900">Accept invitation</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {authenticatedMode
              ? `Signed in as ${currentUser?.email}. Accept to join the project.`
              : 'Set a password to create your account and join the project.'}
          </p>
        </div>
      </div>

      {disabledReason && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{disabledReason}</span>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {!authenticatedMode && (
        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-gray-700">Username <span className="text-gray-400 font-normal">(optional)</span></label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Defaults to the part before @"
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/20 transition"
            />
          </div>
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-gray-700">
              Password <span className="text-red-500">*</span>
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/20 transition"
            />
          </div>
          <div className="space-y-1.5">
            <label className="block text-xs font-medium text-gray-700">
              Confirm password <span className="text-red-500">*</span>
            </label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Retype password"
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/20 transition"
            />
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={handleAccept}
        disabled={submitting || !!disabledReason}
        className="w-full inline-flex items-center justify-center gap-1.5 px-4 py-2 text-sm font-semibold text-white rounded-md bg-gradient-to-br from-[#3CCED7] to-[#A6E661] hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed"
      >
        {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
        {submitting ? 'Accepting…' : authenticatedMode ? 'Accept invitation' : 'Create account & join'}
      </button>

      {!authenticatedMode && (
        <p className="text-xs text-center text-gray-400">
          Already have an account?{' '}
          <button
            type="button"
            onClick={() => router.push(`/login?next=${encodeURIComponent(`/accept-invitation?token=${token}`)}`)}
            className="text-[#3CCED7] hover:underline font-medium"
          >
            Sign in first
          </button>
        </p>
      )}
    </div>
  );
}

export default function AcceptInvitationPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[#F7F8FA] px-4 py-10">
      <div className="w-full max-w-md">
        <div className="rounded-2xl bg-white shadow-sm ring-1 ring-gray-100 overflow-hidden">
          <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
          <div className="px-6 py-6">
            <Suspense
              fallback={
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <Loader2 className="h-4 w-4 animate-spin text-[#3CCED7]" />
                  Loading invitation…
                </div>
              }
            >
              <AcceptInvitationContent />
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}
