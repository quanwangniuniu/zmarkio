'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Briefcase,
  Building2,
  Check,
  ChevronRight,
  ClipboardList,
  Camera,
  Loader2,
  MapPin,
  Monitor,
  Network,
  Pencil,
  X,
} from 'lucide-react';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import PrivacyExportPanel from '@/components/profile/PrivacyExportPanel';
import useAuth from '@/hooks/useAuth';
import { useAuthStore } from '@/lib/authStore';
import { useRouter } from 'next/navigation';
import toast from 'react-hot-toast';
import { authAPI, readPersistedAuthState } from '@/lib/api';
import { Skeleton } from '@/components/ui/skeleton';
import { OrganizationAPI, OrgListItem } from '@/lib/api/organizationApi';

type SectionKey = 'overview' | 'organization' | 'privacy' | 'sessions';

const SECTIONS: Array<{ id: SectionKey; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'organization', label: 'Organization' },
  { id: 'privacy', label: 'Privacy' },
  { id: 'sessions', label: 'Active Sessions' },
];

const getInitials = (name?: string | null): string => {
  if (!name) return '?';
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
};

const humanize = (s: string) =>
  s.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

function parseUserAgent(ua: string): { browser: string; os: string } {
  const browser =
    /Edg\//.test(ua) ? 'Edge' :
    /OPR\//.test(ua) ? 'Opera' :
    /Chrome\//.test(ua) ? 'Chrome' :
    /Firefox\//.test(ua) ? 'Firefox' :
    /Safari\//.test(ua) ? 'Safari' :
    'Unknown browser';

  const os =
    /iPhone/.test(ua) ? 'iPhone' :
    /iPad/.test(ua) ? 'iPad' :
    /Android/.test(ua) ? 'Android' :
    /Windows/.test(ua) ? 'Windows' :
    /Mac OS/.test(ua) ? 'macOS' :
    /Linux/.test(ua) ? 'Linux' :
    'Unknown OS';

  return { browser, os };
}

// ── Inline editable field ────────────────────────────────────────────────────

interface EditableFieldProps {
  label: string;
  value: string;
  placeholder?: string;
  saving?: boolean;
  onSave: (val: string) => Promise<void>;
}

function EditableField({ label, value, placeholder = '—', saving, onSave }: EditableFieldProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const handleSave = async () => {
    if (draft.trim() === value) { setEditing(false); return; }
    await onSave(draft.trim());
    setEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') { setDraft(value); setEditing(false); }
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1.5">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          className="flex-1 text-sm border border-[#3CCED7] rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#3CCED7]/50"
        />
        <button onClick={handleSave} disabled={saving} className="text-[#3CCED7] hover:opacity-80">
          {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
        </button>
        <button onClick={() => { setDraft(value); setEditing(false); }} className="text-gray-400 hover:opacity-80">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => setEditing(true)}
      className="group flex items-center gap-1 text-sm text-gray-900 hover:text-[#3CCED7] transition-colors text-left"
    >
      <span>{value || placeholder}</span>
      <Pencil className="w-3 h-3 text-gray-300 group-hover:text-[#3CCED7] opacity-0 group-hover:opacity-100 transition-opacity" />
    </button>
  );
}

// ── About row ───────────────────────────────────────────────────────────────

interface AboutRowProps {
  icon: React.ElementType;
  label: string;
  placeholder: string;
  value: string;
  saving?: boolean;
  onSave: (val: string) => Promise<void>;
}

function AboutRow({ icon: Icon, label, placeholder, value, saving, onSave }: AboutRowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { setDraft(value); }, [value]);
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const handleSave = async () => {
    if (draft.trim() === value) { setEditing(false); return; }
    await onSave(draft.trim());
    setEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') { setDraft(value); setEditing(false); }
  };

  return (
    <div className={`group rounded-md transition-colors ${editing ? 'bg-[#3CCED7]/5' : 'hover:bg-gray-50'}`}>
      {editing ? (
        <div className="flex items-center gap-2 p-2">
          <Icon className="h-4 w-4 text-[#3CCED7] shrink-0" />
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="flex-1 text-sm border border-[#3CCED7] rounded px-2 py-0.5 focus:outline-none focus:ring-1 focus:ring-[#3CCED7]/50"
          />
          <button onClick={handleSave} disabled={saving} className="text-[#3CCED7] hover:opacity-80">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="h-4 w-4" />}
          </button>
          <button onClick={() => { setDraft(value); setEditing(false); }} className="text-gray-400 hover:opacity-80">
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="w-full flex items-center gap-3 px-2 py-2 text-left"
        >
          <Icon className="h-4 w-4 text-gray-400 group-hover:text-[#3CCED7] shrink-0 transition-colors" />
          <div className="min-w-0 flex-1">
            <div className="text-[11px] uppercase tracking-wider text-gray-400">{label}</div>
            <div className={`text-sm truncate ${value ? 'text-gray-900' : 'text-gray-400'}`}>
              {value || placeholder}
            </div>
          </div>
          <Pencil className="w-3 h-3 text-gray-300 group-hover:text-[#3CCED7] opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
        </button>
      )}
    </div>
  );
}

