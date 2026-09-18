'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { AlertTriangle, Calendar, Check, ChevronRight, Clock, Copy, CreditCard, Loader2, Pencil, RefreshCw, Send, Trash2, UserPlus, Users, X as XIcon, Zap } from 'lucide-react';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { OrganizationAPI, OrgDetail, OrgMember } from '@/lib/api/organizationApi';
import OrgRecentActivityCard from '@/components/organizations/OrgRecentActivityCard';
import OrgUsageBreakdownCard from '@/components/organizations/OrgUsageBreakdownCard';
import { useProjectStore } from '@/lib/projectStore';
import { useAuthStore } from '@/lib/authStore';
import { Skeleton } from '@/components/ui/skeleton';
import toast from 'react-hot-toast';

// ── Helpers ───────────────────────────────────────────────────────────────────

function getInitials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toString();
}

function formatTokenFull(n: number): string {
  return n.toLocaleString();
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(
    new Date(iso)
  );
}

function formatDateRange(start: string | null, end: string | null): string {
  if (!start || !end) return '—';
  const s = new Date(start);
  const e = new Date(end);
  const fmt = (d: Date) =>
    new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(d);
  return `${fmt(s)} – ${fmt(e)}, ${e.getFullYear()}`;
}

function formatPrice(cents: number, currency: string): string {
  const curr = currency || 'AUD';
  // Use narrowSymbol to get just "$", then prepend the ISO currency code
  // so the output reads "AUD $0" instead of the ambiguous "A$0".
  const symbol = new Intl.NumberFormat('en-AU', {
    style: 'currency',
    currency: curr,
    currencyDisplay: 'narrowSymbol',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(cents / 100);
  return `${curr} ${symbol}`;
}

function formatQuota(quota: number | null): string {
  if (quota === null) return 'Unlimited';
  return formatTokenCount(quota);
}

const ROLE_COLORS: Record<string, string> = {
  admin:  'bg-amber-50 text-amber-600 border border-amber-200',
  owner:  'bg-amber-50 text-amber-600 border border-amber-200',
  member: 'bg-[#3CCED7]/10 text-[#3CCED7] border border-[#3CCED7]/20',
  viewer: 'bg-gray-100 text-gray-500 border border-gray-200',
};
function roleBadge(role: string) {
  return ROLE_COLORS[role.toLowerCase()] ?? 'bg-gray-100 text-gray-500 border border-gray-200';
}

// ── Main content component ────────────────────────────────────────────────────

function OrgDetailContent() {
  const params = useParams<{ orgId: string }>();
  const router = useRouter();
  const currentUserId = useAuthStore((s) => s.user?.id);

  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [members, setMembers] = useState<OrgMember[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);

  const [switching, setSwitching] = useState(false);
  const [slugCopied, setSlugCopied] = useState(false);

  // Slug edit state
  const [editingSlug, setEditingSlug] = useState(false);
  const [slugDraft, setSlugDraft] = useState('');
  const [slugSaving, setSlugSaving] = useState(false);
  const [slugError, setSlugError] = useState<string | null>(null);

  // Remove member state
  const [confirmRemoveId, setConfirmRemoveId] = useState<number | null>(null);
  const [removingMemberId, setRemovingMemberId] = useState<number | null>(null);

  // Delete org state
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showLastOrgModal, setShowLastOrgModal] = useState(false);

  // Invite form state
  const [showInviteForm, setShowInviteForm] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'member' | 'admin' | 'viewer'>('member');
  const [inviting, setInviting] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);

  // Session cap edit state
  const [sessionCapDraft, setSessionCapDraft] = useState<string>('');
  const [sessionCapSaving, setSessionCapSaving] = useState(false);

  useEffect(() => {
    const id = params.orgId;
    if (!id) {
      setError('Invalid organization ID.');
      setLoading(false);
      return;
    }

    // Fetch org detail
    OrganizationAPI.getOrganizationDetail(id)
      .then(setOrg)
      .catch((e: { response?: { data?: { error?: string } } }) => {
        const msg = e?.response?.data?.error ?? 'Failed to load organization.';
        setError(msg);
        toast.error(msg);
      })
      .finally(() => setLoading(false));

    // Fetch members (best-effort — admin only)
    setMembersLoading(true);
    OrganizationAPI.getOrganizationMembers(id)
      .then((data) => setMembers(data.members))
      .catch(() => {
        // Non-admin users get a 403; silently ignore
      })
      .finally(() => setMembersLoading(false));
  }, [params.orgId]);

  // ── Invite handler ────────────────────────────────────────────────────────

  const handleInvite = async () => {
    if (!org || !inviteEmail.trim()) return;
    setInviting(true);
    setInviteError(null);
    setInviteSuccess(false);
    try {
      await OrganizationAPI.createOrgInvitation(org.id, inviteEmail.trim(), inviteRole);
      setInviteSuccess(true);
      setInviteEmail('');
      setInviteRole('member');
      // Auto-hide success after 3 s
      setTimeout(() => setInviteSuccess(false), 3000);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string; detail?: string } } };
      const msg =
        err?.response?.data?.error ??
        err?.response?.data?.detail ??
        'Failed to send invitation.';
      setInviteError(msg);
    } finally {
      setInviting(false);
    }
  };

  // ── Switch handler ────────────────────────────────────────────────────────

  const handleSwitch = async () => {
    if (!org || switching) return;
    setSwitching(true);
    try {
      await OrganizationAPI.switchOrganization(org.id);
      setOrg({ ...org, is_current: true });
      useProjectStore.getState().clearProjects();
      toast.success(`Switched to ${org.name}`);
    } catch {
      toast.error('Failed to switch organization.');
    } finally {
      setSwitching(false);
    }
  };

  // ── Slug edit handler ─────────────────────────────────────────────────────

  const handleStartEditSlug = () => {
    if (!org) return;
    setSlugDraft(org.slug);
    setSlugError(null);
    setEditingSlug(true);
  };

  const handleCancelEditSlug = () => {
    setEditingSlug(false);
    setSlugError(null);
  };

  const handleSaveSlug = async () => {
    if (!org || slugSaving) return;
    const trimmed = slugDraft.trim().toLowerCase();
    if (!trimmed) {
      setSlugError('Organization code cannot be empty.');
      return;
    }
    if (!/^[a-z0-9-]+$/.test(trimmed)) {
      setSlugError('Only lowercase letters, numbers, and hyphens are allowed.');
      return;
    }
    if (trimmed === org.slug) {
      setEditingSlug(false);
      return;
    }
    setSlugSaving(true);
    setSlugError(null);
    try {
      const result = await OrganizationAPI.updateOrgSlug(org.id, trimmed);
      setOrg({ ...org, slug: result.slug });
      setEditingSlug(false);
      toast.success('Organization code updated successfully.');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } } };
      setSlugError(err?.response?.data?.error ?? 'Failed to update organization code.');
    } finally {
      setSlugSaving(false);
    }
  };

  // ── Remove member handler ─────────────────────────────────────────────────

  const handleRemoveMember = async (userId: number) => {
    if (!org) return;
    setRemovingMemberId(userId);
    try {
      await OrganizationAPI.removeMember(org.id, userId);
      setMembers((prev) => prev.filter((m) => m.user.id !== userId));
      setOrg((prev) => prev ? { ...prev, member_count: prev.member_count - 1 } : prev);
      setConfirmRemoveId(null);
      toast.success('Member removed.');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } } };
      toast.error(err?.response?.data?.error ?? 'Failed to remove member.');
    } finally {
      setRemovingMemberId(null);
    }
  };

  // ── Delete org handler ────────────────────────────────────────────────────

  const handleDeleteOrg = async () => {
    if (!org) return;
    setDeleting(true);
    try {
      await OrganizationAPI.deleteOrganization(org.id);
      useProjectStore.getState().clearProjects();
      toast.success(`"${org.name}" has been deleted.`);
      router.push('/profile');
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { error?: string } } })?.response?.data?.error ?? '';
      setShowDeleteConfirm(false);
      if (msg.toLowerCase().includes('at least one')) {
        setShowLastOrgModal(true);
      } else {
        toast.error(msg || 'Failed to delete organization.');
      }
    } finally {
      setDeleting(false);
    }
  };

  // ── Loading state ─────────────────────────────────────────────────────────

  if (loading) {
    return (
      <DashboardLayout>
        <div className="p-6 space-y-6 max-w-[1200px] mx-auto">
          <div className="px-8 py-6">
            <div className="flex items-start gap-5">
              <Skeleton className="w-20 h-20 rounded-full shrink-0" />
              <div className="flex-1 space-y-3 pt-1">
                <Skeleton className="h-7 w-56" />
                <Skeleton className="h-4 w-40" />
              </div>
            </div>
          </div>
          <div className="flex gap-6 items-start">
            <div className="flex-1 space-y-5">
              <Skeleton className="h-44 rounded-xl" />
              <Skeleton className="h-52 rounded-xl" />
            </div>
            <div className="w-[340px] shrink-0 space-y-4">
              <Skeleton className="h-52 rounded-xl" />
              <Skeleton className="h-44 rounded-xl" />
              <Skeleton className="h-40 rounded-xl" />
            </div>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  // ── Error state ───────────────────────────────────────────────────────────

  if (error || !org) {
    return (
      <DashboardLayout>
        <div className="p-6 flex items-center justify-center min-h-[40vh]">
          <div className="text-center">
            <div className="w-14 h-14 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-4">
              <span className="text-2xl">⚠️</span>
            </div>
            <p className="text-gray-700 font-medium">{error ?? 'Organization not found.'}</p>
          </div>
        </div>
      </DashboardLayout>
    );
  }

  // ── Computed values ───────────────────────────────────────────────────────

  const sub = org.subscription;
  const usage = org.usage;
  const quota = sub?.plan.monthly_token_quota ?? null;
  const tokensUsed = usage?.tokens_used ?? 0;
  const usagePct = quota ? Math.min((tokensUsed / quota) * 100, 100) : null;
  const seatsUsed = org.member_count;
  const seatsPurchased = sub?.seat_count ?? sub?.plan.included_seats ?? null;
  const seatPct = seatsPurchased ? Math.min((seatsUsed / seatsPurchased) * 100, 100) : null;
  return (
    <DashboardLayout>
      <div className="p-6 space-y-6 max-w-[1200px] mx-auto">

        {/* ── Page Header ─────────────────────────────────────────────────── */}
        <div className="px-8 py-6">
          <div className="flex items-start justify-between gap-6 flex-wrap">
            <div className="flex items-start gap-5 min-w-0">
              <div className="w-20 h-20 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0 shadow-md">
                <span className="text-white text-2xl font-bold">{getInitials(org.name)}</span>
              </div>
              <div className="min-w-0">
                <h1 className="text-2xl font-bold text-gray-900 leading-tight">{org.name}</h1>
                <div className="mt-1 flex flex-col gap-1">
                  {editingSlug ? (
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-gray-400">Organization code:</span>
                        <input
                          autoFocus
                          type="text"
                          value={slugDraft}
                          onChange={(e) => {
                            setSlugDraft(e.target.value.toLowerCase());
                            setSlugError(null);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleSaveSlug();
                            if (e.key === 'Escape') handleCancelEditSlug();
                          }}
                          className="font-mono text-sm border border-gray-300 rounded-md px-2 py-0.5 focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20 focus:outline-none transition w-48"
                          disabled={slugSaving}
                        />
                        <button
                          type="button"
                          onClick={handleSaveSlug}
                          disabled={slugSaving}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-[#3CCED7] text-white hover:opacity-90 disabled:opacity-50 transition"
                        >
                          {slugSaving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                          {slugSaving ? 'Saving…' : 'Save'}
                        </button>
                        <button
                          type="button"
                          onClick={handleCancelEditSlug}
                          disabled={slugSaving}
                          className="inline-flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition"
                        >
                          <XIcon className="w-3.5 h-3.5" />
                        </button>
                      </div>
                      <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-amber-50 border border-amber-200 text-xs text-amber-700 self-start">
                        <AlertTriangle className="w-3 h-3 shrink-0 text-amber-500" />
                        Lowercase letters, numbers and hyphens only · e.g.{' '}
                        <span className="font-mono">acme-corp</span>
                      </div>
                      {slugError && (
                        <span className="text-xs text-red-500">{slugError}</span>
                      )}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-400 flex items-center gap-1.5 flex-wrap">
                      <span><span className="text-gray-400">Organization code:</span>{' '}<span className="font-mono">{org.slug}</span></span>
                      <button
                        type="button"
                        onClick={() => {
                          navigator.clipboard.writeText(org.slug);
                          setSlugCopied(true);
                          setTimeout(() => setSlugCopied(false), 2000);
                        }}
                        title="Copy organization code"
                        className="inline-flex items-center justify-center w-4 h-4 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition"
                      >
                        {slugCopied ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                      </button>
                      {(['admin', 'owner'] as string[]).includes(org.user_role ?? '') && (
                        <button
                          type="button"
                          onClick={handleStartEditSlug}
                          title="Edit organization code"
                          className="inline-flex items-center justify-center w-4 h-4 rounded text-gray-300 hover:text-[#3CCED7] hover:bg-[#3CCED7]/10 transition"
                        >
                          <Pencil className="w-3 h-3" />
                        </button>
                      )}
                    </p>
                  )}
                  <p className="text-sm text-gray-400 flex items-center gap-1.5 flex-wrap">
                    <span>Created {formatDate(org.created_at)}</span>
                    {(['admin', 'owner'] as string[]).includes(org.user_role ?? '') && (
                      showDeleteConfirm ? (
                        <span className="inline-flex items-center gap-1 ml-1">
                          <span className="text-xs text-red-500">Delete?</span>
                          <button
                            type="button"
                            onClick={handleDeleteOrg}
                            disabled={deleting}
                            className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50 transition"
                          >
                            {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Confirm'}
                          </button>
                          <button
                            type="button"
                            onClick={() => setShowDeleteConfirm(false)}
                            className="text-[10px] text-gray-400 hover:text-gray-600 transition"
                          >
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setShowDeleteConfirm(true)}
                          title="Delete organization"
                          className="inline-flex items-center justify-center w-4 h-4 rounded text-gray-300 hover:text-red-400 hover:bg-red-50 transition ml-1"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      )
                    )}
                  </p>
                </div>
                {org.desc && (
                  <p className="text-sm text-gray-600 mt-3 max-w-xl leading-relaxed">{org.desc}</p>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0 flex-wrap">
              {org.is_current ? (
                <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-purple-600 text-white">
                  Current Organization
                </span>
              ) : (
                <button
                  type="button"
                  onClick={handleSwitch}
                  disabled={switching}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold border border-purple-300 text-purple-600 hover:bg-purple-50 transition disabled:opacity-50"
                >
                  {switching && <Loader2 className="w-3 h-3 animate-spin" />}
                  {switching ? 'Switching…' : 'Set as Current Organization'}
                </button>
              )}
              {org.user_role && (
                <span className={`inline-flex items-center px-3 py-1.5 rounded-full text-xs font-semibold capitalize ${roleBadge(org.user_role)}`}>
                  {org.user_role}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* ── Two-column body ──────────────────────────────────────────────── */}
        <div className="flex gap-6 items-start">

          {/* LEFT COLUMN */}
          <div className="flex-1 min-w-0 space-y-5">

            {/* Token Usage Card */}
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
              <div className="flex items-center justify-between mb-5">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg bg-purple-50 flex items-center justify-center">
                    <Zap className="w-4 h-4 text-purple-500" />
                  </div>
                  <span className="text-base font-semibold text-gray-900">Token Usage</span>
                </div>
                {usage?.updated_at && (
                  <span className="text-xs text-gray-400">Updated {formatDate(usage.updated_at)}</span>
                )}
              </div>

              {usage || quota ? (
                <>
                  <div className="flex items-baseline justify-between mb-3">
                    <div className="text-3xl font-bold text-gray-900">
                      {formatTokenFull(tokensUsed)}
                      <span className="text-base font-normal text-gray-400 ml-2">
                        / {quota ? formatTokenFull(quota) : '∞'} tokens
                      </span>
                    </div>
                    {usagePct !== null && (
                      <span className="text-lg font-semibold text-emerald-500">
                        {usagePct.toFixed(1)}% used
                      </span>
                    )}
                  </div>

                  {usagePct !== null && (
                    <div className="h-3 w-full bg-gray-100 rounded-full overflow-hidden mb-4">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${usagePct}%`, background: 'linear-gradient(90deg, #22c55e, #06b6d4)' }}
                      />
                    </div>
                  )}

                  <div className="flex items-center gap-5 text-sm text-gray-500">
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-amber-400 inline-block" />
                      Reserved: <span className="font-medium text-gray-700 ml-1">{formatTokenFull(usage?.tokens_reserved ?? 0)}</span>
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full bg-gray-300 inline-block" />
                      Overage: <span className="font-medium text-gray-700 ml-1">{formatTokenFull(usage?.overage_tokens ?? 0)}</span>
                    </span>
                    {(usage?.overage_tokens ?? 0) === 0 && (
                      <span className="ml-auto inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium border border-emerald-200 text-emerald-600 bg-emerald-50">
                        ✓ Within quota
                      </span>
                    )}
                  </div>
                </>
              ) : (
                <div className="py-6 text-center text-sm text-gray-400">
                  No usage data available for the current month.
                </div>
              )}
            </div>

            {/* Usage Breakdown Card */}
            <OrgUsageBreakdownCard
              breakdown={org.usage_breakdown ?? []}
              totalTokens={org.usage?.tokens_used ?? 0}
            />

            {/* Recent Activity Card */}
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
              <div className="flex items-center gap-2 mb-5">
                <div className="w-8 h-8 rounded-lg bg-gray-50 flex items-center justify-center">
                  <Clock className="w-4 h-4 text-gray-400" />
                </div>
                <span className="text-base font-semibold text-gray-900">Recent Activity</span>
              </div>
              <OrgRecentActivityCard activities={org.recent_activity ?? []} />
            </div>

          </div>

          {/* RIGHT COLUMN */}
          <div className="w-[340px] shrink-0 space-y-4">

            {/* Subscription Plan Card */}
            {sub ? (
              <div
                className="rounded-xl p-6 text-white shadow-md relative overflow-hidden"
                style={{ background: 'linear-gradient(135deg, #5b4fcf 0%, #7c6ede 100%)' }}
              >
                <div className="absolute -top-8 -right-8 w-32 h-32 rounded-full bg-white/10" />

                <div className="flex items-start justify-between mb-4 relative">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-widest text-purple-200">Current Plan</p>
                    <h2 className="text-xl font-bold mt-1">{sub.plan.name}</h2>
                  </div>
                  <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ${sub.is_active ? 'bg-white/20 text-white' : 'bg-red-400/30 text-red-100'}`}>
                    {sub.is_active ? '✓ Active' : 'Inactive'}
                  </span>
                </div>

                <div className="mb-4 relative">
                  <div className="text-4xl font-bold">
                    {formatPrice(sub.plan.base_price_cents, sub.plan.currency)}
                    <span className="text-lg font-normal text-purple-200">/mo</span>
                  </div>
                  {sub.plan.desc && (
                    <p className="text-sm text-purple-200 mt-1">{sub.plan.desc}</p>
                  )}
                </div>

                <div className="border-t border-white/20 pt-4 space-y-2 relative">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-purple-200">Included seats</span>
                    <span className="font-semibold">{sub.plan.included_seats} seats</span>
                  </div>
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-purple-200">Monthly tokens</span>
                    <span className="font-semibold">{formatQuota(sub.plan.monthly_token_quota)} tokens</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50 p-6 text-center">
                <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-2">Current Plan</p>
                <p className="text-sm text-gray-400">No active subscription found.</p>
              </div>
            )}

            {/* Billing Status Card — always shown */}
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
              <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-4">
                Billing Status
              </p>

              {sub ? (
                <div className="space-y-4">
                  {/* Seats */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <div className="flex items-center gap-2 text-sm text-gray-600">
                        <Users className="w-4 h-4 text-gray-400" />
                        <span>Seats</span>
                      </div>
                      <span className="text-sm font-medium text-gray-900">
                        {seatsUsed}{' '}
                        {seatsPurchased !== null && (
                          <span className="text-gray-400 font-normal">/ {seatsPurchased} active</span>
                        )}
                      </span>
                    </div>
                    {seatPct !== null && (
                      <>
                        <div className="h-1.5 w-full bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full bg-gradient-to-r from-purple-500 to-indigo-400"
                            style={{ width: `${seatPct}%` }}
                          />
                        </div>
                        {seatsPurchased !== null && (
                          <p className="text-xs text-gray-400 mt-1">
                            {Math.max(0, seatsPurchased - seatsUsed)} seat{Math.max(0, seatsPurchased - seatsUsed) !== 1 ? 's' : ''} available
                          </p>
                        )}
                      </>
                    )}
                  </div>

                  <div className="border-t border-gray-100" />

                  {/* Cycle */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <Calendar className="w-4 h-4 text-gray-400" />
                      <span>Cycle</span>
                    </div>
                    <span className="text-sm font-medium text-gray-900">
                      {formatDateRange(sub.start_date, sub.end_date)}
                    </span>
                  </div>

                  {/* Auto-renew */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <RefreshCw className="w-4 h-4 text-gray-400" />
                      <span>Auto-renew</span>
                    </div>
                    {sub.cancel_at_period_end ? (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-600 border border-amber-200">
                        ⚠ Expires at period end
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-600 border border-emerald-200">
                        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
                        Active
                      </span>
                    )}
                  </div>

                  <div className="border-t border-gray-100 pt-2">
                    <button
                      type="button"
                      onClick={() => router.push('/subscription')}
                      className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg border border-gray-200 text-sm font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-300 transition"
                    >
                      <span className="flex items-center gap-2">
                        <CreditCard className="w-4 h-4 text-gray-400" />
                        Manage Billing
                      </span>
                      <ChevronRight className="w-4 h-4 text-gray-400" />
                    </button>
                  </div>
                </div>
              ) : (
                <div className="space-y-4">
                  {/* Show member count even without sub */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <Users className="w-4 h-4 text-gray-400" />
                      <span>Members</span>
                    </div>
                    <span className="text-sm font-medium text-gray-900">{seatsUsed} active</span>
                  </div>
                  <div className="border-t border-gray-100" />
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <Calendar className="w-4 h-4 text-gray-400" />
                      <span>Cycle</span>
                    </div>
                    <span className="text-sm text-gray-400">—</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                      <RefreshCw className="w-4 h-4 text-gray-400" />
                      <span>Auto-renew</span>
                    </div>
                    <span className="text-sm text-gray-400">—</span>
                  </div>

                  <div className="border-t border-gray-100 pt-2">
                    <button
                      type="button"
                      onClick={() => router.push('/subscription')}
                      className="w-full flex items-center justify-between px-3 py-2.5 rounded-lg border border-gray-200 text-sm font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-300 transition"
                    >
                      <span className="flex items-center gap-2">
                        <CreditCard className="w-4 h-4 text-gray-400" />
                        Manage Billing
                      </span>
                      <ChevronRight className="w-4 h-4 text-gray-400" />
                    </button>
                  </div>
                </div>
              )}
            </div>

            {/* Members Card */}
            <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <p className="text-xs font-semibold uppercase tracking-widest text-gray-400">Members</p>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-400">{org.member_count} total</span>
                  {(['admin', 'owner'] as string[]).includes(org.user_role ?? '') && (
                    <button
                      type="button"
                      onClick={() => {
                        setShowInviteForm((v) => !v);
                        setInviteSuccess(false);
                        setInviteError(null);
                      }}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white hover:opacity-90 transition"
                    >
                      <UserPlus className="w-3 h-3" />
                      Invite
                    </button>
                  )}
                </div>
              </div>

              {/* Inline invite form — admin only */}
              {showInviteForm && (
                <div className="mb-4 p-3 rounded-lg border border-[#3CCED7]/25 bg-gradient-to-r from-[#3CCED7]/5 to-[#A6E661]/5 space-y-2">
                  <input
                    type="email"
                    placeholder="Email address"
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleInvite()}
                    disabled={inviting}
                    className="w-full text-sm px-3 py-1.5 rounded-md border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#3CCED7] disabled:opacity-50"
                  />
                  <select
                    value={inviteRole}
                    onChange={(e) => setInviteRole(e.target.value as 'member' | 'admin' | 'viewer')}
                    disabled={inviting}
                    className="w-full text-xs px-2 py-1.5 rounded-md border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#3CCED7] bg-white disabled:opacity-50"
                  >
                    <option value="member">Member</option>
                    <option value="admin">Admin</option>
                    <option value="viewer">Viewer</option>
                  </select>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={handleInvite}
                      disabled={inviting || !inviteEmail.trim()}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white hover:opacity-90 transition disabled:opacity-50"
                    >
                      {inviting ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <Send className="w-3 h-3" />
                      )}
                      {inviting ? 'Sending…' : 'Send Invite'}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setShowInviteForm(false);
                        setInviteEmail('');
                        setInviteRole('member');
                        setInviteError(null);
                        setInviteSuccess(false);
                      }}
                      className="text-xs text-gray-400 hover:text-gray-600 transition"
                    >
                      Cancel
                    </button>
                    {inviteSuccess && (
                      <span className="ml-auto text-xs font-medium text-emerald-600">✓ Invite sent!</span>
                    )}
                    {inviteError && !inviteSuccess && (
                      <span className="ml-auto text-xs text-red-500">{inviteError}</span>
                    )}
                  </div>
                </div>
              )}

              {membersLoading ? (
                <div className="space-y-3">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="flex items-center gap-3">
                      <Skeleton className="w-8 h-8 rounded-full shrink-0" />
                      <div className="flex-1 space-y-1.5">
                        <Skeleton className="h-3.5 w-24" />
                        <Skeleton className="h-3 w-14" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : members.length > 0 ? (
                <div className="space-y-2">
                  {members.slice(0, 5).map((m) => {
                    const isSelf = String(m.user.id) === String(currentUserId);
                    const canManage = (['admin', 'owner'] as string[]).includes(org.user_role ?? '');
                    const isConfirming = confirmRemoveId === m.user.id;
                    const isRemoving = removingMemberId === m.user.id;
                    return (
                      <div key={m.id} className="flex items-center gap-3 group">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
                          <span className="text-white text-xs font-bold">
                            {getInitials(m.user.name || m.user.username)}
                          </span>
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            {m.user.name || m.user.username}
                            {isSelf && <span className="ml-1 text-[10px] text-gray-400">(you)</span>}
                          </p>
                          <p className="text-xs text-gray-400 truncate">{m.user.email}</p>
                        </div>

                        {isConfirming ? (
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              type="button"
                              onClick={() => handleRemoveMember(m.user.id)}
                              disabled={isRemoving}
                              className="text-[10px] font-medium px-2 py-0.5 rounded bg-red-500 text-white hover:bg-red-600 disabled:opacity-50 transition"
                            >
                              {isRemoving ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Remove'}
                            </button>
                            <button
                              type="button"
                              onClick={() => setConfirmRemoveId(null)}
                              className="text-[10px] text-gray-400 hover:text-gray-600 px-1 transition"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-1.5 shrink-0">
                            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full capitalize ${roleBadge(m.role)}`}>
                              {m.role}
                            </span>
                            {canManage && !isSelf && (
                              <button
                                type="button"
                                onClick={() => setConfirmRemoveId(m.user.id)}
                                title="Remove member"
                                className="opacity-0 group-hover:opacity-100 inline-flex items-center justify-center w-5 h-5 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 transition"
                              >
                                <Trash2 className="w-3 h-3" />
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {members.length > 5 && (
                    <p className="text-xs text-center text-gray-400 pt-1">
                      +{members.length - 5} more member{members.length - 5 !== 1 ? 's' : ''}
                    </p>
                  )}
                </div>
              ) : (
                <div className="py-4 flex flex-col items-center text-center">
                  <Users className="w-6 h-6 text-gray-300 mb-2" />
                  <p className="text-sm text-gray-400">No member data available.</p>
                </div>
              )}
            </div>

            {/* Settings Card — admin only */}
            {(['admin', 'owner'] as string[]).includes(org.user_role ?? '') && (
              <div className="bg-white rounded-xl border border-gray-200 p-6 shadow-sm">
                <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 mb-4">Settings</p>
                <div>
                  <p className="text-xs text-gray-500 mb-1">Max concurrent sessions per user</p>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={sessionCapDraft !== '' ? sessionCapDraft : (org.max_concurrent_sessions ?? 5)}
                      onChange={(e) => setSessionCapDraft(e.target.value)}
                      disabled={sessionCapSaving}
                      className="w-20 text-sm px-2 py-1.5 rounded-md border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#3CCED7] disabled:opacity-50"
                    />
                    <button
                      type="button"
                      disabled={sessionCapSaving || sessionCapDraft === '' || Number(sessionCapDraft) === org.max_concurrent_sessions}
                      onClick={async () => {
                        const cap = Number(sessionCapDraft);
                        if (!cap || cap < 1) return;
                        setSessionCapSaving(true);
                        try {
                          await OrganizationAPI.updateOrgSettings(org.id, { max_concurrent_sessions: cap });
                          setOrg((prev) => prev ? { ...prev, max_concurrent_sessions: cap } : prev);
                          setSessionCapDraft('');
                          toast.success('Session limit updated.');
                        } catch {
                          toast.error('Failed to update session limit.');
                        } finally {
                          setSessionCapSaving(false);
                        }
                      }}
                      className="px-3 py-1.5 text-xs font-medium rounded-md bg-[#3CCED7] text-white hover:opacity-90 transition disabled:opacity-40"
                    >
                      {sessionCapSaving ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Save'}
                    </button>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-1.5">
                    When a user exceeds this limit, their oldest session is automatically revoked.
                  </p>
                </div>
              </div>
            )}

          </div>
        </div>

      </div>

      {/* ── Last-org error modal ──────────────────────────────────────────── */}
      {showLastOrgModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/40" onClick={() => setShowLastOrgModal(false)} />
          <div className="relative bg-white rounded-xl shadow-xl max-w-sm w-full p-6 space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center shrink-0">
                <AlertTriangle className="w-5 h-5 text-red-500" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-gray-900">Cannot Delete Organization</h3>
                <p className="text-xs text-gray-500 mt-0.5">Deletion failed</p>
              </div>
            </div>
            <p className="text-sm text-gray-600 leading-relaxed">
              Every user must belong to at least one organization. You cannot delete your only organization.
            </p>
            <p className="text-sm text-gray-500">
              Please join or create another organization first, then try again.
            </p>
            <button
              type="button"
              onClick={() => setShowLastOrgModal(false)}
              className="w-full py-2 rounded-lg bg-gray-900 text-white text-sm font-medium hover:bg-gray-700 transition"
            >
              Got it
            </button>
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}

export default function OrganizationDetailPage() {
  return (
    <ProtectedRoute>
      <OrgDetailContent />
    </ProtectedRoute>
  );
}
