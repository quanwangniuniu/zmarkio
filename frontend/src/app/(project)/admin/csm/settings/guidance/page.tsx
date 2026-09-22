'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertCircle, Plus } from 'lucide-react';
import CsmGuidanceAPI from '@/lib/api/csmGuidanceApi';
import { ExperienceGroupAPI } from '@/lib/api/experienceGroupApi';
import type { GuidanceEntry } from '@/types/csmGuidance';
import type { ExperienceGroupListItem } from '@/types/experienceGroup';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import GuidanceFormModal from '@/components/csm-settings/GuidanceFormModal';
import GuidanceSortableList from '@/components/csm-settings/GuidanceSortableList';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import { BUILDER_CONTROL_CLASS } from '@/components/csm-settings/constants';
import { PORTAL_SUBMIT_BUTTON_CLASS } from '@/components/ticket-form/constants';
import ConfirmModal from '@/components/ui/ConfirmModal';
import LoadingSpinner from '@/components/ui/LoadingSpinner';

const UNASSIGNED = 'unassigned';

/** Triggers run to 2000 characters, so quote only enough to identify the entry. */
const summarize = (text: string) =>
  text.length > 120 ? `${text.slice(0, 120).trimEnd()}…` : text;

export default function GuidanceSettingsPage() {
  const { projectId, projectValid } = useProjectIdFromUrl();
  const [groups, setGroups] = useState<ExperienceGroupListItem[]>([]);
  // A group id as string, or UNASSIGNED for entries that lost all their groups.
  // Tagged with its project so a project switch never queries the old group.
  const [selection, setSelection] = useState<{ projectId: number; value: string } | null>(null);
  const selected = selection?.projectId === projectId ? selection.value : '';
  const setSelected = useCallback(
    (value: string) => setSelection({ projectId, value }),
    [projectId],
  );
  const [entries, setEntries] = useState<GuidanceEntry[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [loadingGroups, setLoadingGroups] = useState(true);
  const [loadingEntries, setLoadingEntries] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<GuidanceEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<GuidanceEntry | null>(null);
  const [deleting, setDeleting] = useState(false);

  const selectedGroupId = selected && selected !== UNASSIGNED ? Number(selected) : null;

  const loadGroups = useCallback(async () => {
    if (!projectValid) return;
    setLoadingGroups(true);
    setError(null);
    try {
      const [groupRes, caps] = await Promise.all([
        ExperienceGroupAPI.list({ project: projectId }),
        CsmGuidanceAPI.capabilities(projectId),
      ]);
      const list = Array.isArray(groupRes.data) ? groupRes.data : groupRes.data.results ?? [];
      setGroups(list);
      setCanManage(caps.can_manage);
      // Keep the current selection only if it still exists (e.g. on retry).
      setSelection((prev) =>
        prev?.projectId === projectId
          && (prev.value === UNASSIGNED || list.some((g) => String(g.id) === prev.value))
          ? prev
          : { projectId, value: list[0] ? String(list[0].id) : UNASSIGNED },
      );
    } catch {
      setError('Failed to load experience groups.');
    } finally {
      setLoadingGroups(false);
    }
  }, [projectId, projectValid]);

  const loadEntries = useCallback(async () => {
    if (!projectValid || !selected) return;
    setLoadingEntries(true);
    setError(null);
    try {
      if (selected === UNASSIGNED) {
        const all = await CsmGuidanceAPI.list(projectId);
        setEntries(all.filter((e) => e.experience_groups.length === 0));
      } else {
        setEntries(await CsmGuidanceAPI.list(projectId, Number(selected)));
      }
    } catch {
      setError('Failed to load guidance.');
    } finally {
      setLoadingEntries(false);
    }
  }, [projectId, projectValid, selected]);

  useEffect(() => { loadGroups(); }, [loadGroups]);
  useEffect(() => { loadEntries(); }, [loadEntries]);

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };

  const openEdit = (row: GuidanceEntry) => {
    setEditing(row);
    setModalOpen(true);
  };

  const handleSaved = () => {
    setModalOpen(false);
    toast.success(editing ? 'Guidance updated.' : 'Guidance created.');
    setEditing(null);
    // Group membership may have changed, so refetch the current group's order.
    loadEntries();
  };

  const handleDeleteConfirm = async () => {
    const row = deleteTarget;
    if (!row) return;
    setDeleting(true);
    try {
      await CsmGuidanceAPI.remove(row.id);
      toast.success('Guidance deleted.');
      setEntries((prev) => prev.filter((e) => e.id !== row.id));
    } catch {
      toast.error('Could not delete guidance.');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <CsmSettingsPageRoot>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Agent Guidance</h1>
          <p className="mt-1 text-sm text-gray-500">
            Guidance shown to agents in the conversation workspace, per Experience Group.
            Drag to set the order agents see.
          </p>
        </div>
        {projectValid && canManage && (
          <button type="button" onClick={openCreate} className={`gap-2 ${PORTAL_SUBMIT_BUTTON_CLASS}`}>
            <Plus className="h-4 w-4" aria-hidden />
            New guidance
          </button>
        )}
      </div>

      {!projectValid ? (
        <CsmSettingsProjectGuard />
      ) : (
        <>
          {error && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              {error}
              <button
                type="button"
                onClick={() => { loadGroups(); loadEntries(); }}
                className="ml-auto rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-100"
              >
                Retry
              </button>
            </div>
          )}

          {!loadingGroups && !canManage && (
            <p className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm text-gray-600">
              Only the project owner or an organization admin can change guidance. You can view it.
            </p>
          )}

          <div className="flex max-w-sm flex-col gap-1.5">
            <label htmlFor="guidance-group-filter" className="text-sm font-medium text-gray-700">
              Experience group
            </label>
            <select
              id="guidance-group-filter"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              disabled={loadingGroups}
              className={BUILDER_CONTROL_CLASS}
            >
              {groups.map((g) => (
                <option key={g.id} value={String(g.id)}>{g.name}</option>
              ))}
              <option value={UNASSIGNED}>Unassigned entries</option>
            </select>
          </div>

          {loadingGroups || loadingEntries ? (
            <div className="flex min-h-[300px] flex-col items-center justify-center gap-3">
              <LoadingSpinner />
              <p className="text-sm text-gray-500">Loading…</p>
            </div>
          ) : entries.length === 0 ? (
            <div className="flex min-h-[200px] flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed border-gray-200">
              <p className="text-sm italic text-gray-400">
                {selected === UNASSIGNED
                  ? 'Every guidance entry belongs to at least one experience group.'
                  : 'No guidance for this experience group yet.'}
              </p>
              {canManage && selected !== UNASSIGNED && (
                <button
                  type="button"
                  onClick={openCreate}
                  className="rounded-lg border border-indigo-600 px-4 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50"
                >
                  New guidance
                </button>
              )}
            </div>
          ) : (
            <GuidanceSortableList
              projectId={projectId}
              experienceGroupId={selectedGroupId}
              canManage={canManage}
              items={entries}
              onChange={setEntries}
              onReorderFailed={loadEntries}
              onEdit={openEdit}
              onDelete={setDeleteTarget}
            />
          )}
        </>
      )}

      {projectValid && (
        <GuidanceFormModal
          isOpen={modalOpen}
          projectId={projectId}
          editing={editing}
          experienceGroups={groups}
          defaultExperienceGroupId={selectedGroupId}
          onClose={() => { setModalOpen(false); setEditing(null); }}
          onSaved={handleSaved}
        />
      )}

      <ConfirmModal
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        loading={deleting}
        type="danger"
        title="Delete guidance"
        message={
          deleteTarget
            ? `Delete "${summarize(deleteTarget.trigger_description)}"? Agents will stop seeing it immediately.`
            : ''
        }
        confirmText="Delete"
        cancelText="Cancel"
      />
    </CsmSettingsPageRoot>
  );
}
