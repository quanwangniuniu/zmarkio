'use client';

import Link from 'next/link';
import { useBuildUrl } from '@/lib/buildUrl';
import { useEffect, useMemo, useState } from 'react';
import { ExternalLink, Loader2, Pencil, Save, X } from 'lucide-react';
import toast from 'react-hot-toast';
import DecisionStatusPill from '@/components/decisions/DecisionStatusPill';
import DecisionConnectionsAside from '@/components/decisions/detail/DecisionConnectionsAside';
import DecisionContextSection from '@/components/decisions/detail/DecisionContextSection';
import DecisionLinkedTasksSection from '@/components/decisions/detail/DecisionLinkedTasksSection';
import DecisionOptionsSection from '@/components/decisions/detail/DecisionOptionsSection';
import DecisionOriginMeetingBlock from '@/components/decisions/detail/DecisionOriginMeetingBlock';
import DecisionPropertiesAside from '@/components/decisions/detail/DecisionPropertiesAside';
import DecisionReasoningSection from '@/components/decisions/detail/DecisionReasoningSection';
import DecisionSignalDialog, {
  type SignalFormPayload,
} from '@/components/decisions/detail/DecisionSignalDialog';
import DecisionSignalsSection from '@/components/decisions/detail/DecisionSignalsSection';
import DraftOwnerNotice from '@/components/decisions/detail/DraftOwnerNotice';
import { useDecisionDetail } from '@/components/decisions/detail/hooks/useDecisionDetail';
import { OWNER_ONLY_STATUSES, useDraftOwnerLock } from '@/components/decisions/detail/hooks/useDraftOwnerLock';
import { useProjectRole } from '@/components/decisions/detail/hooks/useProjectRole';
import { DecisionAPI } from '@/lib/api/decisionApi';
import ConfirmDialog from '@/components/tasks/detail/ConfirmDialog';
import type {
  DecisionGraphNode,
  DecisionOptionDraft,
  DecisionRiskLevel,
  DecisionSignal,
  DecisionStatus,
} from '@/types/decision';

interface PanelDraft {
  contextSummary: string;
  reasoning: string;
  riskLevel: DecisionRiskLevel | null;
  confidenceScore: number | null;
  plannedDecisionDate: string | null;
  options: DecisionOptionDraft[];
}

interface Props {
  decisionId: number | string;
  /** Slug of the decision; preferred for API lookups and URLs (slug-only backend). */
  decisionSlug?: string | null;
  projectId: number | string | null;
  /** Status from the graph card (used until detail API loads). */
  graphNodeStatus?: DecisionStatus | null;
  canEdit: boolean;
  /** Open the form in edit mode (e.g. right after Create Decision). */
  startInEditMode?: boolean;
  onStartInEditModeConsumed?: () => void;
  isProvisional?: boolean;
  onProvisionalSaved?: () => void;
  onDiscardProvisional?: () => Promise<void> | void;
  onClose: () => void;
  onOpenFullPage?: (idOrSlug: number | string, projectId?: number | string | null) => void;
  onUpdated?: (opts?: {
    fullReload?: boolean;
    nodePatch?: { id: number } & Partial<Pick<DecisionGraphNode, 'title' | 'status' | 'riskLevel'>>;
  }) => void | Promise<void>;
}

