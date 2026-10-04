'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { Users, UserPlus, Mail, Shield, Trash2, Loader2, Crown, Check, X } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import InlineSelect from '@/components/tasks/detail/InlineSelect';
import {
  ProjectAPI,
  type ProjectMemberData,
  type ProjectInvitationData,
  type ProjectRoleOption,
} from '@/lib/api/projectApi';
import { useAuthStore } from '@/lib/authStore';
import { useDashboardTileControls } from '@/components/dashboard/builder/DashboardTileControls';

interface Props {
  projectId: number | string | null;
  projectName?: string | null;
}

type Tab = 'members' | 'invitations' | 'approvals';

const getInitials = (name?: string | null, fallback = '?'): string => {
  if (!name) return fallback;
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return fallback;
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
};

const formatDate = (iso?: string | null): string => {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  } catch {
    return iso;
  }
};

const getApiErrorMessage = (err: unknown, fallback: string): string => {
  const responseData = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
  if (!responseData) {
    return (err as { message?: string })?.message || fallback;
  }
  const roleError = responseData.role;
  if (Array.isArray(roleError) && roleError[0]) {
    return String(roleError[0]);
  }
  if (typeof roleError === 'string' && roleError) {
    return roleError;
  }
  if (typeof responseData.error === 'string' && responseData.error) {
    return responseData.error;
  }
  if (typeof responseData.detail === 'string' && responseData.detail) {
    return responseData.detail;
  }
  if (typeof responseData.message === 'string' && responseData.message) {
    return responseData.message;
  }
  return fallback;
};

