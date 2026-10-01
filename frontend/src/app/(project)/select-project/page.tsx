'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import toast from 'react-hot-toast';
import { Search, Loader2, AlertCircle, Mail, BarChart2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import ProjectCard from '@/components/select-project/ProjectCard';
import CreateProjectCard from '@/components/select-project/CreateProjectCard';
import CreateProjectChoiceModal from '@/components/select-project/CreateProjectChoiceModal';
import QuickCreateProjectModal from '@/components/select-project/QuickCreateProjectModal';
import ChatFAB from '@/components/global-chat/ChatFAB';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import CrossProjectRollupWidget from '@/components/dashboard/CrossProjectRollupWidget';
import Modal from '@/components/ui/Modal';
import { useProjects } from '@/hooks/useProjects';
import { Id } from '@/types/common';
import { ProjectAPI, type ProjectInvitationData } from '@/lib/api/projectApi';
import { quickStartPathWithCreateProjectReturn } from '@/lib/quickStartReturn';

type CreateProjectFlow = 'closed' | 'chooser' | 'classic';

export default function SelectProjectPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    projects,
    loading,
    error,
    fetchProjects,
    setActiveProject,
    deleteProject,
    deletingProjectId,
  } = useProjects();
  const [search, setSearch] = useState('');
  const [defaultProjectId, setDefaultProjectId] = useState<Id | null>(null);
  const [pendingInvites, setPendingInvites] = useState<ProjectInvitationData[]>([]);
  const [invitesLoading, setInvitesLoading] = useState(false);
  const [invitesError, setInvitesError] = useState<string | null>(null);
  const [acceptingInviteId, setAcceptingInviteId] = useState<number | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<{ id: Id; name: string } | null>(null);
  const [createFlow, setCreateFlow] = useState<CreateProjectFlow>('closed');
  const [compareOpen, setCompareOpen] = useState(false);

  useEffect(() => {
    if (searchParams.get('create') !== 'chooser') return;
    setCreateFlow('chooser');
    router.replace('/select-project');
  }, [searchParams, router]);

  const loadPendingInvites = useCallback(async () => {
    try {
      setInvitesLoading(true);
      setInvitesError(null);
      const data = await ProjectAPI.getMyPendingInvitations();
      setPendingInvites(data || []);
    } catch (err: any) {
      // `any` retained because axios errors have a layered shape
      // (err.response.data.detail/message) that is not surfaced through unknown
      const message =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        'Failed to load invitations.';
      setInvitesError(message);
    } finally {
      setInvitesLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();
    loadPendingInvites();
  }, [fetchProjects, loadPendingInvites]);

  useEffect(() => {
    const current = projects.find((p) => p.is_active);

    if (current) {
      setDefaultProjectId(current.id);
      return;
    }

    setDefaultProjectId((prev) => (prev !== null && projects.some((project) => String(project.id) === String(prev)) ? prev : null));
  }, [projects]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return projects
      .filter((p) => {
        if (!term) return true;
        const name = p.name?.toLowerCase() || '';
        const description = (p.description || '').toLowerCase();
        const owner = (p.owner?.name || p.owner?.email || '').toLowerCase();
        return name.includes(term) || description.includes(term) || owner.includes(term);
      })
      .sort((a, b) => {
        const at = new Date(a.updated_at || a.created_at || 0).getTime();
        const bt = new Date(b.updated_at || b.created_at || 0).getTime();
        return bt - at;
      });
  }, [projects, search]);

  const handleSelect = async (id: Id) => {
    const didUpdate = await setActiveProject(id, false);

    if (didUpdate !== false) {
      setDefaultProjectId(id);
    }

    return didUpdate;
  };

  const handleSetDefault = async (id: Id) => {
    const didUpdate = await setActiveProject(id, false);
    if (didUpdate !== false) {
      setDefaultProjectId(id);
    }
    return didUpdate;
  };

  // The second arg to setActiveProject is hard-coded `false` on this page:
  // the hook treats `true` as a toggle-off (local-only, no API call), which
  // is not the semantic we want here — card click / checkbox means
  // "make this my default" and must always POST /set_active/.

  const handleDelete = (id: Id, name: string) => {
    setDeleteConfirm({ id, name });
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    const { id } = deleteConfirm;
    await deleteProject(id);
    setDeleteConfirm(null);
  };

  const handleAcceptInvite = async (invite: ProjectInvitationData) => {
    if (!invite.token) {
      toast.error('Missing invitation token.');
      return;
    }
    if (!invite.approved) {
      toast.error('Invitation is pending approval.');
      return;
    }
    try {
      setAcceptingInviteId(invite.id);
      await ProjectAPI.acceptInvitation(invite.token);
      toast.success('Invitation accepted.');
      await fetchProjects();
      await loadPendingInvites();
    } catch (err: any) {
      // `any` retained because axios errors have a layered shape
      // (err.response.data.detail/message) that is not surfaced through unknown
      const message =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        'Failed to accept invitation.';
      toast.error(message);
    } finally {
      setAcceptingInviteId(null);
    }
  };

  const renderPendingInvites = () => {
    if (invitesLoading && pendingInvites.length === 0) {
      return (
        <div className="mb-6 flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-3 text-sm text-gray-500">
          <Loader2 className="w-4 h-4 animate-spin text-[#3CCED7]" />
          Loading invitations...
        </div>
      );
    }
    if (invitesError) {
      return (
        <div className="mb-6 flex items-center justify-between rounded-lg border border-red-200 bg-white px-4 py-3 text-sm text-red-600">
          <span>{invitesError}</span>
          <button
            onClick={loadPendingInvites}
            className="rounded-full bg-red-600 px-3 py-1 text-xs font-semibold text-white hover:bg-red-700"
          >
            Retry
          </button>
        </div>
      );
    }
    if (pendingInvites.length === 0) return null;

    return (
      <div className="mb-6 rounded-lg border border-[#3CCED7]/30 bg-white p-4">
        <div className="flex items-center gap-2 mb-3">
          <Mail className="w-4 h-4 text-[#3CCED7]" />
          <span className="text-sm font-medium text-gray-900">
            Pending invitations ({pendingInvites.length})
          </span>
        </div>
        <div className="space-y-2">
          {pendingInvites.map((invite) => (
            <div
              key={invite.id}
              className="flex flex-col gap-2 rounded-md border border-gray-100 bg-gray-50/60 px-3 py-2 text-sm md:flex-row md:items-center md:justify-between"
            >
              <div>
                <span className="font-medium text-gray-900">
                  {invite.project?.name || 'Project'}
                </span>
                <span className="mx-2 text-gray-300">·</span>
                <span className="text-gray-500">Role: {invite.role}</span>
                {!invite.approved && (
                  <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                    Pending approval
                  </span>
                )}
                <p className="text-[12px] text-gray-400 mt-0.5">
                  Invited by {invite.invited_by?.name || invite.invited_by?.email || 'Owner'}
                </p>
              </div>
              <button
                onClick={() => handleAcceptInvite(invite)}
                disabled={acceptingInviteId === invite.id || !invite.approved}
                className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {acceptingInviteId === invite.id && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Accept
              </button>
            </div>
          ))}
        </div>
      </div>
    );
  };

  return (
    <DashboardLayout alerts={[]} upcomingMeetings={[]}>
      <div className="max-w-[1200px] mx-auto w-full">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-gray-900 mb-1">
            Select a Project
          </h1>
          <p className="text-sm text-gray-500">
            Switch to another project to view its overview, campaigns, and performance data.
          </p>
        </div>

        {renderPendingInvites()}

        <div className="mb-6 flex items-center justify-between gap-3">
          <div className="max-w-sm w-full relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <Input
              placeholder="Search by name, owner, or description"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9 h-9 text-sm border-gray-200 focus:border-[#3CCED7] focus:ring-[#3CCED7]/20"
            />
          </div>
          {projects.length > 1 && (
            <button
              type="button"
              onClick={() => setCompareOpen(true)}
              className="flex items-center gap-2 h-9 px-4 rounded-lg text-sm font-medium text-white bg-gradient-to-r from-[#3CCED7] to-[#A6E661] hover:opacity-90 transition-opacity whitespace-nowrap flex-shrink-0"
            >
              <BarChart2 className="w-4 h-4" />
              Compare projects
            </button>
          )}
        </div>

        {loading && projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-gray-500">
            <Loader2 className="w-6 h-6 animate-spin text-[#3CCED7] mb-2" />
            <p className="text-sm">Loading your projects...</p>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-16 text-gray-500">
            <AlertCircle className="w-6 h-6 text-red-500 mb-2" />
            <p className="text-sm text-red-600 mb-3">{error}</p>
            <button
              onClick={() => fetchProjects()}
              className="px-3 py-1.5 rounded-full text-xs font-semibold bg-[#3CCED7] text-white hover:opacity-90"
            >
              Retry
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filtered.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                isDefault={defaultProjectId !== null && String(defaultProjectId) === String(project.id)}
                onSetDefault={handleSetDefault}
                onSelect={handleSelect}
                onDelete={handleDelete}
                deleting={deletingProjectId === project.id}
              />
            ))}
            <CreateProjectCard onClick={() => setCreateFlow('chooser')} />
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="text-center py-16">
            <p className="text-sm text-gray-400">No projects match your search.</p>
          </div>
        )}

        {compareOpen && (
          <Modal isOpen={compareOpen} onClose={() => setCompareOpen(false)}>
            <div className="w-[min(1100px,calc(100vw-2rem))] max-h-[calc(100vh-4rem)] overflow-y-auto rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <CrossProjectRollupWidget
                projects={projects}
                onSelectProject={async (id) => {
                  setCompareOpen(false);
                  await handleSelect(id);
                }}
              />
            </div>
          </Modal>
        )}
      </div>

      <CreateProjectChoiceModal
        open={createFlow === 'chooser'}
        onClose={() => setCreateFlow('closed')}
        onQuickStart={() => {
          router.push(quickStartPathWithCreateProjectReturn());
        }}
        onClassic={() => setCreateFlow('classic')}
      />

      <QuickCreateProjectModal
        open={createFlow === 'classic'}
        onClose={() => setCreateFlow('closed')}
        onExit={() => setCreateFlow('chooser')}
        onCreated={async () => {
          await fetchProjects();
        }}
      />

      {deleteConfirm && (
        <Modal
          isOpen={true}
          onClose={() => {
            if (deletingProjectId !== deleteConfirm.id) setDeleteConfirm(null);
          }}
        >
          <div className="w-[min(420px,calc(100vw-2rem))]">
            <div className="rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="px-6 pt-6 pb-4 border-b border-gray-100">
                <h2 className="text-lg font-semibold text-gray-900">Delete project</h2>
                <p className="text-sm text-gray-600 mt-1">
                  Delete &quot;{deleteConfirm.name}&quot;? This cannot be undone.
                </p>
              </div>
              <div className="p-6 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setDeleteConfirm(null)}
                  disabled={deletingProjectId === deleteConfirm.id}
                  className="rounded border border-gray-200 px-3 py-1.5 text-sm font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={confirmDelete}
                  disabled={deletingProjectId === deleteConfirm.id}
                  className="rounded bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60"
                >
                  {deletingProjectId === deleteConfirm.id ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            </div>
          </div>
        </Modal>
      )}

      <ChatFAB />
    </DashboardLayout>
  );
}
