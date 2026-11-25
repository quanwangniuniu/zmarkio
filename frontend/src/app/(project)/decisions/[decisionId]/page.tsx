'use client';

import { useCallback, useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import toast from 'react-hot-toast';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';
import { useBuildUrl } from '@/lib/buildUrl';
import { DecisionAPI } from '@/lib/api/decisionApi';

import DecisionDetailHeader from '@/components/decisions/detail/DecisionDetailHeader';
import { extractFieldErrors } from '@/components/decisions/decisionValidation';
import DecisionFSMActionBar from '@/components/decisions/detail/DecisionFSMActionBar';
import DecisionPropertiesAside from '@/components/decisions/detail/DecisionPropertiesAside';
import DecisionCommitChecklistAside from '@/components/decisions/detail/DecisionCommitChecklistAside';
import DecisionContextSection from '@/components/decisions/detail/DecisionContextSection';
import DecisionReasoningSection from '@/components/decisions/detail/DecisionReasoningSection';
import DecisionOptionsSection from '@/components/decisions/detail/DecisionOptionsSection';
import DecisionSignalsSection from '@/components/decisions/detail/DecisionSignalsSection';
import DecisionReviewsSection from '@/components/decisions/detail/DecisionReviewsSection';
import DecisionExecutionSummarySection from '@/components/decisions/detail/DecisionExecutionSummarySection';
import DecisionLinkedTasksSection from '@/components/decisions/detail/DecisionLinkedTasksSection';
import DecisionOriginMeetingBlock from '@/components/decisions/detail/DecisionOriginMeetingBlock';
import DecisionConnectionsAside from '@/components/decisions/detail/DecisionConnectionsAside';
import DecisionActivityAside from '@/components/decisions/detail/DecisionActivityAside';
import DecisionSnapshotAside from '@/components/decisions/detail/DecisionSnapshotAside';
import DecisionCommitDialog, { type FieldError } from '@/components/decisions/detail/DecisionCommitDialog';
import DecisionApproveDialog from '@/components/decisions/detail/DecisionApproveDialog';
import DecisionArchiveDialog from '@/components/decisions/detail/DecisionArchiveDialog';
import DecisionReviewDialog from '@/components/decisions/detail/DecisionReviewDialog';
import DecisionSignalDialog from '@/components/decisions/detail/DecisionSignalDialog';
import DraftOwnerNotice from '@/components/decisions/detail/DraftOwnerNotice';
import DecisionDeleteDialog from '@/components/decisions/DecisionDeleteDialog';
import ConfirmDialog from '@/components/tasks/detail/ConfirmDialog';
import { useDecisionDetail, extractError } from '@/components/decisions/detail/hooks/useDecisionDetail';
import { useProjectRole } from '@/components/decisions/detail/hooks/useProjectRole';
import { useDraftOwnerLock } from '@/components/decisions/detail/hooks/useDraftOwnerLock';
import type {
  DecisionOptionDraft,
  DecisionRiskLevel,
  DecisionSignal,
} from '@/types/decision';

const EDITABLE_STATUSES = new Set(['PREDRAFT', 'DRAFT']);

function DecisionDetailContent() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const params = useParams<{ decisionId: string }>();
  const { projectId, activeProject } = useActiveProjectForFlatRoute();

  const decisionId = String(params.decisionId);

  const { canEdit, canApproveOrReview, members } = useProjectRole(projectId);

  const detail = useDecisionDetail(decisionId || null, projectId);
  const status = detail.status;
  const base = detail.base;

  const [reviews, setReviews] = useState<any[]>([]);
  const [commitOpen, setCommitOpen] = useState(false);
  const [approveOpen, setApproveOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [signalDialogOpen, setSignalDialogOpen] = useState(false);
  const [signalEdit, setSignalEdit] = useState<DecisionSignal | null>(null);
  const [pendingSignalDelete, setPendingSignalDelete] = useState<DecisionSignal | null>(null);
  const [signalDeleting, setSignalDeleting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState({ commit: false, approve: false, archive: false });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const isEditingStatus = status ? EDITABLE_STATUSES.has(status) : false;
  const editable = isEditingStatus && canEdit;
  const committed = detail.committed;
  const ownerId = detail.draft?.createdBy ?? committed?.createdBy ?? null;
  const ownerLocked = useDraftOwnerLock(status, ownerId, canApproveOrReview);
  const ownerEditable = editable && !ownerLocked;
  const title = detail.draft?.title ?? detail.committed?.title ?? '';
  const contextSummary = detail.draft?.contextSummary ?? committed?.contextSummary ?? '';
  const reasoning = detail.draft?.reasoning ?? committed?.reasoning ?? '';
  const riskLevel = (detail.draft?.riskLevel ?? committed?.riskLevel ?? null) as DecisionRiskLevel | null;
  const confidenceScore = detail.draft?.confidenceScore ?? committed?.confidenceScore ?? null;
  const options: DecisionOptionDraft[] = (detail.draft?.options as any) ?? (committed?.options as any) ?? [];
  const signals = detail.signals;
  const projectSeq = detail.draft?.projectSeq ?? committed?.projectSeq ?? null;
  const createdByAgent = detail.draft?.createdByAgent ?? committed?.createdByAgent ?? false;
  const originMeeting =
    ((detail.draft as any)?.originMeeting ??
      (detail.draft as any)?.origin_meeting ??
      (committed as any)?.originMeeting ??
      (committed as any)?.origin_meeting) ??
    null;
  const commitRecord = (committed as any)?.commitRecord ?? null;
  const stateTransitions = ((committed as any)?.stateTransitions ?? []) as any[];
  const plannedDecisionDate = (detail.draft as any)?.plannedDecisionDate ?? null;

  // ---- Load reviews once committed ----
  const loadReviews = useCallback(async () => {
    if (!decisionId) return;
    if (status === 'COMMITTED' || status === 'REVIEWED' || status === 'ARCHIVED') {
      try {
        const res = await DecisionAPI.listReviews(decisionId, projectId);
        const arr = Array.isArray(res) ? res : res?.items ?? [];
        setReviews(arr);
      } catch {
        // ignore; aside optional
      }
    } else {
      setReviews([]);
    }
  }, [decisionId, projectId, status]);

  useMemo(() => {
    loadReviews();
  }, [loadReviews]);

  const clearFieldError = (...fields: string[]) => {
    setFieldErrors((prev) => {
      const next = { ...prev };
      for (const f of fields) delete next[f];
      return next;
    });
  };

  // ---- Patch helpers ----
  const saveDraft = async (payload: Parameters<typeof detail.patchDraft>[0], fields: string[]) => {
    try {
      await detail.patchDraft(payload);
      clearFieldError(...fields);
    } catch (err) {
      const errors = extractFieldErrors(err);
      if (Object.keys(errors).length > 0) setFieldErrors((prev) => ({ ...prev, ...errors }));
      throw err;
    }
  };

  const handleTitleSave = async (next: string) => {
    await saveDraft({ title: next || null }, ['title']);
    toast.success('Saved');
  };
  const handleContextSave = async (next: string) => {
    await detail.patchDraft({ contextSummary: next });
    clearFieldError('contextSummary');
  };
  const handleReasoningSave = async (next: string) => {
    await detail.patchDraft({ reasoning: next });
    clearFieldError('reasoning');
  };
  const handleRiskChange = async (next: DecisionRiskLevel | null) => {
    await saveDraft({ riskLevel: next }, ['riskLevel']);
  };
  const handleConfidenceChange = async (next: number | null) => {
    await saveDraft({ confidenceScore: next }, ['confidenceScore']);
  };
  const handlePlannedDateChange = async (next: string | null) => {
    await detail.patchDraft({ plannedDecisionDate: next } as any);
  };
  const handleOptionsSave = async (next: DecisionOptionDraft[]) => {
    await saveDraft({ options: next }, ['options', 'selectedOption']);
  };

  // ---- FSM actions ----
  const performCommit = async (): Promise<FieldError[] | null> => {
    if (!decisionId) return null;
    setBusy((b) => ({ ...b, commit: true }));
    try {
      await DecisionAPI.commit(decisionId, projectId);
      toast.success(
        riskLevel === 'HIGH' ? 'Decision submitted for approval' : 'Decision committed',
      );
      setFieldErrors({});
      router.replace(buildUrl('/decisions'));
      return null;
    } catch (err: any) {
      const body = err?.response?.data;
      const field_errors: FieldError[] | undefined = body?.error?.details?.fieldErrors;
      if (field_errors && field_errors.length > 0) {
        const mapping: Record<string, string> = {};
        field_errors.forEach((fe) => {
          mapping[fe.field] = fe.message;
        });
        setFieldErrors(mapping);
        return field_errors;
      }
      toast.error(extractError(err, 'Commit failed'));
      return null;
    } finally {
      setBusy((b) => ({ ...b, commit: false }));
    }
  };

  const performApprove = async () => {
    if (!decisionId) return;
    setBusy((b) => ({ ...b, approve: true }));
    try {
      await DecisionAPI.approve(decisionId, projectId);
      toast.success('Decision approved');
      await detail.refetch();
    } catch (err) {
      toast.error(extractError(err, 'Approval failed'));
      throw err;
    } finally {
      setBusy((b) => ({ ...b, approve: false }));
    }
  };

  const performArchive = async () => {
    if (!decisionId) return;
    setBusy((b) => ({ ...b, archive: true }));
    try {
      await DecisionAPI.archive(decisionId, projectId);
      toast.success('Decision archived');
      await detail.refetch();
    } catch (err) {
      toast.error(extractError(err, 'Archive failed'));
      throw err;
    } finally {
      setBusy((b) => ({ ...b, archive: false }));
    }
  };

  const performSaveDraft = async () => {
    // Save-as-you-type is already live; button provides explicit feedback.
    toast.success('All changes saved');
  };

  const performPromoteToDraft = async () => {
    if (!decisionId) return;
    try {
      await detail.patchDraft({});
      toast.success('Promoted to draft');
    } catch {
      // patchDraft already toasts on error
    }
  };

  const performAddReview = async (payload: { outcomeText: string; reflectionText: string; decisionQuality: 'GOOD' | 'ACCEPTABLE' | 'POOR' }) => {
    if (!decisionId) return;
    await DecisionAPI.createReview(decisionId, payload as any, projectId);
    toast.success('Review submitted');
    await loadReviews();
    await detail.refetch();
  };

  const performDelete = async () => {
    if (!decisionId) return;
    setDeleting(true);
    try {
      await DecisionAPI.deleteDecision(decisionId, projectId);
      toast.success('Decision deleted');
      router.push(buildUrl('/decisions'));
    } catch (err) {
      toast.error(extractError(err, 'Delete failed'));
    } finally {
      setDeleting(false);
    }
  };

  // ---- Signal CRUD ----
  const openSignalCreate = () => {
    setSignalEdit(null);
    setSignalDialogOpen(true);
  };
  const openSignalEdit = (signal: DecisionSignal) => {
    setSignalEdit(signal);
    setSignalDialogOpen(true);
  };
  const handleSignalSubmit = async (payload: any) => {
    if (!decisionId || !ownerEditable) return;
    try {
      if (signalEdit?.id) {
        await DecisionAPI.updateSignal(decisionId, signalEdit.id, payload, projectId);
      } else {
        await DecisionAPI.createSignal(decisionId, payload, projectId);
      }
      toast.success(signalEdit?.id ? 'Signal updated' : 'Signal added');
      await detail.refreshSignals();
    } catch (err: any) {
      toast.error(extractError(err, 'Failed to save signal'));
      throw err;
    }
  };
  const handleSignalDelete = async () => {
    if (!decisionId || !ownerEditable || !pendingSignalDelete?.id) return;
    setSignalDeleting(true);
    try {
      await DecisionAPI.deleteSignal(decisionId, pendingSignalDelete.id, projectId);
      toast.success('Signal deleted');
      await detail.refreshSignals();
      setPendingSignalDelete(null);
    } catch (err) {
      toast.error(extractError(err, 'Failed to delete signal'));
    } finally {
      setSignalDeleting(false);
    }
  };

  const handleReviewInPage = (firstField: string | null) => {
    if (!firstField) return;
    const anchorMap: Record<string, string> = {
      contextSummary: 'decision-section-context',
      reasoning: 'decision-section-reasoning',
      options: 'decision-section-options',
      selectedOption: 'decision-section-options',
      signals: 'decision-section-signals',
      riskLevel: 'decision-section-properties',
      confidenceScore: 'decision-section-properties',
    };
    const id = anchorMap[firstField];
    if (id) {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };

  // ---- Loading / error states ----
  if (!decisionId) {
    return (
      <DashboardLayout alerts={[]} upcomingMeetings={[]}>
        <div className="mx-auto w-full max-w-[1440px] px-3 py-3 text-sm text-gray-500 sm:px-6 sm:py-4">
          Invalid decision id.
        </div>
      </DashboardLayout>
    );
  }
  if (detail.loading && !base) {
    return (
      <DashboardLayout alerts={[]} upcomingMeetings={[]}>
        <div className="mx-auto w-full max-w-[1440px] px-3 py-3 text-sm text-gray-500 sm:px-6 sm:py-4">
          Loading decision…
        </div>
      </DashboardLayout>
    );
  }
  if (detail.error || !base) {
    return (
      <DashboardLayout alerts={[]} upcomingMeetings={[]}>
        <div className="mx-auto w-full max-w-[1440px] px-3 py-3 text-sm text-gray-500 sm:px-6 sm:py-4">
          {detail.error || 'Decision not found.'}
        </div>
      </DashboardLayout>
    );
  }

  const archivedClass = status === 'ARCHIVED' ? 'opacity-60' : '';

  const actionBar = (
    <DecisionFSMActionBar
      status={status}
      canEdit={canEdit}
      canApproveOrReview={canApproveOrReview}
      committing={busy.commit}
      approving={busy.approve}
      archiving={busy.archive}
      onDelete={() => setDeleteOpen(true)}
      onSaveDraft={performSaveDraft}
      onCommit={() => setCommitOpen(true)}
      onApprove={() => setApproveOpen(true)}
      onArchive={() => setArchiveOpen(true)}
      onAddReview={() => setReviewOpen(true)}
      onPromoteToDraft={performPromoteToDraft}
    />
  );

  return (
    <DashboardLayout alerts={[]} upcomingMeetings={[]}>
      <div className={`bg-gray-50 ${archivedClass}`}>
        <div className="mx-auto max-w-[1440px] px-3 py-3 sm:px-6 sm:py-4">
          <DecisionDetailHeader
            projectId={projectId}
            projectName={activeProject?.name}
            projectSeq={projectSeq}
            title={title}
            status={status}
            riskLevel={riskLevel}
            createdByAgent={createdByAgent}
            editable={editable}
            onTitleSave={handleTitleSave}
            error={fieldErrors.title}
            onErrorDismiss={() => clearFieldError('title')}
            actionBar={actionBar}
          />

          {status === 'ARCHIVED' && (
            <div className="mt-3 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] text-gray-600 sm:mt-4">
              This decision has been archived. History, reviews, and snapshot are preserved but no further changes can be made.
            </div>
          )}

          <div className="mt-3 grid min-w-0 gap-3 sm:mt-4 sm:gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
            <main className="min-w-0 space-y-3 sm:space-y-5">
              <DecisionContextSection
                value={contextSummary ?? ''}
                editable={editable}
                error={fieldErrors.contextSummary}
                onSave={handleContextSave}
              />
              {editable && ownerLocked && <DraftOwnerNotice ownerId={ownerId} members={members} />}
              <DecisionOptionsSection
                value={options}
                editable={ownerEditable}
                error={fieldErrors.options}
                selectedError={fieldErrors.selectedOption}
                onSave={handleOptionsSave}
              />
              <DecisionReasoningSection
                value={reasoning ?? ''}
                editable={ownerEditable}
                error={fieldErrors.reasoning}
                onSave={handleReasoningSave}
              />
              <DecisionSignalsSection
                signals={signals}
                editable={ownerEditable}
                topError={fieldErrors.signals}
                onAdd={openSignalCreate}
                onEdit={openSignalEdit}
                onDelete={(s) => setPendingSignalDelete(s)}
              />
              <DecisionLinkedTasksSection
                decisionId={decisionId}
                projectId={projectId}
                editable={status !== 'ARCHIVED' && canEdit}
                onCreateTask={() => {
                  const q = new URLSearchParams();
                  q.set('link_decision', String(detail.committed?.slug ?? decisionId));
                  router.push(buildUrl(`/tasks/new?${q.toString()}`));
                }}
              />
              {(status === 'COMMITTED' || status === 'REVIEWED' || status === 'ARCHIVED') && (
                <DecisionExecutionSummarySection signals={signals} />
              )}
              {(status === 'COMMITTED' || status === 'REVIEWED' || status === 'ARCHIVED') && (
                <DecisionReviewsSection
                  reviews={reviews}
                  canAddReview={status !== 'ARCHIVED' && canApproveOrReview}
                  onAddReview={() => setReviewOpen(true)}
                />
              )}
            </main>

            <aside className="min-w-0 space-y-3 sm:space-y-5">
              {editable && (
                <DecisionCommitChecklistAside
                  contextSummary={contextSummary ?? ''}
                  options={options}
                  reasoning={reasoning ?? ''}
                  signals={signals}
                  riskLevel={riskLevel}
                  confidenceScore={confidenceScore}
                  onJump={(id) => {
                    const el = document.getElementById(id);
                    if (!el) return;
                    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    el.classList.add('ring-2', 'ring-[#3CCED7]', 'ring-offset-2', 'rounded-md');
                    setTimeout(() => {
                      el.classList.remove('ring-2', 'ring-[#3CCED7]', 'ring-offset-2', 'rounded-md');
                    }, 1500);
                  }}
                />
              )}
              <div id="decision-section-properties" />
              <DecisionPropertiesAside
                status={status}
                riskLevel={riskLevel}
                confidenceScore={confidenceScore}
                plannedDecisionDate={plannedDecisionDate}
                committedAt={committed?.committedAt ?? null}
                approvedAt={committed?.approvedAt ?? null}
                authorId={committed?.createdBy ?? null}
                approvedById={committed?.approvedBy ?? null}
                members={members}
                editable={editable}
                errors={{
                  risk: fieldErrors.riskLevel,
                  confidence: fieldErrors.confidenceScore,
                }}
                onRiskChange={handleRiskChange}
                onConfidenceChange={handleConfidenceChange}
                onPlannedDateChange={handlePlannedDateChange}
              />
              <DecisionOriginMeetingBlock origin={originMeeting as any} projectId={projectId} />
              <DecisionConnectionsAside
                decisionId={decisionId}
                projectId={projectId}
                mySeq={projectSeq ?? null}
              />
              {(status === 'COMMITTED' || status === 'REVIEWED' || status === 'ARCHIVED') && (
                <DecisionActivityAside transitions={stateTransitions} />
              )}
              {(status === 'COMMITTED' || status === 'REVIEWED' || status === 'ARCHIVED') && (
                <DecisionSnapshotAside
                  snapshot={(commitRecord as any)?.validation_snapshot ?? null}
                  committedAt={(commitRecord as any)?.committedAt ?? committed?.committedAt ?? null}
                />
              )}
            </aside>
          </div>
        </div>
      </div>

      <DecisionCommitDialog
        open={commitOpen}
        onOpenChange={setCommitOpen}
        riskLevel={riskLevel}
        onConfirm={performCommit}
        onReviewInPage={handleReviewInPage}
      />
      <DecisionApproveDialog
        open={approveOpen}
        onOpenChange={setApproveOpen}
        onConfirm={performApprove}
      />
      <DecisionArchiveDialog
        open={archiveOpen}
        onOpenChange={setArchiveOpen}
        onConfirm={performArchive}
      />
      <DecisionReviewDialog
        open={reviewOpen}
        onOpenChange={setReviewOpen}
        onConfirm={performAddReview}
      />
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
        confirmLabel="Delete signal"
        onConfirm={handleSignalDelete}
      />
      <DecisionDeleteDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={title}
        busy={deleting}
        onConfirm={performDelete}
      />
    </DashboardLayout>
  );
}

export default function DecisionDetailPage() {
  return (
    <ProtectedRoute>
      <DecisionDetailContent />
    </ProtectedRoute>
  );
}