export default function TeamManagementSection({ projectId, projectName }: Props) {
  const tileControls = useDashboardTileControls('project-team');
  const currentUser = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<Tab>('members');
  const [members, setMembers] = useState<ProjectMemberData[]>([]);
  const [invitations, setInvitations] = useState<ProjectInvitationData[]>([]);
  const [approvals, setApprovals] = useState<ProjectInvitationData[]>([]);
  const [roleOptions, setRoleOptions] = useState<ProjectRoleOption[]>([]);
  const [defaultRole, setDefaultRole] = useState<string>('member');
  const [loading, setLoading] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<string>('member');
  const [inviting, setInviting] = useState(false);
  const [busyMember, setBusyMember] = useState<number | null>(null);
  const [busyInvite, setBusyInvite] = useState<number | null>(null);

  const currentUserId = useMemo(() => {
    const raw = currentUser?.id;
    if (raw === null || raw === undefined) return null;
    const n = typeof raw === 'number' ? raw : Number(raw);
    return Number.isFinite(n) ? n : null;
  }, [currentUser?.id]);

  const isOwner = useMemo(() => {
    if (currentUserId === null) return false;
    const self = members.find((m) => m.user?.id === currentUserId);
    return self?.role === 'owner';
  }, [members, currentUserId]);

  const loadAll = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    const results = await Promise.allSettled([
      ProjectAPI.getAllProjectMembers(projectId),
      ProjectAPI.getPendingInvitations(projectId),
      ProjectAPI.getPendingInvitationApprovals(projectId),
      ProjectAPI.getProjectAvailableRoles(projectId),
    ]);
    if (results[0].status === 'fulfilled') setMembers(results[0].value);
    if (results[1].status === 'fulfilled') setInvitations(results[1].value);
    if (results[2].status === 'fulfilled') setApprovals(results[2].value);
    if (results[3].status === 'fulfilled') {
      setRoleOptions(results[3].value.roles);
      setDefaultRole(results[3].value.default_role);
      setInviteRole(results[3].value.default_role);
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!projectId || !inviteEmail.trim()) return;
    setInviting(true);
    try {
      await ProjectAPI.inviteProjectMember(projectId, {
        email: inviteEmail.trim(),
        role: inviteRole,
      });
      toast.success(`Invitation sent to ${inviteEmail.trim()}`);
      setInviteEmail('');
      setInviteRole(defaultRole);
      await loadAll();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed to send invitation'));
    } finally {
      setInviting(false);
    }
  };

  const handleRoleChange = async (member: ProjectMemberData, newRole: string) => {
    if (!projectId || member.role === newRole) return;
    setBusyMember(member.id);
    try {
      await ProjectAPI.updateProjectMemberRole(projectId, member.id, newRole);
      toast.success(`Role updated for ${member.user?.username || member.user?.email}`);
      await loadAll();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed to update role'));
    } finally {
      setBusyMember(null);
    }
  };

  const handleTransferOwner = async (member: ProjectMemberData) => {
    if (!projectId) return;
    const confirmed = window.confirm(
      `Transfer ownership to ${member.user?.username || member.user?.email}? You will be demoted to Team Leader.`,
    );
    if (!confirmed) return;
    setBusyMember(member.id);
    try {
      await ProjectAPI.updateProjectMemberRole(projectId, member.id, 'owner');
      toast.success('Ownership transferred');
      await loadAll();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed to transfer ownership'));
    } finally {
      setBusyMember(null);
    }
  };

  const handleRemove = async (member: ProjectMemberData) => {
    if (!projectId) return;
    const confirmed = window.confirm(
      `Remove ${member.user?.username || member.user?.email} from this project?`,
    );
    if (!confirmed) return;
    setBusyMember(member.id);
    try {
      await ProjectAPI.removeProjectMember(projectId, member.id);
      toast.success('Member removed');
      await loadAll();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed to remove member'));
    } finally {
      setBusyMember(null);
    }
  };

  const handleApprove = async (invitation: ProjectInvitationData) => {
    if (!projectId) return;
    setBusyInvite(invitation.id);
    try {
      await ProjectAPI.approveProjectInvitation(projectId, invitation.id);
      toast.success(`Invitation to ${invitation.email} approved`);
      await loadAll();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed to approve invitation'));
    } finally {
      setBusyInvite(null);
    }
  };

  const handleReject = async (invitation: ProjectInvitationData) => {
    if (!projectId) return;
    setBusyInvite(invitation.id);
    try {
      await ProjectAPI.rejectProjectInvitation(projectId, invitation.id);
      toast.success('Invitation rejected');
      await loadAll();
    } catch (err) {
      toast.error(getApiErrorMessage(err, 'Failed to reject invitation'));
    } finally {
      setBusyInvite(null);
    }
  };

  if (!projectId) return null;

  return (
    <Card
      data-overview-section="team-management"
      className="flex h-full min-h-0 flex-col border-[0.5px] border-gray-200 bg-white shadow-none"
    >
      <CardHeader className="shrink-0 pb-3 px-5 pt-5">
        <div className="flex flex-wrap items-center gap-2">
          {tileControls?.dragHandle}
          <Users className="w-4 h-4 text-gray-500" />
          <CardTitle className="text-sm font-semibold text-gray-900">
            {tileControls?.title ?? 'Team Management'}
          </CardTitle>
          {projectName && (
            <span className="text-xs text-gray-400">· {projectName}</span>
          )}
          <div className="ml-0 flex max-w-full items-center gap-1 overflow-x-auto rounded-md bg-gray-50 p-1 sm:ml-auto">
            <TabButton active={tab === 'members'} onClick={() => setTab('members')}>
              Members ({members.length})
            </TabButton>
            <TabButton active={tab === 'invitations'} onClick={() => setTab('invitations')}>
              Invitations ({invitations.length})
            </TabButton>
            <TabButton active={tab === 'approvals'} onClick={() => setTab('approvals')}>
              Approvals ({approvals.length})
            </TabButton>
          </div>
          {tileControls?.removeButton}
        </div>
      </CardHeader>

      <CardContent className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 pb-5">
        {isOwner && (
          <form
            onSubmit={handleInvite}
            className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border border-[#3CCED7]/20 bg-[#3CCED7]/5 p-3"
          >
            <UserPlus className="hidden w-4 h-4 shrink-0 text-[#3CCED7] lg:block" />
            <input
              type="email"
              required
              placeholder="name@company.com"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="h-9 min-w-0 flex-[1_1_180px] rounded-md border border-gray-200 bg-white px-3 text-sm outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
            />
            <div className="min-w-0 flex-[1_1_140px]">
              <InlineSelect
                ariaLabel="Invite role"
                value={inviteRole}
                onValueChange={setInviteRole}
                options={
                  roleOptions.length > 0
                    ? roleOptions.map((opt) => ({ value: opt.value, label: opt.label }))
                    : [{ value: 'member', label: 'member' }]
                }
              />
            </div>
            <button
              type="submit"
              disabled={inviting || !inviteEmail.trim()}
              className="inline-flex h-9 min-w-0 flex-[1_1_100px] items-center justify-center gap-1.5 rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-4 text-sm font-semibold text-white shadow-sm hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {inviting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Mail className="w-4 h-4" />}
              Invite
            </button>
          </form>
        )}

        {loading && members.length === 0 ? (
          <div className="flex items-center justify-center py-10 text-xs text-gray-400">
            <Loader2 className="w-4 h-4 animate-spin mr-2" /> Loading…
          </div>
        ) : tab === 'members' ? (
          <MembersList
            members={members}
            roleOptions={roleOptions}
            currentUserId={currentUserId ?? undefined}
            isOwner={isOwner}
            busyId={busyMember}
            onRoleChange={handleRoleChange}
            onTransferOwner={handleTransferOwner}
            onRemove={handleRemove}
          />
        ) : tab === 'invitations' ? (
          <InvitationsList invitations={invitations} />
        ) : (
          <ApprovalsList
            invitations={approvals}
            canAct={isOwner}
            busyId={busyInvite}
            onApprove={handleApprove}
            onReject={handleReject}
          />
        )}
      </CardContent>
    </Card>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`shrink-0 px-3 py-1 rounded-md text-xs font-medium transition-colors ${
        active
          ? 'bg-white text-gray-900 shadow-sm'
          : 'text-gray-500 hover:text-gray-800'
      }`}
    >
      {children}
    </button>
  );
}

