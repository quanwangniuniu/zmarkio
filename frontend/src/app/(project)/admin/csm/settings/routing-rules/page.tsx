'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import toast from 'react-hot-toast';
import { AlertCircle, FlaskConical, Info, Plus } from 'lucide-react';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import { FORM_LABEL_CLASS, SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';
import RoutingRuleFormDrawer from '@/components/csm-settings/routing/RoutingRuleFormDrawer';
import RoutingRulesList from '@/components/csm-settings/routing/RoutingRulesList';
import { useRoutingOptions } from '@/components/csm-settings/routing/useRoutingOptions';
import { useRoutingRules } from '@/components/csm-settings/routing/useRoutingRules';
import { PORTAL_SUBMIT_BUTTON_CLASS } from '@/components/ticket-form/constants';
import PortalSelect from '@/components/ticket-form/portal/PortalSelect';
import ConfirmModal from '@/components/ui/ConfirmModal';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { useBuildUrl } from '@/lib/buildUrl';
import type { RoutingRule } from '@/types/routingRule';

export default function RoutingRulesSettingsPage() {
  const { projectId, projectValid } = useProjectIdFromUrl();
  const buildUrl = useBuildUrl();
  const options = useRoutingOptions(projectId, projectValid);
  const router = useRouter();
  const searchParams = useSearchParams();
  // The selected group lives in ?group= so a refresh (or a shared link) keeps it.
  // An unknown or stale id falls back to the first group.
  const groupParam = Number(searchParams?.get('group'));
  const groupId = options.experienceGroups.find((g) => g.id === groupParam)?.id
    ?? options.experienceGroups[0]?.id ?? null;
  const selectGroup = (value: string) => {
    const params = new URLSearchParams(searchParams?.toString() || '');
    params.set('group', value);
    router.replace(buildUrl(`/admin/csm/settings/routing-rules?${params.toString()}`));
  };
  const { rules, loading, error, load, upsert, reorder, toggle, remove } = useRoutingRules(projectId, groupId);
  const unroutable = rules.filter((r) => !r.can_route).length;

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<RoutingRule | null>(null);
  const [pendingDelete, setPendingDelete] = useState<RoutingRule | null>(null);
  const [deleting, setDeleting] = useState(false);

  const groupOptions = options.experienceGroups.map((g) => ({ value: String(g.id), label: g.name }));

  const openCreate = () => { setEditing(null); setDrawerOpen(true); };
  const openEdit = (rule: RoutingRule) => { setEditing(rule); setDrawerOpen(true); };

  const handleSaved = (saved: RoutingRule) => {
    toast.success(editing ? 'Rule updated.' : 'Rule created.');
    setDrawerOpen(false);
    setEditing(null);
    upsert(saved);
  };

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleting(true);
    await remove(pendingDelete);
    setDeleting(false);
    setPendingDelete(null);
  };

  const ready = !options.loading && options.vocabulary !== null;

  return (
    <CsmSettingsPageRoot>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Routing Rules</h1>
          <p className="mt-1 text-sm text-gray-500">
            Route conversations to queues per experience group. Rules run top to bottom; the first match wins.
          </p>
        </div>
        {projectValid && (
          <div className="flex flex-wrap items-center gap-3">
            <Link href={buildUrl('/admin/csm/settings/routing-sandbox')} className={SECONDARY_BUTTON_CLASS}>
              <FlaskConical className="h-4 w-4" aria-hidden />
              Test in sandbox
            </Link>
            <button
              type="button"
              onClick={openCreate}
              disabled={!ready || groupId === null}
              className={`gap-2 ${PORTAL_SUBMIT_BUTTON_CLASS}`}
            >
              <Plus className="h-4 w-4" aria-hidden />
              New rule
            </button>
          </div>
        )}
      </div>

      {!projectValid ? (
        <CsmSettingsProjectGuard />
      ) : options.loading ? (
        <div className="flex min-h-[300px] items-center justify-center"><LoadingSpinner /></div>
      ) : options.error ? (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          {options.error}
        </div>
      ) : options.experienceGroups.length === 0 ? (
        <p className="text-sm text-gray-500">Create an experience group first; routing rules belong to one.</p>
      ) : (
        <>
          <div className="flex items-start gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-800">
            <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            Rules route each new portal conversation once, when the customer sends their first message, using
            the customer&apos;s experience group. With no match it goes to the channel&apos;s default queue. Test
            changes in the sandbox first.
          </div>

          {!loading && unroutable > 0 && (
            <div
              className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
              role="status"
              data-testid="unroutable-banner"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {unroutable === 1 ? '1 rule' : `${unroutable} rules`} in this group can&apos;t route because the queue was deleted or
              deactivated, so evaluation skips {unroutable === 1 ? 'it' : 'them'}. Edit and choose an active queue.
            </div>
          )}

          <div className="max-w-sm">
            <label htmlFor="rr-group" className={FORM_LABEL_CLASS}>
              Experience group
            </label>
            <PortalSelect
              id="rr-group"
              value={groupId === null ? '' : String(groupId)}
              options={groupOptions}
              onChange={selectGroup}
            />
          </div>

          {error && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {error}
              <button type="button" onClick={load} className="ml-auto rounded-lg border border-red-300 px-3 py-1.5 text-sm">
                Retry
              </button>
            </div>
          )}

          {loading ? (
            <div className="flex min-h-[200px] items-center justify-center"><LoadingSpinner /></div>
          ) : rules.length === 0 ? (
            <div className="flex min-h-[160px] flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed border-gray-200">
              <p className="text-sm italic text-gray-400">
                No rules yet. Without rules, conversations use the channel&apos;s default queue.
              </p>
            </div>
          ) : (
            <RoutingRulesList
              rules={rules}
              lookups={options.lookups}
              onReorder={reorder}
              onEdit={openEdit}
              onToggle={toggle}
              onDelete={setPendingDelete}
            />
          )}
        </>
      )}

      {projectValid && ready && groupId !== null && (
        <RoutingRuleFormDrawer
          isOpen={drawerOpen}
          projectId={projectId}
          experienceGroupId={groupId}
          editing={editing}
          vocabulary={options.vocabulary!}
          queues={options.queues}
          channels={options.channels}
          organisations={options.organisations}
          onClose={() => { setDrawerOpen(false); setEditing(null); }}
          onSaved={handleSaved}
        />
      )}

      <ConfirmModal
        isOpen={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={confirmDelete}
        title="Delete routing rule"
        message={`Delete "${pendingDelete?.name ?? ''}"? This cannot be undone.`}
        confirmText="Delete"
        loading={deleting}
      />
    </CsmSettingsPageRoot>
  );
}
