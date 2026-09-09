'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import toast from 'react-hot-toast';
import { Loader2, Mail, Plus, AlertTriangle } from 'lucide-react';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ConfirmDialog from '@/components/tasks/detail/ConfirmDialog';
import {
  EmailDraftTableV2,
  type EmailDraftRow,
} from '@/components/email-draft';
import { klaviyoApi } from '@/lib/api/klaviyoApi';
import { useBuildUrl } from '@/lib/buildUrl';
import type { KlaviyoDraft } from '@/hooks/useKlaviyoData';
import { toastDeduped } from '@/lib/toastDeduped';

function toRow(draft: KlaviyoDraft): EmailDraftRow {
  return {
    id: draft.id,
    slug: draft.slug,
    title: draft.name || draft.subject || 'Untitled template',
    subject: draft.subject,
    status: draft.status || 'draft',
    updatedAt: draft.updated_at,
    createdAt: draft.created_at,
  };
}

export default function KlaviyoV2Page() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const [drafts, setDrafts] = useState<EmailDraftRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<EmailDraftRow | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const loadDrafts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await klaviyoApi.getEmailDrafts();
      setDrafts(result.map(toRow));
    } catch (err: any) {
      if (err?.status === 401) {
        if (typeof window !== 'undefined') window.location.href = '/login';
        return;
      }
      setError(err?.message || 'Failed to load Klaviyo drafts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDrafts();
  }, [loadDrafts]);

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return drafts;
    return drafts.filter((row) =>
      [row.title, row.subject, row.status]
        .filter(Boolean)
        .some((v) => (v as string).toLowerCase().includes(q))
    );
  }, [drafts, searchQuery]);

  const handleOpen = (row: EmailDraftRow) => {
    router.push(buildUrl(`/klaviyo/${row.slug}`));
  };
  const handleEdit = handleOpen;

  const handleConfirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    try {
      await klaviyoApi.deleteEmailDraft(deleteTarget.slug ?? deleteTarget.id);
      setDrafts((prev) => prev.filter((d) => d.id !== deleteTarget.id));
      toast.success(`Moved "${deleteTarget.title}" to trash`);
      setDeleteTarget(null);
    } catch (err) {
      toast.error('Failed to delete draft. Please try again.');
    } finally {
      setDeleteBusy(false);
    }
  };

  const [creating, setCreating] = useState(false);
  const handleCreate = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const created = await klaviyoApi.createEmailDraft({
        subject: 'Untitled template',
        status: 'draft',
      });
      if (created?.id) {
        router.push(buildUrl(`/klaviyo/${created.slug}`));
      } else {
        void loadDrafts();
      }
    } catch (err) {
      // Retries of the same create failure merge into one toast with a count badge.
      toastDeduped.error('Failed to create template. Please try again.');
      setCreating(false);
    }
  };

  return (
    <DashboardLayout>
      <div className="space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">
              Klaviyo templates
            </h1>
            <p className="mt-0.5 text-xs text-gray-500">
              Build and edit block-based email templates synced with Klaviyo.
            </p>
          </div>
          <button
            type="button"
            onClick={handleCreate}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3.5 text-sm font-medium text-white shadow-sm transition hover:opacity-95"
          >
            <Plus className="h-4 w-4" />
            New template
          </button>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search Klaviyo templates"
            className="w-full max-w-lg rounded-md border border-gray-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center rounded-lg bg-white py-20 ring-1 ring-gray-200">
            <Loader2 className="h-5 w-5 animate-spin text-[#3CCED7]" />
            <span className="ml-2 text-sm text-gray-500">Loading templates...</span>
          </div>
        ) : error ? (
          <div className="flex items-center justify-center rounded-lg bg-white py-20 ring-1 ring-rose-200">
            <AlertTriangle className="h-5 w-5 text-rose-500" />
            <div className="ml-3">
              <p className="text-sm font-medium text-rose-700">
                Failed to load templates
              </p>
              <p className="text-xs text-gray-500">{error}</p>
              <button
                type="button"
                onClick={() => void loadDrafts()}
                className="mt-2 text-xs font-medium text-[#3CCED7] hover:underline"
              >
                Retry
              </button>
            </div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg bg-white py-20 ring-1 ring-gray-200">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-[#3CCED7]/10 to-[#A6E661]/10">
              <Mail className="h-6 w-6 text-[#3CCED7]" />
            </div>
            <p className="mt-3 text-sm font-medium text-gray-900">
              {searchQuery
                ? 'No templates match your search'
                : 'No Klaviyo templates yet'}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              {searchQuery
                ? 'Try a different keyword or clear the search.'
                : 'Start with a blank block-based template.'}
            </p>
            {!searchQuery && (
              <button
                type="button"
                onClick={handleCreate}
                className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3.5 text-sm font-medium text-white shadow-sm transition hover:opacity-95"
              >
                <Plus className="h-4 w-4" />
                Create your first template
              </button>
            )}
          </div>
        ) : (
          <EmailDraftTableV2
            platform="klaviyo"
            rows={filtered}
            onOpen={handleOpen}
            onEdit={handleEdit}
            onDelete={(row) => setDeleteTarget(row)}
          />
        )}
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="Move this template to trash?"
        description={
          deleteTarget
            ? `"${deleteTarget.title}" will be moved to trash and can be restored later.`
            : undefined
        }
        confirmLabel={deleteBusy ? 'Deleting...' : 'Delete'}
        destructive
        busy={deleteBusy}
        onConfirm={handleConfirmDelete}
      />
    </DashboardLayout>
  );
}
