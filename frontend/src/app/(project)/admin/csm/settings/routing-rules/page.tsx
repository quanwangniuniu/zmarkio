'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import toast from 'react-hot-toast';
import { AlertCircle, FlaskConical, Info, Plus } from 'lucide-react';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import { SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';
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
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const groupId = selectedGroupId ?? options.experienceGroups[0]?.id ?? null;
  const { rules, loading, error, load, upsert, reorder, toggle, remove } = useRoutingRules(projectId, groupId);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<RoutingRule | null>(null);
  const [pendingDelete, setPendingDelete] = useState<RoutingRule | null>(null);
  const [deleting, setDeleting] = useState(false);

  const lookups = useMemo(() => ({
    vocabulary: options.vocabulary,
    channelNames: new Map(options.channels.map((c) => [c.id, c.display_name])),
    organisationNames: new Map(options.organisations.map((o) => [o.id, o.name])),
  }), [options.vocabulary, options.channels, options.organisations]);

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
            Rules are not applied to live conversations yet. Use the sandbox to check how they would route;
            live chats still go to the channel&apos;s default queue.
          </div>

          <div className="max-w-sm">
            <label htmlFor="rr-group" className="mb-1.5 block text-[12px] font-medium uppercase tracking-wider text-gray-500">
              Experience group
            </label>
            <PortalSelect
              id="rr-group"
              value={groupId === null ? '' : String(groupId)}
              options={groupOptions}
              onChange={(v) => setSelectedGroupId(Number(v))}
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
              lookups={lookups}
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
