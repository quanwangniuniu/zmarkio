'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Notebook, Plus, Search } from 'lucide-react';
import { toast } from 'react-hot-toast';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import NotionDraftCard from '@/components/notion/NotionDraftCard';
import ConfirmDialog from '@/components/tasks/detail/ConfirmDialog';
import { NotionDraftAPI } from '@/lib/api/notionDraftApi';
import { ProjectAPI } from '@/lib/api/projectApi';
import { useBuildUrl } from '@/lib/buildUrl';
import type { DraftStatus, DraftSummary } from '@/types/notion';

type StatusFilter = 'all' | 'draft' | 'published' | 'archived';

const FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'draft', label: 'Draft' },
  { key: 'published', label: 'Published' },
];

function NotionV2ListContent() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const params = useParams<{ projectSlug?: string | string[] }>();
  const projectSlug = Array.isArray(params?.projectSlug) ? params.projectSlug[0] : params?.projectSlug;
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [pendingDeleteId, setPendingDeleteId] = useState<number | string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const fetchDrafts = useCallback(async () => {
    setLoading(true);
    try {
      const data = await NotionDraftAPI.listDrafts();
      const safe = Array.isArray(data) ? data : [];
      setDrafts(safe.filter((d) => d && d.id !== undefined && d.id !== null));
    } catch (error) {
      console.error('Failed to load drafts', error);
      toast.error('Unable to load drafts');
      setDrafts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDrafts();
  }, [fetchDrafts]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return drafts.filter((d) => {
      if (filter !== 'all' && d.status !== filter) return false;
      if (!needle) return true;
      return (
        (d.title || '').toLowerCase().includes(needle) ||
        (d.user_email || '').toLowerCase().includes(needle)
      );
    });
  }, [drafts, filter, query]);

  const handleCreate = useCallback(async () => {
    if (creating) return;
    setCreating(true);
    try {
      const created = await NotionDraftAPI.createDraft({
        title: 'Untitled',
        status: 'draft' as DraftStatus,
        content_blocks: [
          { type: 'rich_text', content: { html: '' }, order: 0 },
        ],
      });
      if (!created?.id || !created.slug) {
        toast.error('Failed to create draft');
        return;
      }
      if (projectSlug) {
        try {
          const project = await ProjectAPI.getProject(projectSlug);
          const projectId = Number(project.id);
          if (!Number.isInteger(projectId) || projectId < 1) {
            throw new Error('Project id is missing');
          }
          await NotionDraftAPI.linkProject(created.slug, projectId);
          toast.success('Draft created');
        } catch (linkError) {
          console.error('Failed to link draft to project', linkError);
          toast.error('Draft created, but it could not be linked to this project.');
        }
      } else {
        toast.success('Draft created');
      }
      router.push(buildUrl(`/notion/${created.slug}`));
    } catch (error: any) {
      console.error('Failed to create draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to create draft');
    } finally {
      setCreating(false);
    }
  }, [creating, projectSlug, buildUrl, router]);

  const handleOpen = useCallback(
    (idOrSlug: number | string) => {
      router.push(buildUrl(`/notion/${idOrSlug}`));
    },
    [router]
  );

  const handleDuplicate = useCallback(
    async (id: number | string) => {
      try {
        const created = await NotionDraftAPI.duplicateDraft(id);
        if (!created?.id) throw new Error('Duplicated draft id missing');
        toast.success('Draft duplicated');
        router.push(buildUrl(`/notion/${created.slug}`));
      } catch (error: any) {
        console.error('Failed to duplicate draft', error);
        toast.error(error?.response?.data?.detail || 'Failed to duplicate draft');
      }
    },
    [router]
  );

  const handleExport = useCallback(async (id: number | string, title: string) => {
    try {
      const blob = await NotionDraftAPI.exportDraft(id);
      const safeName = (title || 'draft').replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '_') || 'draft';
      const url = window.URL.createObjectURL(new Blob([blob], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${safeName}_export.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Draft exported');
    } catch (error: any) {
      console.error('Failed to export draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to export draft');
    }
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDeleteId) return;
    setDeleting(true);
    try {
      await NotionDraftAPI.deleteDraft(pendingDeleteId);
      setDrafts((prev) => prev.filter((d) => (d.slug ?? d.id) !== pendingDeleteId));
      toast.success('Draft deleted');
      setPendingDeleteId(null);
    } catch (error: any) {
      console.error('Failed to delete draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to delete draft');
    } finally {
      setDeleting(false);
    }
  }, [pendingDeleteId]);

  const deletingTarget = drafts.find((d) => (d.slug ?? d.id) === pendingDeleteId);

  return (
    <DashboardLayout>
      <div className="max-w-[1000px] mx-auto w-full">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-3xl font-semibold text-gray-900">Notion</h1>
            <p className="mt-1 text-sm text-gray-500">
              Your notes, campaign briefs, and meeting drafts — block-based.
            </p>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            disabled={creating}
            className="inline-flex items-center gap-1.5 bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white font-medium px-4 py-2 rounded-md hover:opacity-95 text-sm shadow-sm transition disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <Plus className="w-4 h-4" />
            {creating ? 'Creating…' : 'New page'}
          </button>
        </div>

        <div className="mb-5 flex items-center gap-2">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search drafts…"
              className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-md bg-white focus:outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
            />
          </div>
          <div className="flex items-center gap-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                onClick={() => setFilter(f.key)}
                className={`px-3 py-1.5 text-xs font-medium rounded-md transition ${
                  filter === f.key
                    ? 'bg-[#3CCED7]/8 text-[#3CCED7] border border-[#3CCED7]/30'
                    : 'text-gray-600 hover:bg-gray-100 border border-transparent'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-16 rounded-lg bg-gray-100 animate-pulse" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          drafts.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-[#3CCED7]/15 to-[#A6E661]/15 flex items-center justify-center mb-4">
                <Notebook className="w-7 h-7 text-[#3CCED7]" />
              </div>
              <h3 className="text-base font-medium text-gray-900 mb-1.5">No drafts yet</h3>
              <p className="text-sm text-gray-500 mb-4 max-w-sm">
                Start capturing ideas, meeting notes, or campaign briefs with block-based editing.
              </p>
              <button
                type="button"
                onClick={handleCreate}
                disabled={creating}
                className="inline-flex items-center gap-1.5 bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white font-medium px-4 py-2 rounded-md hover:opacity-95 text-sm shadow-sm disabled:opacity-60"
              >
                <Plus className="w-4 h-4" />
                Create your first draft
              </button>
            </div>
          ) : (
            <div className="py-16 text-center text-sm text-gray-500">
              No drafts match the current filter.
            </div>
          )
        ) : (
          <div className="space-y-2">
            {filtered.map((draft) => (
              <NotionDraftCard
                key={draft.id}
                draft={draft}
                onOpen={handleOpen}
                onDuplicate={handleDuplicate}
                onExport={handleExport}
                onDelete={(id) => setPendingDeleteId(id)}
              />
            ))}
          </div>
        )}
      </div>

      <ConfirmDialog
        open={pendingDeleteId !== null}
        onOpenChange={(next) => {
          if (!next) setPendingDeleteId(null);
        }}
        title="Delete draft?"
        description={
          deletingTarget
            ? `"${deletingTarget.title || 'Untitled'}" will be moved to trash.`
            : undefined
        }
        confirmLabel={deleting ? 'Deleting…' : 'Delete'}
        destructive
        busy={deleting}
        onConfirm={handleConfirmDelete}
      />
    </DashboardLayout>
  );
}

export default function NotionV2ListPage() {
  return (
    <ProtectedRoute>
      <NotionV2ListContent />
    </ProtectedRoute>
  );
}
