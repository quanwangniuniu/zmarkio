'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import { DecisionAPI, type DecisionDraftPayload } from '@/lib/api/decisionApi';
import { extractFieldErrors } from '@/components/decisions/decisionValidation';
import type {
  DecisionCommittedResponse,
  DecisionDraftResponse,
  DecisionSignal,
  DecisionStatus,
} from '@/types/decision';

const DRAFT_PATCH_STATUSES = new Set<DecisionStatus>(['PREDRAFT', 'DRAFT', 'AWAITING_APPROVAL']);
const COMMITTED_PATCH_STATUSES = new Set<DecisionStatus>(['COMMITTED', 'REVIEWED']);

export type DecisionKind = 'draft' | 'committed' | 'unknown';

export interface DecisionDetailState {
  kind: DecisionKind;
  draft: DecisionDraftResponse | null;
  committed: DecisionCommittedResponse | null;
  signals: DecisionSignal[];
  loading: boolean;
  error: string | null;
}

export function extractError(err: any, fallback: string): string {
  return (
    err?.response?.data?.detail ||
    err?.response?.data?.error?.message ||
    err?.response?.data?.message ||
    Object.values(extractFieldErrors(err))[0] ||
    (err as Error)?.message ||
    fallback
  );
}

function saveErrorMessage(err: any): string {
  return err?.request && !err?.response
    ? "Couldn't save — check your connection."
    : extractError(err, 'Failed to save');
}

export function useDecisionDetail(decisionId: number | string | null, projectId: number | string | null) {
  const [state, setState] = useState<DecisionDetailState>({
    kind: 'unknown',
    draft: null,
    committed: null,
    signals: [],
    loading: false,
    error: null,
  });
  const cancelledRef = useRef(false);

  const fetchDetail = useCallback(async () => {
    if (!decisionId) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      // 1. Always fetch committed resource first — it always returns 200 and includes status
      //    DecisionDraftSerializer does NOT expose status field, so draft-first is unreliable.
      const committed = await DecisionAPI.getDecision(decisionId, projectId);
      const status = committed.status;
      const editable = status === 'DRAFT' || status === 'AWAITING_APPROVAL';
      // PREDRAFT is filtered out of list and isn't expected here; treat as editable if encountered.
      const isPredraft = (status as string) === 'PREDRAFT';
      let draft: DecisionDraftResponse | null = null;
      let kind: DecisionKind = 'committed';
      if (editable || isPredraft) {
        draft = await DecisionAPI.getDraft(decisionId, projectId);
        kind = 'draft';
      }
      const signalsRes = await DecisionAPI.listSignals(decisionId, projectId).catch(
        () => ({ items: [] as DecisionSignal[] })
      );
      if (cancelledRef.current) return;
      setState({
        kind,
        draft,
        committed,
        signals: signalsRes.items ?? [],
        loading: false,
        error: null,
      });
    } catch (err: any) {
      if (cancelledRef.current) return;
      const msg = extractError(err, 'Failed to load decision');
      setState((s) => ({ ...s, loading: false, error: msg }));
      toast.error(msg);
    }
  }, [decisionId, projectId]);

  useEffect(() => {
    cancelledRef.current = false;
    fetchDetail();
    return () => {
      cancelledRef.current = true;
    };
  }, [fetchDetail]);

  const patchDraft = useCallback(
    async (payload: DecisionDraftPayload) => {
      if (!decisionId) return null;
      try {
        const next = await DecisionAPI.patchDraft(decisionId, payload, projectId);
        setState((s) => ({ ...s, draft: next, kind: 'draft' }));
        return next;
      } catch (err) {
        toast.error(saveErrorMessage(err));
        throw err;
      }
    },
    [decisionId, projectId]
  );

  const saveDecision = useCallback(
    async (payload: DecisionDraftPayload) => {
      if (!decisionId) return null;
      const decisionStatus = state.committed?.status;
      try {
        if (decisionStatus && DRAFT_PATCH_STATUSES.has(decisionStatus)) {
          const next = await DecisionAPI.patchDraft(decisionId, payload, projectId);
          setState((s) => ({ ...s, draft: next, kind: 'draft' }));
          return next;
        }
        if (decisionStatus && COMMITTED_PATCH_STATUSES.has(decisionStatus)) {
          const next = await DecisionAPI.patchDecision(decisionId, payload, projectId);
          setState((s) => ({
            ...s,
            committed: next,
            kind: 'committed',
          }));
          return next;
        }
        toast.error('This decision cannot be edited.');
        return null;
      } catch (err) {
        toast.error(saveErrorMessage(err));
        throw err;
      }
    },
    [decisionId, projectId, state.committed?.status],
  );

  const refreshSignals = useCallback(async () => {
    if (!decisionId) return;
    try {
      const res = await DecisionAPI.listSignals(decisionId, projectId);
      setState((s) => ({ ...s, signals: res.items ?? [] }));
    } catch (err) {
      toast.error(extractError(err, 'Failed to reload signals'));
    }
  }, [decisionId, projectId]);

  // status always comes from committed response (draft endpoint does not expose status)
  const getStatus = () => state.committed?.status ?? null;

  const getBase = () => (state.kind === 'draft' ? state.draft : state.committed);

  return {
    ...state,
    status: getStatus(),
    base: getBase(),
    refetch: fetchDetail,
    patchDraft,
    saveDecision,
    refreshSignals,
  };
}