export default function DecisionTreeDetailPanel({
  decisionId,
  decisionSlug = null,
  projectId,
  graphNodeStatus = null,
  canEdit: canEditProp,
  startInEditMode = false,
  onStartInEditModeConsumed,
  isProvisional = false,
  onProvisionalSaved,
  onDiscardProvisional,
  onClose,
  onOpenFullPage,
  onUpdated,
}: Props) {
  const buildUrl = useBuildUrl();
  const decisionKey = decisionSlug ?? decisionId;
  const detail = useDecisionDetail(decisionKey, projectId);
  const { canEdit: roleCanEdit, canApproveOrReview, members } = useProjectRole(projectId);
  const committed = detail.committed;
  const base = detail.base;
  const status = detail.status;
  const effectiveStatus = status ?? graphNodeStatus;

  const [editing, setEditing] = useState(false);
  const [localTitle, setLocalTitle] = useState('');
  const [panelDraft, setPanelDraft] = useState<PanelDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [signalDialogOpen, setSignalDialogOpen] = useState(false);
  const [signalEdit, setSignalEdit] = useState<DecisionSignal | null>(null);
  const [pendingSignalDelete, setPendingSignalDelete] = useState<DecisionSignal | null>(null);
  const [signalDeleting, setSignalDeleting] = useState(false);

  const title = detail.draft?.title ?? committed?.title ?? '';
  const contextSummary = detail.draft?.contextSummary ?? committed?.contextSummary ?? '';
  const reasoning = detail.draft?.reasoning ?? committed?.reasoning ?? '';
  const riskLevel = (detail.draft?.riskLevel ?? committed?.riskLevel ?? null) as DecisionRiskLevel | null;
  const confidenceScore = detail.draft?.confidenceScore ?? committed?.confidenceScore ?? null;
  const options: DecisionOptionDraft[] = useMemo(
    () =>
      (detail.draft?.options as DecisionOptionDraft[] | undefined) ??
      (committed?.options as DecisionOptionDraft[] | undefined) ??
      [],
    [committed?.options, detail.draft?.options],
  );
  const projectSeq = detail.draft?.projectSeq ?? committed?.projectSeq ?? null;
  const plannedDecisionDate =
    (detail.draft as { plannedDecisionDate?: string | null } | null)?.plannedDecisionDate ?? null;
  const originMeeting =
    (detail.draft?.origin_meeting ?? committed?.origin_meeting) ?? null;

  const canEdit = canEditProp && roleCanEdit;
  const canModify = canEdit && effectiveStatus !== 'ARCHIVED';
  const fieldsEditable = canModify && editing && panelDraft != null;
  const ownerId = detail.draft?.createdBy ?? committed?.createdBy ?? null;
  const ownerLocked = useDraftOwnerLock(effectiveStatus, ownerId, canApproveOrReview);
  const ownerFieldsEditable = fieldsEditable && !ownerLocked;
  const signalsEditable =
    ownerFieldsEditable && effectiveStatus != null && OWNER_ONLY_STATUSES.has(effectiveStatus);
  const showEditButton = canModify && !editing && (base != null || graphNodeStatus != null);

  useEffect(() => {
    setEditing(false);
    setPanelDraft(null);
  }, [decisionId]);

  useEffect(() => {
    if (!startInEditMode || detail.loading || !base || editing) return;
    setLocalTitle(title ?? '');
    setPanelDraft({
      contextSummary: contextSummary ?? '',
      reasoning: reasoning ?? '',
      riskLevel,
      confidenceScore,
      plannedDecisionDate: plannedDecisionDate ?? null,
      options: [...(options ?? [])],
    });
    setEditing(true);
    onStartInEditModeConsumed?.();
  }, [
    startInEditMode,
    detail.loading,
    base,
    editing,
    title,
    contextSummary,
    reasoning,
    riskLevel,
    confidenceScore,
    plannedDecisionDate,
    options,
    onStartInEditModeConsumed,
  ]);

  useEffect(() => {
    if (!editing) {
      setLocalTitle(title ?? '');
    }
  }, [title, editing]);

  const notifyUpdated = async (fullReload = false) => {
    await onUpdated?.({
      fullReload,
      nodePatch: fullReload
        ? undefined
        : {
            id: committed?.id ?? detail.draft?.id ?? (typeof decisionId === 'number' ? decisionId : 0),
            title: localTitle.trim() || null,
            status: effectiveStatus ?? undefined,
            riskLevel: displayRisk ?? undefined,
          },
    });
  };

  const startEditing = () => {
    setLocalTitle(title ?? '');
    setPanelDraft({
      contextSummary: contextSummary ?? '',
      reasoning: reasoning ?? '',
      riskLevel,
      confidenceScore,
      plannedDecisionDate: plannedDecisionDate ?? null,
      options: [...(options ?? [])],
    });
    setEditing(true);
  };

  const cancelEditing = async () => {
    if (isProvisional && onDiscardProvisional) {
      setSaving(true);
      try {
        await onDiscardProvisional();
      } finally {
        setSaving(false);
      }
      return;
    }
    setEditing(false);
    setPanelDraft(null);
    setLocalTitle(title ?? '');
  };

  const handleSaveAll = async () => {
    if (!panelDraft) return;
    setSaving(true);
    try {
      await detail.saveDecision({
        title: localTitle.trim() || null,
        contextSummary: panelDraft.contextSummary,
        riskLevel: panelDraft.riskLevel,
        confidenceScore: panelDraft.confidenceScore,
        plannedDecisionDate: panelDraft.plannedDecisionDate,
        ...(ownerLocked ? {} : { reasoning: panelDraft.reasoning, options: panelDraft.options }),
      } as Parameters<typeof detail.saveDecision>[0]);
      setEditing(false);
      setPanelDraft(null);
      toast.success('Saved');
      await notifyUpdated(isProvisional);
      onProvisionalSaved?.();
    } finally {
      setSaving(false);
    }
  };

  const displayContext = fieldsEditable ? panelDraft.contextSummary : (contextSummary ?? '');
  const displayReasoning = fieldsEditable ? panelDraft.reasoning : (reasoning ?? '');
  const displayRisk = fieldsEditable ? panelDraft.riskLevel : riskLevel;
  const displayConfidence = fieldsEditable ? panelDraft.confidenceScore : confidenceScore;
  const displayPlannedDate = fieldsEditable ? panelDraft.plannedDecisionDate : plannedDecisionDate;
  const displayOptions = fieldsEditable ? panelDraft.options : options;

  const openSignalCreate = () => {
    setSignalEdit(null);
    setSignalDialogOpen(true);
  };

  const openSignalEdit = (signal: DecisionSignal) => {
    setSignalEdit(signal);
    setSignalDialogOpen(true);
  };

  const handleSignalSubmit = async (payload: SignalFormPayload) => {
    if (signalEdit?.id) {
      await DecisionAPI.updateSignal(decisionKey, signalEdit.id, payload, projectId);
      toast.success('Signal updated');
    } else {
      await DecisionAPI.createSignal(decisionKey, payload, projectId);
      toast.success('Signal added');
    }
    await detail.refreshSignals();
    await notifyUpdated(false);
  };

  const handleSignalDelete = async () => {
    if (!pendingSignalDelete?.id) return;
    setSignalDeleting(true);
    try {
      await DecisionAPI.deleteSignal(decisionKey, pendingSignalDelete.id, projectId);
      toast.success('Signal deleted');
      await detail.refreshSignals();
      setPendingSignalDelete(null);
      notifyUpdated();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to delete signal';
      toast.error(msg);
    } finally {
      setSignalDeleting(false);
    }
  };

  const fullPageHref = buildUrl(`/decisions/${decisionKey}`);

  return (
    <>
      <aside className="flex h-full w-[400px] shrink-0 flex-col border-r border-gray-200 bg-gray-50 shadow-lg">
        <div className="flex shrink-0 flex-col gap-2 border-b border-gray-100 bg-white px-4 py-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                {projectSeq != null && (
                  <span className="text-[11px] font-medium tabular-nums text-gray-400">
                    #{projectSeq}
                  </span>
                )}
                {effectiveStatus && <DecisionStatusPill status={effectiveStatus} />}
                {saving && <span className="text-[11px] text-gray-400">Saving…</span>}
              </div>
              {fieldsEditable ? (
                <input
                  type="text"
                  value={localTitle}
                  onChange={(e) => setLocalTitle(e.target.value)}
                  placeholder="Untitled decision"
                  aria-label="Decision title"
                  className="mt-1 w-full rounded-md border border-gray-200 bg-white px-2 py-1 text-[15px] font-semibold leading-snug text-gray-900 placeholder:text-gray-300 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
                />
              ) : (
                <h2 className="mt-1 line-clamp-2 text-[15px] font-semibold leading-snug text-gray-900">
                  {title?.trim() || 'Untitled decision'}
                </h2>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-0.5">
              {onOpenFullPage && !isProvisional ? (
                <button
                  type="button"
                  onClick={() => onOpenFullPage(decisionKey, projectId)}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-gray-900"
                  title="Open full page"
                  aria-label="Open full page"
                >
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                </button>
              ) : !isProvisional ? (
                <Link
                  href={fullPageHref}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-gray-900"
                  title="Open full page"
                  aria-label="Open full page"
                >
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                </Link>
              ) : null}
              <button
                type="button"
                onClick={onClose}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-gray-900"
                aria-label="Close details panel"
                title="Close"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>

          {showEditButton ? (
            <button
              type="button"
              onClick={startEditing}
              disabled={!base || detail.loading}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[#3CCED7] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#35b8c0] disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Pencil className="h-4 w-4" aria-hidden="true" />
              Edit
            </button>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {detail.loading && !base ? (
            <div className="flex items-center justify-center py-16 text-gray-500">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading decision…
            </div>
          ) : detail.error || !base ? (
            <p className="py-8 text-center text-sm text-rose-600">
              {detail.error || 'Decision not found.'}
            </p>
          ) : (
            <div className="space-y-4">
              <DecisionPropertiesAside
                status={effectiveStatus}
                riskLevel={displayRisk}
                confidenceScore={displayConfidence}
                plannedDecisionDate={displayPlannedDate}
                committedAt={committed?.committedAt ?? null}
                approvedAt={committed?.approvedAt ?? null}
                authorId={committed?.createdBy ?? null}
                approvedById={committed?.approvedBy ?? null}
                members={members}
                editable={fieldsEditable}
                onRiskChange={(next) =>
                  setPanelDraft((d) => (d ? { ...d, riskLevel: next } : d))
                }
                onConfidenceChange={(next) =>
                  setPanelDraft((d) => (d ? { ...d, confidenceScore: next } : d))
                }
                onPlannedDateChange={(next) =>
                  setPanelDraft((d) => (d ? { ...d, plannedDecisionDate: next } : d))
                }
              />
              {originMeeting ? (
                <DecisionOriginMeetingBlock origin={originMeeting} projectId={projectId} />
              ) : null}
              <DecisionContextSection
                value={displayContext}
                editable={fieldsEditable}
                onSave={(next) =>
                  setPanelDraft((d) => (d ? { ...d, contextSummary: next } : d))
                }
              />
              {fieldsEditable && ownerLocked && <DraftOwnerNotice ownerId={ownerId} members={members} />}
              <DecisionOptionsSection
                value={displayOptions}
                editable={ownerFieldsEditable}
                onSave={(next) => setPanelDraft((d) => (d ? { ...d, options: next } : d))}
              />
              <DecisionReasoningSection
                value={displayReasoning}
                editable={ownerFieldsEditable}
                onSave={(next) => setPanelDraft((d) => (d ? { ...d, reasoning: next } : d))}
              />
              <DecisionSignalsSection
                signals={detail.signals}
                editable={signalsEditable}
                onAdd={openSignalCreate}
                onEdit={openSignalEdit}
                onDelete={(s) => setPendingSignalDelete(s)}
              />
              <DecisionConnectionsAside
                decisionId={decisionKey}
                projectId={projectId}
                mySeq={projectSeq}
              />
              <DecisionLinkedTasksSection
                decisionId={decisionId}
                projectId={projectId}
                editable={status !== 'ARCHIVED' && canEdit && !editing}
                onCreateTask={() => {
                  const q = new URLSearchParams();
                  q.set('link_decision', String(decisionKey));
                  window.open(buildUrl(`/tasks/new?${q.toString()}`), '_blank');
                }}
              />
            </div>
          )}
        </div>

        {editing && base ? (
          <div className="flex shrink-0 gap-2 border-t border-gray-200 bg-white px-4 py-3">
            <button
              type="button"
              onClick={() => void cancelEditing()}
              disabled={saving}
              className="inline-flex flex-1 items-center justify-center rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSaveAll}
              disabled={saving}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-[#3CCED7] px-4 py-2 text-sm font-medium text-white transition hover:bg-[#35b8c0] disabled:opacity-60"
            >
              <Save className="h-4 w-4" aria-hidden="true" />
              Save
            </button>
          </div>
        ) : null}
      </aside>

      <DecisionSignalDialog
        open={signalDialogOpen}
        mode={signalEdit?.id ? 'edit' : 'create'}
        initial={signalEdit}
        onOpenChange={setSignalDialogOpen}
        onSubmit={handleSignalSubmit}
      />

      <ConfirmDialog
        open={!!pendingSignalDelete}
        onOpenChange={(v) => {
          if (!v) setPendingSignalDelete(null);
        }}
        title="Delete signal"
        description="This removes the signal from the decision. This action cannot be undone."
        destructive
        busy={signalDeleting}
        onConfirm={handleSignalDelete}
      />
    </>
  );
}