function MembersList({
  members,
  roleOptions,
  currentUserId,
  isOwner,
  busyId,
  onRoleChange,
  onTransferOwner,
  onRemove,
}: {
  members: ProjectMemberData[];
  roleOptions: ProjectRoleOption[];
  currentUserId?: number;
  isOwner: boolean;
  busyId: number | null;
  onRoleChange: (m: ProjectMemberData, r: string) => void;
  onTransferOwner: (m: ProjectMemberData) => void;
  onRemove: (m: ProjectMemberData) => void;
}) {
  if (members.length === 0) {
    return <p className="text-xs text-gray-400 py-4 text-center">No members yet.</p>;
  }

  return (
    <ul className="divide-y divide-gray-100">
      {members.map((m) => {
        const displayName = m.user?.username || m.user?.email || `User #${m.user?.id}`;
        const isSelf = m.user?.id === currentUserId;
        const isMemberOwner = m.role === 'owner';
        // Primary owner (project.owner) can never be edited/removed.
        // Co-owners (role='owner' but not project.owner) can be managed.
        const isPrimaryOwner = m.user?.id === m.project?.owner?.id;
        const canEdit = isOwner && !(isMemberOwner && isPrimaryOwner);
        const busy = busyId === m.id;
        return (
          <li key={m.id} className="flex flex-wrap items-center gap-3 py-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
              <span className="text-white text-[11px] font-semibold">
                {getInitials(displayName)}
              </span>
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                <p className="text-sm font-medium text-gray-900 truncate">{displayName}</p>
                {isSelf && (
                  <span className="text-[10px] text-gray-400">(you)</span>
                )}
                {isMemberOwner && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                    <Crown className="w-2.5 h-2.5" />
                    owner
                  </span>
                )}
              </div>
              {m.user?.email && m.user.email !== displayName && (
                <p className="text-[11px] text-gray-400 truncate">{m.user.email}</p>
              )}
            </div>

            {canEdit ? (
              <div className="w-full sm:min-w-[150px] sm:w-auto">
                <InlineSelect
                  ariaLabel="Member role"
                  value={m.role}
                  disabled={busy}
                  onValueChange={(v) => onRoleChange(m, v)}
                  options={(() => {
                    const base = roleOptions.map((opt) => ({
                      value: opt.value,
                      label: opt.label,
                    }));
                    if (!base.some((o) => o.value === m.role)) {
                      base.unshift({ value: m.role, label: m.role });
                    }
                    return base;
                  })()}
                />
              </div>
            ) : (
              <span className="rounded-md bg-gray-50 px-2 py-1 text-xs text-gray-700">
                {m.role}
              </span>
            )}

            {canEdit && (
              <div className="flex w-full flex-wrap gap-2 sm:w-auto">
                <button
                  type="button"
                  onClick={() => onTransferOwner(m)}
                  disabled={busy}
                  title="Transfer ownership"
                  className="h-8 rounded-md border border-[#3CCED7]/40 bg-white px-2.5 text-xs text-[#1a9ba3] hover:bg-[#3CCED7]/10 disabled:opacity-50"
                >
                  Transfer
                </button>
                <button
                  type="button"
                  onClick={() => onRemove(m)}
                  disabled={busy}
                  title="Remove member"
                  className="h-8 w-8 rounded-md border border-rose-200 bg-white text-rose-600 hover:bg-rose-50 inline-flex items-center justify-center disabled:opacity-50"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function InvitationsList({ invitations }: { invitations: ProjectInvitationData[] }) {
  if (invitations.length === 0) {
    return <p className="text-xs text-gray-400 py-4 text-center">No pending invitations.</p>;
  }
  return (
    <ul className="divide-y divide-gray-100">
      {invitations.map((inv) => (
        <li key={inv.id} className="flex flex-wrap items-center gap-3 py-3">
          <Mail className="w-4 h-4 text-gray-400 shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-gray-900 truncate">{inv.email}</p>
            <p className="text-[11px] text-gray-400">
              {inv.role} · invited {formatDate(inv.created_at)}
              {inv.invited_by?.username && ` by ${inv.invited_by.username}`}
            </p>
          </div>
          {inv.accepted ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
              accepted
            </span>
          ) : inv.approved === false ? (
            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700">
              awaiting approval
            </span>
          ) : (
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-600">
              pending
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function ApprovalsList({
  invitations,
  canAct,
  busyId,
  onApprove,
  onReject,
}: {
  invitations: ProjectInvitationData[];
  canAct: boolean;
  busyId: number | null;
  onApprove: (inv: ProjectInvitationData) => void;
  onReject: (inv: ProjectInvitationData) => void;
}) {
  if (invitations.length === 0) {
    return <p className="text-xs text-gray-400 py-4 text-center">No invitations awaiting approval.</p>;
  }
  return (
    <ul className="divide-y divide-gray-100">
      {invitations.map((inv) => {
        const busy = busyId === inv.id;
        return (
          <li key={inv.id} className="flex flex-wrap items-center gap-3 py-3">
            <Shield className="w-4 h-4 text-amber-500 shrink-0" />
            <div className="min-w-0 flex-1">
              <p className="text-sm text-gray-900 truncate">{inv.email}</p>
              <p className="text-[11px] text-gray-400">
                {inv.role} · requested {formatDate(inv.created_at)}
                {inv.invited_by?.username && ` by ${inv.invited_by.username}`}
              </p>
            </div>
            {canAct && (
              <div className="flex w-full flex-wrap gap-2 sm:w-auto">
                <button
                  type="button"
                  onClick={() => onApprove(inv)}
                  disabled={busy}
                  className="h-8 inline-flex items-center gap-1 rounded-md bg-[#3CCED7]/10 px-2.5 text-xs font-medium text-[#1a9ba3] hover:bg-[#3CCED7]/15 disabled:opacity-50"
                >
                  <Check className="w-3.5 h-3.5" />
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => onReject(inv)}
                  disabled={busy}
                  className="h-8 inline-flex items-center gap-1 rounded-md bg-rose-50 px-2.5 text-xs font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-50"
                >
                  <X className="w-3.5 h-3.5" />
                  Reject
                </button>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