// ── Main content ─────────────────────────────────────────────────────────────

function ProfileContent() {
  const { user, loading, logout } = useAuth();
  const setUser = useAuthStore((s) => s.setUser);
  const router = useRouter();
  const [activeSection, setActiveSection] = useState<SectionKey>('overview');
  const [saving, setSaving] = useState(false);
  const [avatarUploading, setAvatarUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Delete account modal
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  // Organization / projects
  const [projects, setProjects] = useState<{ project_id: number; project_name: string; role: string }[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [orgs, setOrgs] = useState<OrgListItem[]>([]);
  const [orgsLoading, setOrgsLoading] = useState(false);

  // Active sessions
  const [sessions, setSessions] = useState<{ jti: string; ip: string; user_agent: string; created_at: string }[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [revokingJti, setRevokingJti] = useState<string | null>(null);

  const profileLoading = loading || !user;

  const displayName = useMemo(() => {
    if (!user) return 'User';
    const full = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
    return full || user.username || user.email?.split('@')[0] || 'User';
  }, [user]);

  const primaryRole = useMemo(() => {
    const roles = Array.isArray(user?.roles) ? user!.roles : [];
    return roles[0] ?? null;
  }, [user]);

  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      const data = await authAPI.getMyProjects();
      setProjects(data);
    } catch {
      toast.error('Failed to load projects.');
    } finally {
      setProjectsLoading(false);
    }
  }, []);

  const loadOrgs = useCallback(async () => {
    setOrgsLoading(true);
    try {
      const data = await OrganizationAPI.getUserOrganizations();
      setOrgs(data.organizations);
    } catch {
      toast.error('Failed to load organizations.');
    } finally {
      setOrgsLoading(false);
    }
  }, []);

  const loadSessions = useCallback(async () => {
    setSessionsLoading(true);
    try {
      const data = await authAPI.getSessions();
      setSessions(data);
    } catch {
      toast.error('Failed to load sessions.');
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  const handleRevokeSession = async (jti: string) => {
    setRevokingJti(jti);
    try {
      await authAPI.revokeSession(jti);
      setSessions((prev) => prev.filter((s) => s.jti !== jti));
      toast.success('Session revoked.', { position: 'top-center' });
    } catch {
      toast.error('Failed to revoke session.');
    } finally {
      setRevokingJti(null);
    }
  };

  useEffect(() => {
    if (activeSection === 'organization') {
      loadProjects();
      loadOrgs();
    }
    if (activeSection === 'sessions') {
      loadSessions();
    }
  }, [activeSection, loadProjects, loadOrgs, loadSessions]);

  // ── Patch helper ─────────────────────────────────────────────────────────

  const patchProfile = async (data: Record<string, string>) => {
    setSaving(true);
    try {
      const updated = await authAPI.updateProfile(data);
      setUser(updated);
      toast.success('Saved.');
    } catch (e: any) {
      const msg = e?.response?.data?.username?.[0] || e?.response?.data?.detail || 'Failed to save.';
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  // ── Avatar upload ─────────────────────────────────────────────────────────

  const handleAvatarChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('avatar', file);
    setAvatarUploading(true);
    try {
      const updated = await authAPI.updateProfile(fd);
      setUser(updated);
      toast.success('Avatar updated.');
    } catch (e: any) {
      const msg = e?.response?.data?.avatar?.[0] || 'Failed to upload avatar.';
      toast.error(msg);
    } finally {
      setAvatarUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  // ── Delete account ────────────────────────────────────────────────────────

  const handleDeleteAccount = async () => {
    if (deleteConfirmText !== 'DELETE MY ACCOUNT') return;
    setIsDeleting(true);
    try {
      // Note: the persisted field is `refreshToken`, not `refresh` (fixed pre-existing typo)
      const refreshToken = readPersistedAuthState()?.state?.refreshToken ?? '';
      await authAPI.deleteAccount(refreshToken);
      toast.success('Your account has been deleted.');
      await logout();
      router.replace('/login');
    } catch (e) {
      const axiosError = e as { response?: { data?: { error?: string; detail?: string } } };
      const message = axiosError.response?.data?.error ?? axiosError.response?.data?.detail ?? (e instanceof Error ? e.message : 'Failed to delete account');
      toast.error(message);
    } finally {
      setIsDeleting(false);
      setIsDeleteModalOpen(false);
    }
  };

  // ── Render overview tab ───────────────────────────────────────────────────

  const renderOverview = () => (
    <div className="space-y-6">
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500 mb-3">Account</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-4">
          {/* First name */}
          <div>
            <div className="text-xs text-gray-500 mb-1">First name</div>
            {profileLoading ? (
              <Skeleton className="h-4 w-32" />
            ) : (
              <EditableField
                label="First name"
                value={user?.first_name ?? ''}
                placeholder="Add first name"
                saving={saving}
                onSave={(val) => patchProfile({ first_name: val })}
              />
            )}
          </div>

          {/* Last name */}
          <div>
            <div className="text-xs text-gray-500 mb-1">Last name</div>
            {profileLoading ? (
              <Skeleton className="h-4 w-32" />
            ) : (
              <EditableField
                label="Last name"
                value={user?.last_name ?? ''}
                placeholder="Add last name"
                saving={saving}
                onSave={(val) => patchProfile({ last_name: val })}
              />
            )}
          </div>

          {/* Email — read-only */}
          <div>
            <div className="text-xs text-gray-500 mb-1">Email</div>
            {profileLoading ? (
              <Skeleton className="h-4 w-44" />
            ) : (
              <div className="text-sm text-gray-900 break-all">{user?.email ?? '—'}</div>
            )}
          </div>

          {/* Username */}
          <div>
            <div className="text-xs text-gray-500 mb-1">Username</div>
            {profileLoading ? (
              <Skeleton className="h-4 w-24" />
            ) : (
              <EditableField
                label="Username"
                value={user?.username ?? ''}
                saving={saving}
                onSave={(val) => patchProfile({ username: val })}
              />
            )}
          </div>

          {/* Primary role — read-only */}
          <div>
            <div className="text-xs text-gray-500 mb-1">Primary role</div>
            {profileLoading ? (
              <Skeleton className="h-4 w-20" />
            ) : (
              <div className="text-sm text-gray-900">{primaryRole ?? '—'}</div>
            )}
          </div>
        </div>
      </div>

      {/* Recent Activity */}
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500 mb-3">Recent Activity</h2>
        <div className="rounded-lg border border-dashed border-gray-200 py-10 flex flex-col items-center text-center">
          <div className="w-10 h-10 rounded-full bg-gray-100 flex items-center justify-center mb-3">
            <ClipboardList className="w-5 h-5 text-gray-400" />
          </div>
          <p className="text-sm text-gray-500">No recent activity yet.</p>
          <p className="text-xs text-gray-400 mt-1">Your recent tasks and decisions will show up here.</p>
        </div>
      </div>
    </div>
  );

  // ── Role badge style ──────────────────────────────────────────────────────

  const roleBadgeClass = (role: string) => {
    const r = role.toLowerCase();
    if (r === 'owner') return 'bg-amber-50 text-amber-600 border-amber-200';
    if (r === 'viewer') return 'bg-gray-100 text-gray-500 border-gray-200';
    return 'bg-[#3CCED7]/10 text-[#3CCED7] border-[#3CCED7]/20'; // member / default
  };

  // ── Render organization tab ───────────────────────────────────────────────

  const getOrgInitials = (name: string) => {
    const words = name.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return '?';
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
  };

  const renderOrganization = () => {
    // Group projects by role for clear multi-role display
    const roleOrder = ['owner', 'member', 'viewer'];
    const grouped = projects.reduce<Record<string, typeof projects>>((acc, p) => {
      const key = p.role.toLowerCase();
      if (!acc[key]) acc[key] = [];
      acc[key].push(p);
      return acc;
    }, {});
    const sortedRoles = Object.keys(grouped).sort(
      (a, b) => (roleOrder.indexOf(a) === -1 ? 99 : roleOrder.indexOf(a)) - (roleOrder.indexOf(b) === -1 ? 99 : roleOrder.indexOf(b))
    );

    return (
      <div className="space-y-5">
        {/* Organizations list */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500">
              My Organizations
            </h2>
            {!orgsLoading && orgs.length > 0 && (
              <span className="text-xs text-gray-400">{orgs.length} organization{orgs.length !== 1 ? 's' : ''}</span>
            )}
          </div>

          {orgsLoading ? (
            <div className="space-y-2">
              {[1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-gray-100">
                  <Skeleton className="h-9 w-9 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3.5 w-36" />
                    <Skeleton className="h-3 w-20" />
                  </div>
                  <Skeleton className="h-3.5 w-3.5 rounded" />
                </div>
              ))}
            </div>
          ) : orgs.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-200 py-8 flex flex-col items-center text-center">
              <Building2 className="w-7 h-7 text-gray-300 mb-2" />
              <p className="text-sm text-gray-500">No organizations found.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {orgs.map((org) => (
                <button
                  key={org.id}
                  type="button"
                  onClick={() => router.push(`/organizations/${org.slug ?? org.id}`)}
                  className="w-full flex items-center gap-3 p-3 rounded-lg border border-gray-100 hover:border-[#3CCED7]/40 hover:bg-[#3CCED7]/5 transition-colors text-left group"
                >
                  <div className="w-9 h-9 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
                    <span className="text-white text-xs font-bold">{getOrgInitials(org.name)}</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-gray-900 truncate">{org.name}</span>
                      {org.is_current && (
                        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-purple-100 text-purple-600">
                          Current
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-gray-400 truncate">
                      {org.user_role && <span className="capitalize">{org.user_role}</span>}
                      {org.user_role && <span className="mx-1">·</span>}
                      <span>{org.member_count} member{org.member_count !== 1 ? 's' : ''}</span>
                    </div>
                  </div>
                  <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-[#3CCED7] shrink-0 transition-colors" />
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Projects */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500">
              Projects &amp; Roles
            </h2>
            {!projectsLoading && projects.length > 0 && (
              <span className="text-xs text-gray-400">{projects.length} project{projects.length !== 1 ? 's' : ''}</span>
            )}
          </div>

          {projectsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-gray-100">
                  <Skeleton className="h-8 w-8 rounded-md" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-3.5 w-32" />
                    <Skeleton className="h-3 w-16" />
                  </div>
                </div>
              ))}
            </div>
          ) : projects.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-200 py-10 flex flex-col items-center text-center">
              <Building2 className="w-8 h-8 text-gray-300 mb-2" />
              <p className="text-sm text-gray-500">No project memberships yet.</p>
            </div>
          ) : (
            <div className="space-y-4">
              {sortedRoles.map((role) => (
                <div key={role}>
                  {sortedRoles.length > 1 && (
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${roleBadgeClass(role)}`}>
                        {humanize(role)}
                      </span>
                      <span className="text-[11px] text-gray-400">{grouped[role].length} project{grouped[role].length !== 1 ? 's' : ''}</span>
                    </div>
                  )}
                  <div className="space-y-2">
                    {grouped[role].map((p) => (
                      <div key={p.project_id} className="flex items-center gap-3 p-3 rounded-lg border border-gray-100 hover:border-[#3CCED7]/30 transition-colors">
                        <div className="w-8 h-8 rounded-md bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center shrink-0">
                          <span className="text-white text-xs font-bold">
                            {p.project_name[0]?.toUpperCase()}
                          </span>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-gray-900 truncate">{p.project_name}</div>
                        </div>
                        <span className={`shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full border ${roleBadgeClass(role)}`}>
                          {humanize(p.role)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  };

  const renderSessions = () => (
    <div className="space-y-3">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-500">Active Sessions</h2>
        {!sessionsLoading && (
          <span className="text-xs text-gray-400">{sessions.length} session{sessions.length !== 1 ? 's' : ''}</span>
        )}
      </div>

      {sessionsLoading ? (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-gray-100">
              <Skeleton className="h-8 w-8 rounded-md" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-3 w-24" />
              </div>
              <Skeleton className="h-7 w-16 rounded-md" />
            </div>
          ))}
        </div>
      ) : sessions.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-10 flex flex-col items-center text-center">
          <Monitor className="w-8 h-8 text-gray-300 mb-2" />
          <p className="text-sm text-gray-500">No active sessions found.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {sessions.map((session) => {
            const { browser, os } = parseUserAgent(session.user_agent || '');
            return (
              <div key={session.jti} className="flex items-center gap-3 p-3 rounded-lg border border-gray-100">
                <div className="w-8 h-8 rounded-md bg-gray-100 flex items-center justify-center shrink-0">
                  <Monitor className="w-4 h-4 text-gray-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-gray-900">{browser} on {os}</div>
                  <div className="text-xs text-gray-400">
                    {session.ip} · {new Date(session.created_at).toLocaleString()}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => handleRevokeSession(session.jti)}
                  disabled={revokingJti === session.jti}
                  className="shrink-0 px-3 py-1.5 text-xs font-medium text-red-600 border border-red-200 rounded-md hover:bg-red-50 transition-colors disabled:opacity-50"
                >
                  {revokingJti === session.jti ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Revoke'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  const renderActiveSection = () => {
    if (activeSection === 'overview') return renderOverview();
    if (activeSection === 'organization') return renderOrganization();
    if (activeSection === 'sessions') return renderSessions();
    return <PrivacyExportPanel />;
  };

  // ── Layout ────────────────────────────────────────────────────────────────

  return (
    <DashboardLayout>
      <div className="p-6">
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          {/* Brand strip */}
          <div className="h-16 bg-gradient-to-r from-[#3CCED7]/20 via-white to-[#A6E661]/20" />

          <div className="px-8 pb-8">
            {/* Identity header */}
            <div className="-mt-8 flex items-end justify-between gap-4 flex-wrap">
              <div className="flex items-end gap-4">
                {/* Avatar */}
                <div className="relative group">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/gif"
                    className="hidden"
                    onChange={handleAvatarChange}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={avatarUploading}
                    className="w-16 h-16 rounded-full shadow-sm ring-4 ring-white overflow-hidden flex items-center justify-center focus:outline-none"
                    title="Change avatar"
                  >
                    {profileLoading ? (
                      <Skeleton className="h-16 w-16 rounded-full" />
                    ) : user?.avatar ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={user.avatar}
                        alt="Avatar"
                        className="object-cover w-full h-full"
                      />
                    ) : (
                      <div className="w-full h-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] flex items-center justify-center">
                        <span className="text-white text-lg font-semibold">{getInitials(displayName)}</span>
                      </div>
                    )}
                    {/* Hover overlay */}
                    {!profileLoading && (
                      <div className="absolute inset-0 rounded-full bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                        {avatarUploading ? (
                          <Loader2 className="w-5 h-5 text-white animate-spin" />
                        ) : (
                          <Camera className="w-5 h-5 text-white" />
                        )}
                      </div>
                    )}
                  </button>
                </div>

                <div className="pb-1">
                  <div className="text-lg font-semibold text-gray-900 leading-tight">
                    {profileLoading ? <Skeleton className="h-6 w-40" /> : displayName}
                  </div>
                  <div className="text-sm text-gray-500 mt-0.5">
                    {profileLoading ? <Skeleton className="h-4 w-56" /> : (user?.email ?? '')}
                  </div>
                </div>
              </div>

              {!profileLoading && primaryRole && (
                <span className="inline-flex items-center rounded-full border border-[#3CCED7]/30 bg-[#3CCED7]/5 px-2.5 py-0.5 text-xs font-medium text-[#3CCED7]">
                  {primaryRole}
                </span>
              )}
              {profileLoading && <Skeleton className="h-6 w-20 rounded-full" />}
            </div>

            <div className="mt-8 flex items-start gap-6 w-full">
              {/* LEFT: About + Danger Zone */}
              <div className="w-[30%] min-w-[280px] max-w-[380px] flex flex-col gap-3 shrink-0">
                <section className="w-full rounded-lg border border-gray-200 bg-white p-5">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-4">About</h3>

                  <div className="space-y-1">
                    {profileLoading ? (
                      [1, 2, 3, 4].map((i) => (
                        <div key={i} className="rounded-md px-2 py-2 flex items-center gap-3">
                          <Skeleton className="h-4 w-4 rounded-sm" />
                          <div className="flex-1 space-y-2">
                            <Skeleton className="h-3 w-16" />
                            <Skeleton className="h-4 w-28" />
                          </div>
                        </div>
                      ))
                    ) : (
                      <>
                        <AboutRow
                          icon={Briefcase}
                          label="Role"
                          placeholder="Add your role"
                          value={user?.job ?? ''}
                          saving={saving}
                          onSave={(val) => patchProfile({ job: val })}
                        />
                        <AboutRow
                          icon={Network}
                          label="Department"
                          placeholder="Add your department"
                          value={user?.department ?? ''}
                          saving={saving}
                          onSave={(val) => patchProfile({ department: val })}
                        />
                        <AboutRow
                          icon={MapPin}
                          label="Location"
                          placeholder="Add your location"
                          value={user?.location ?? ''}
                          saving={saving}
                          onSave={(val) => patchProfile({ location: val })}
                        />
                      </>
                    )}
                  </div>

                  {/* Contact */}
                  <div className="mt-5 pt-4 border-t border-gray-100">
                    <div className="text-[11px] uppercase tracking-wider text-gray-400 mb-1">Contact</div>
                    {profileLoading ? (
                      <div className="flex items-center gap-3 px-2 py-1.5">
                        <Skeleton className="h-4 w-4 rounded-sm" />
                        <Skeleton className="h-4 w-44" />
                      </div>
                    ) : (
                      <div className="flex items-center gap-3 px-2 py-1.5 text-sm text-gray-700">
                        <span className="truncate">{user?.email ?? '—'}</span>
                      </div>
                    )}
                  </div>
                </section>

                {!profileLoading && (
                  <section className="w-full rounded-lg border border-red-200 bg-white p-5">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-red-500 mb-3">
                      Danger Zone
                    </h3>
                    <p className="text-xs text-gray-500 mb-3">
                      Permanently remove your account and personal data. Projects and tasks you created will be kept.
                    </p>
                    <button
                      type="button"
                      onClick={() => { setDeleteConfirmText(''); setIsDeleteModalOpen(true); }}
                      className="w-full px-3 py-2 text-sm font-medium text-red-600 border border-red-300 rounded-lg hover:bg-red-50 transition-colors"
                    >
                      Delete Account
                    </button>
                  </section>
                )}
              </div>

              {/* RIGHT: tabs */}
              <div className="flex-1 min-w-0">
                <div className="rounded-lg border border-gray-200 bg-white">
                  <div className="border-b border-gray-200">
                    <nav className="flex gap-6 px-5" aria-label="Profile sections">
                      {SECTIONS.map((section) => {
                        const active = activeSection === section.id;
                        return (
                          <button
                            key={section.id}
                            type="button"
                            onClick={() => setActiveSection(section.id)}
                            className={`py-3 text-sm font-medium border-b-2 transition-colors -mb-px ${
                              active
                                ? 'border-[#3CCED7] text-[#3CCED7]'
                                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                            }`}
                            aria-current={active ? 'page' : undefined}
                          >
                            {section.label}
                          </button>
                        );
                      })}
                    </nav>
                  </div>
                  <div className="p-6">
                    {renderActiveSection()}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Delete Account Modal */}
      {isDeleteModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-white rounded-2xl shadow-xl p-8 w-full max-w-md mx-4">
            <div className="flex items-center gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-red-600 shrink-0" />
              <h2 className="text-xl font-bold text-gray-900">Delete Your Account</h2>
            </div>
            <p className="text-sm text-gray-600 mb-2">
              This action is <span className="font-semibold text-red-600">irreversible</span>. The following data will be permanently removed:
            </p>
            <ul className="text-sm text-gray-600 list-disc list-inside mb-4 space-y-1">
              <li>Your profile and login credentials</li>
              <li>Team and project memberships</li>
              <li>Role assignments and permissions</li>
              <li>Notification settings and integrations</li>
            </ul>
            <p className="text-sm text-gray-600 mb-4">
              Projects and tasks you created will <span className="font-semibold">remain</span> so your team can continue working on them.
            </p>
            <p className="text-sm font-medium text-gray-700 mb-2">
              Type <span className="font-mono bg-gray-100 px-1 rounded">DELETE MY ACCOUNT</span> to confirm:
            </p>
            <input
              type="text"
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder="DELETE MY ACCOUNT"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-red-400"
            />
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setIsDeleteModalOpen(false)}
                disabled={isDeleting}
                className="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteAccount}
                disabled={deleteConfirmText !== 'DELETE MY ACCOUNT' || isDeleting}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isDeleting ? 'Deleting...' : 'Permanently Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </DashboardLayout>
  );
}

export default function ProfilePage() {
  return (
    <ProtectedRoute>
      <ProfileContent />
    </ProtectedRoute>
  );
}
