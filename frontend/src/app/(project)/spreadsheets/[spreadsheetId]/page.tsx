'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { AlertCircle, FileSpreadsheet } from 'lucide-react';
import toast from 'react-hot-toast';

import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ConfirmDialog from '@/components/common/ConfirmDialog';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';
import { useBuildUrl } from '@/lib/buildUrl';
import { ProjectAPI } from '@/lib/api/projectApi';
import { SpreadsheetAPI } from '@/lib/api/spreadsheetApi';
import { PatternAPI } from '@/lib/api/patternApi';
import {
  stageSpreadsheetInsightsPreload,
  SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT,
  type SpreadsheetHighlightDetail,
} from '@/lib/agentLaunchContext';
import { openAgentSidePanel } from '@/lib/agentSidePanelStore';
import {
  SpreadsheetData,
  SheetData,
  UpdateSheetRequest,
} from '@/types/spreadsheet';
import SpreadsheetGrid, { SpreadsheetGridHandle } from '@/components/spreadsheets/SpreadsheetGrid';
import PatternAgentPanelV2 from '@/components/spreadsheets/PatternAgentPanelV2';
import { PivotEditorPanel } from '@/components/spreadsheets/PivotEditorPanel';
import {
  PivotConfig,
  SourceColumn,
  SourceRow,
  buildPivotTable,
  pivotResultToCellOperations,
  generateClearOperationsForStaleCells,
  generatePivotSheetName,
  createEmptyPivotConfig,
  isPivotConfigValid,
} from '@/lib/spreadsheet/pivot';
import { rowColToA1 } from '@/lib/spreadsheets/a1';
import {
  HEADER_ROW_INDEX,
  RENAME_DEDUP_WINDOW_MS,
  recordRenameColumnStep,
  shouldRecordHeaderRename,
  RenameDedupState,
} from '@/lib/spreadsheets/patternRecorder';
import {
  CreatePatternPayload,
  ApplyHighlightParams,
  FillSeriesParams,
  InsertColumnParams,
  InsertRowParams,
  DeleteColumnParams,
  PatternStep,
  TimelineItem,
  flattenTimelineItems,
  WorkflowPatternDetail,
  WorkflowPatternStepRecord,
  WorkflowPatternSummary,
  PatternJobStatus,
} from '@/types/patterns';
import {
  deleteTimelineItemById,
  moveStepOutOfGroup,
  timelineItemsToCreateSteps,
  updateTimelineItemById,
} from '@/lib/spreadsheets/timelineItems';

import SpreadsheetDetailHeader from '@/components/spreadsheets/detail/SpreadsheetDetailHeader';
import SheetTabBarBottom from '@/components/spreadsheets/detail/SheetTabBarBottom';
import CreateSheetDialog from '@/components/spreadsheets/CreateSheetDialog';
import PresenceAvatars from '@/components/spreadsheets/presence/PresenceAvatars';
import { useSheetPresenceBridge } from '@/components/spreadsheets/presence/useSheetPresenceBridge';

function getNextSheetName(existingSheets: SheetData[]): string {
  const re = /^sheet(\d+)$/i;
  let maxNumber = 0;
  existingSheets.forEach((sheet) => {
    const match = sheet.name.trim().match(re);
    if (match) {
      const n = Number(match[1]);
      if (!Number.isNaN(n)) maxNumber = Math.max(maxNumber, n);
    }
  });
  return `Sheet${maxNumber + 1}`;
}

function createStepId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `step_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function extractErrorMessage(err: unknown, fallback: string): string {
  const anyErr = err as {
    response?: { data?: { error?: string; detail?: string } };
    message?: string;
  };
  return (
    anyErr?.response?.data?.error ||
    anyErr?.response?.data?.detail ||
    anyErr?.message ||
    fallback
  );
}

function extractSpreadsheetLoadError(err: unknown): string {
  const anyErr = err as {
    response?: { status?: number; data?: { error?: string } };
    message?: string;
  };
  const status = anyErr?.response?.status;
  if (status === 404) return 'Spreadsheet not found.';
  if (status === 403) return 'You do not have access to this spreadsheet.';
  if (status === 401) return 'Your session has expired. Please sign in again.';
  return (
    anyErr?.response?.data?.error ||
    anyErr?.message ||
    'Failed to load spreadsheet.'
  );
}

export default function SpreadsheetsV2DetailPage() {
  const params = useParams();
  const spreadsheetId = params?.spreadsheetId as string;
  const { projectId } = useActiveProjectForFlatRoute();
  const buildUrl = useBuildUrl();

  const [spreadsheet, setSpreadsheet] = useState<SpreadsheetData | null>(null);
  const [sheets, setSheets] = useState<SheetData[]>([]);
  const [activeSheetId, setActiveSheetId] = useState<number | null>(null);
  const refreshSheetListRef = useRef<() => Promise<void>>(async () => {});
  const sheetListRefreshGenerationRef = useRef(0);
  const { remoteUsers, onSelectionChange, clientId: collabClientId } = useSheetPresenceBridge(
    activeSheetId,
    {
      onCellsUpdated: (cells) => gridRef.current?.applyRemoteCells(cells),
      onRefreshRequired: (reason) => {
        if (reason === 'sheet_created' || reason === 'sheet_updated' || reason === 'sheet_deleted') {
          void refreshSheetListRef.current();
          return;
        }
        if (reason === 'reconnected') {
          void refreshSheetListRef.current();
        }
        gridRef.current?.refresh();
      },
    }
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createSheetOpen, setCreateSheetOpen] = useState(false);
  const [createSheetDefaultName, setCreateSheetDefaultName] = useState('Sheet1');
  const [deleteConfirmSheet, setDeleteConfirmSheet] = useState<SheetData | null>(null);
  const [deletingSheet, setDeletingSheet] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renamingSpreadsheetSaving, setRenamingSpreadsheetSaving] = useState(false);

  const [projectName, setProjectName] = useState<string | null>(null);

  const [highlightCell, setHighlightCell] = useState<{ row: number; col: number } | null>(null);
  const [highlightLocations, setHighlightLocations] = useState<{ row: number; col: number }[] | null>(null);
  const [patterns, setPatterns] = useState<WorkflowPatternSummary[]>([]);
  const [patternsLoading, setPatternsLoading] = useState(true);
  const [exportingPattern, setExportingPattern] = useState(false);
  const [selectedPattern, setSelectedPattern] = useState<WorkflowPatternDetail | null>(null);
  const [applySteps, setApplySteps] = useState<
    Array<WorkflowPatternStepRecord & { status: 'pending' | 'success' | 'error'; errorMessage?: string }>
  >([]);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applyFailedIndex, setApplyFailedIndex] = useState<number | null>(null);
  const [isApplying, setIsApplying] = useState(false);
  const [patternJobId, setPatternJobId] = useState<string | null>(null);
  const [patternJobStatus, setPatternJobStatus] = useState<PatternJobStatus | null>(null);
  const [patternJobProgress, setPatternJobProgress] = useState(0);
  const [patternJobError, setPatternJobError] = useState<string | null>(null);
  const [sheetHydrationReady, setSheetHydrationReady] = useState(true);

  const [pivotConfigsBySheet, setPivotConfigsBySheet] = useState<Record<number, PivotConfig>>({});
  const [pivotSourceDataBySheet, setPivotSourceDataBySheet] = useState<Record<number, {
    cells: Map<string, { rawInput: string; computedString?: string | null }>;
    rowCount: number;
    colCount: number;
    sourceSheetId: number;
    sourceSheetName: string;
  }>>({});
  const [pivotDimensionsBySheet, setPivotDimensionsBySheet] = useState<Record<number, { rowCount: number; colCount: number }>>({});
  const [showPivotEditor, setShowPivotEditor] = useState(false);
  const [agentStepsBySheet, setAgentStepsBySheet] = useState<Record<number, TimelineItem[]>>({});

  const gridRef = useRef<SpreadsheetGridHandle | null>(null);

  refreshSheetListRef.current = async () => {
    if (!spreadsheetId) return;
    const generation = ++sheetListRefreshGenerationRef.current;
    try {
      const response = await SpreadsheetAPI.listSheets(String(spreadsheetId));
      if (generation !== sheetListRefreshGenerationRef.current) return;
      const list = response.results || [];
      setSheets(list);
      setCreateSheetDefaultName(getNextSheetName(list));
      setActiveSheetId((current) => {
        if (current != null && list.some((sheet) => sheet.id === current)) return current;
        return list[0]?.id ?? null;
      });
    } catch (err) {
      console.error('Failed to refresh sheet list after collaboration event:', err);
    }
  };
  const patternJobStartRef = useRef<number | null>(null);
  const renameDedupRef = useRef<Record<number, RenameDedupState>>({});
  const activeJobIdRef = useRef<string | null>(null);
  const applyStepsRef = useRef<typeof applySteps>([]);
  const applyHighlightStepsRef = useRef<(steps: WorkflowPatternStepRecord[]) => void>(() => {});
  const isReplayingRef = useRef(false);
  // Promise cache for the auto-create "Sheet1" call. React Strict Mode fires the
  // loader effect twice in dev, and the two async coroutines can both observe an
  // empty sheet list before either has POSTed. Caching the in-flight promise
  // guarantees only ONE POST /sheets/ is sent per mount — concurrent callers
  // await the same promise and share its result (or error).
  const firstSheetPromiseRef = useRef<Promise<SheetData | null> | null>(null);

  useEffect(() => {
    if (!projectId) {
      setProjectName(null);
      return;
    }
    let cancelled = false;
    ProjectAPI.getProjects()
      .then((list) => {
        if (cancelled) return;
        const match = list.find((p) => String(p.id) === String(projectId) || p.slug === projectId);
        setProjectName(match?.name ?? null);
      })
      .catch(() => {
        if (!cancelled) setProjectName(null);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (activeSheetId == null) return;
    setHighlightCell(null);
    const active = sheets.find((s) => s.id === activeSheetId);
    setShowPivotEditor(!!active && active.kind === 'pivot');
  }, [activeSheetId, sheets]);

  const handleAnalyzeWithAgent = useCallback(() => {
    if (!spreadsheetId || activeSheetId == null || !spreadsheet || projectId == null) return;
    const active = sheets.find((s) => s.id === activeSheetId);
    if (!active) return;
    stageSpreadsheetInsightsPreload({
      projectId,
      spreadsheetId: Number(spreadsheetId),
      sheetId: activeSheetId,
      sheetName: active.name,
      spreadsheetName: spreadsheet.name,
    });
    window.dispatchEvent(new CustomEvent('agent:new-chat'));
    openAgentSidePanel();
  }, [spreadsheetId, activeSheetId, spreadsheet, sheets, projectId]);

  const handleOpenPatternGenerationAgent = useCallback(() => {
    if (activeSheetId == null) return;
    const active = sheets.find((s) => s.id === activeSheetId);
    window.dispatchEvent(new CustomEvent('agent:pattern-generation-mode', {
      detail: {
        sheetId: activeSheetId,
        sheetName: active?.name ?? undefined,
        spreadsheetName: spreadsheet?.name ?? undefined,
      },
    }));
    openAgentSidePanel();
  }, [activeSheetId, sheets, spreadsheet]);

  const handleOpenPivotGenerationAgent = useCallback(() => {
    if (!spreadsheetId || activeSheetId == null) return;
    const active = sheets.find((s) => s.id === activeSheetId);
    window.dispatchEvent(new CustomEvent('agent:pivot-generation-mode', {
      detail: {
        spreadsheetId,
        sheetId: activeSheetId,
        sheetName: active?.name ?? undefined,
        spreadsheetName: spreadsheet?.name ?? undefined,
      },
    }));
    openAgentSidePanel();
  }, [spreadsheetId, activeSheetId, sheets, spreadsheet]);

  useEffect(() => {
    const onStepsGenerated = (e: Event) => {
      const detail = (e as CustomEvent<{ sheetId: number; steps: TimelineItem[] }>).detail;
      if (!detail?.steps?.length) return;
      setAgentStepsBySheet((prev) => {
        const current = prev[detail.sheetId] ?? [];
        return { ...prev, [detail.sheetId]: [...current, ...detail.steps] };
      });
    };
    window.addEventListener('agent:pattern-steps-generated', onStepsGenerated);
    return () => window.removeEventListener('agent:pattern-steps-generated', onStepsGenerated);
  }, []);

  // The pivot-generation agent creates a new pivot sheet through the API from the
  // side panel; pull it into this view (and switch to it) without a full reload.
  useEffect(() => {
    const onPivotSheetCreated = (e: Event) => {
      const detail = (e as CustomEvent<{ spreadsheetId?: string | number; sheetId?: number }>).detail;
      if (!detail || detail.sheetId == null) return;
      if (String(detail.spreadsheetId) !== String(spreadsheetId)) return;
      void refreshSheetListRef.current().then(() => {
        setActiveSheetId(detail.sheetId!);
      });
    };
    window.addEventListener('agent:pivot-sheet-created', onPivotSheetCreated);
    return () => window.removeEventListener('agent:pivot-sheet-created', onPivotSheetCreated);
  }, [spreadsheetId]);

  useEffect(() => {
    let clearTimer: number | null = null;

    const onHighlightLocations = (event: Event) => {
      const detail = (event as CustomEvent<SpreadsheetHighlightDetail>).detail;
      if (!detail || Number(detail.spreadsheetId) !== Number(spreadsheetId)) return;
      if (detail.sheetId !== activeSheetId) {
        setActiveSheetId(detail.sheetId);
      }
      const locations = detail.locations?.map((loc) => ({
        row: loc.row,
        col: loc.col,
      })) ?? [];
      setHighlightLocations(locations.length ? locations : null);
      setHighlightCell(null);
      if (locations.length > 0) {
        gridRef.current?.navigateToCell(locations[0].row, locations[0].col);
      }
      if (clearTimer) {
        window.clearTimeout(clearTimer);
      }
      clearTimer = window.setTimeout(() => {
        setHighlightLocations(null);
        clearTimer = null;
      }, 8000);
    };

    window.addEventListener(SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT, onHighlightLocations);
    return () => {
      window.removeEventListener(SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT, onHighlightLocations);
      if (clearTimer) {
        window.clearTimeout(clearTimer);
      }
    };
  }, [spreadsheetId, activeSheetId]);

  useEffect(() => {
    const hydratePivotSource = async () => {
      if (!spreadsheetId || !activeSheetId) return;
      const active = sheets.find((s) => s.id === activeSheetId);
      if (!active || active.kind !== 'pivot') return;
      if (pivotSourceDataBySheet[activeSheetId]) return;

      const config = pivotConfigsBySheet[activeSheetId];
      const sourceSheetId =
        config?.sourceSheetId ?? active.pivot_config?.source_sheet_id ?? null;
      if (!sourceSheetId) return;

      const sourceSheet = sheets.find((s) => s.id === sourceSheetId);
      const sourceSheetName = sourceSheet?.name ?? 'Unknown';

      try {
        const response = await SpreadsheetAPI.readCellRange(
          String(spreadsheetId),
          sourceSheetId,
          0,
          999,
          0,
          50
        );
        const sourceRowCount = response.sheet_row_count ?? response.row_count;
        const sourceColCount = response.sheet_column_count ?? response.column_count;
        const cells = new Map<string, { rawInput: string; computedString?: string | null }>();
        for (const cell of response.cells) {
          cells.set(`${cell.row_position}:${cell.column_position}`, {
            rawInput: cell.raw_input ?? '',
            computedString: cell.computed_string ?? null,
          });
        }
        setPivotSourceDataBySheet((prev) => {
          if (prev[activeSheetId]) return prev;
          return {
            ...prev,
            [activeSheetId]: {
              cells,
              rowCount: sourceRowCount,
              colCount: sourceColCount,
              sourceSheetId,
              sourceSheetName,
            },
          };
        });
      } catch (err) {
        console.error('Failed to hydrate pivot source data:', err);
      }
    };
    void hydratePivotSource();
  }, [activeSheetId, spreadsheetId, sheets, pivotConfigsBySheet, pivotSourceDataBySheet]);

  const agentSteps = activeSheetId != null ? agentStepsBySheet[activeSheetId] ?? [] : [];

  const updateAgentSteps = useCallback(
    (updater: TimelineItem[] | ((prev: TimelineItem[]) => TimelineItem[])) => {
      if (activeSheetId == null) return;
      setAgentStepsBySheet((prev) => {
        const current = prev[activeSheetId] ?? [];
        const next = typeof updater === 'function' ? updater(current) : updater;
        if (next === current) return prev;
        return { ...prev, [activeSheetId]: next };
      });
    },
    [activeSheetId]
  );

  const handleAddSteps = useCallback(
    (steps: TimelineItem[]) => {
      if (activeSheetId == null) return;
      updateAgentSteps((prev) => [...prev, ...steps]);
    },
    [activeSheetId, updateAgentSteps]
  );

  const ensureFirstSheet = async (existingSheets: SheetData[]) => {
    if (!spreadsheetId || existingSheets.length > 0) return null;

    // Dedupe concurrent callers (React Strict Mode double-invoke, remount races,
    // etc.) by sharing one in-flight Promise. Without this, each caller fires
    // its own POST, the backend serializes them, and the second one 400s with
    // "Sheet1 already exists" — noisy in devtools and occasionally flaky in UI.
    if (firstSheetPromiseRef.current) {
      return firstSheetPromiseRef.current;
    }

    const promise: Promise<SheetData | null> = (async () => {
      try {
        return await SpreadsheetAPI.createSheet(String(spreadsheetId), { name: 'Sheet1' });
      } catch (err: unknown) {
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 400) {
          const retry = await SpreadsheetAPI.listSheets(String(spreadsheetId));
          if (retry.results && retry.results.length > 0) return retry.results[0];
        }
        throw err;
      }
    })();

    firstSheetPromiseRef.current = promise;
    void promise.finally(() => {
      if (firstSheetPromiseRef.current === promise) {
        firstSheetPromiseRef.current = null;
      }
    });
    return promise;
  };

  useEffect(() => {
    const load = async () => {
      if (!spreadsheetId) {
        setError('Spreadsheet ID is required');
        setLoading(false);
        return;
      }
      try {
        setLoading(true);
        setError(null);
        const data = await SpreadsheetAPI.getSpreadsheet(String(spreadsheetId));
        setSpreadsheet(data);
        const resp = await SpreadsheetAPI.listSheets(String(spreadsheetId));
        const list = resp.results || [];
        if (list.length === 0) {
          const created = await ensureFirstSheet(list);
          if (created) {
            setSheets([created]);
            setActiveSheetId(created.id);
            setCreateSheetDefaultName(getNextSheetName([created]));
          }
        } else {
          setSheets(list);
          setCreateSheetDefaultName(getNextSheetName(list));
          const hydrated: Record<number, PivotConfig> = {};
          list.forEach((sheet) => {
            if (sheet.kind === 'pivot' && sheet.pivot_config) {
              const cfg = sheet.pivot_config;
              hydrated[sheet.id] = {
                sourceSheetId: cfg.source_sheet_id,
                rows: cfg.rows_config || [],
                columns: cfg.columns_config || [],
                values: cfg.values_config || [],
                showGrandTotalRow: cfg.show_grand_total_row,
              };
            }
          });
          setPivotConfigsBySheet(hydrated);
          if (list.length > 0 && activeSheetId == null) setActiveSheetId(list[0].id);
        }
      } catch (err) {
        setError(extractSpreadsheetLoadError(err));
      } finally {
        setLoading(false);
      }
    };
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spreadsheetId]);

  useEffect(() => {
    setPatternsLoading(true);
    PatternAPI.listPatterns()
      .then((response) => setPatterns(response.results || []))
      .catch(() => undefined)
      .finally(() => setPatternsLoading(false));
  }, []);

  const loadPatternDetail = useCallback(async (patternId: string) => {
    try {
      const pattern = await PatternAPI.getPattern(patternId);
      const sorted = [...(pattern.steps || [])].sort((a, b) => a.seq - b.seq);
      setSelectedPattern(pattern);
      setApplySteps(sorted.map((step) => ({ ...step, status: 'pending' })));
      setApplyError(null);
      setApplyFailedIndex(null);
    } catch (err) {
      toast.error('Failed to load pattern');
    }
  }, []);

  const handleDeletePattern = useCallback(
    async (patternId: string) => {
      try {
        await PatternAPI.deletePattern(patternId);
        setPatterns((prev) => prev.filter((p) => p.id !== patternId));
        if (selectedPattern?.id === patternId) {
          setSelectedPattern(null);
          setApplySteps([]);
          setApplyError(null);
          setApplyFailedIndex(null);
        }
        toast.success('Pattern deleted');
      } catch (err) {
        toast.error(extractErrorMessage(err, 'Failed to delete pattern'));
      }
    },
    [selectedPattern]
  );

  const applyHighlightSteps = useCallback((steps: WorkflowPatternStepRecord[]) => {
    steps.forEach((step) => {
      if (step.type !== 'APPLY_HIGHLIGHT' || step.disabled) return;
      gridRef.current?.applyHighlightOperation(step.params as ApplyHighlightParams);
    });
  }, []);

  useEffect(() => {
    applyStepsRef.current = applySteps;
    applyHighlightStepsRef.current = applyHighlightSteps;
  }, [applySteps, applyHighlightSteps]);

  const applyPatternSteps = useCallback(async () => {
    if (!selectedPattern || !spreadsheetId || !activeSheetId) return;
    isReplayingRef.current = true;
    setIsApplying(true);
    setApplyError(null);
    setApplyFailedIndex(null);
    setPatternJobError(null);
    setPatternJobProgress(0);
    setPatternJobId(null);
    setPatternJobStatus(null);
    setApplySteps((prev) => prev.map((s) => ({ ...s, status: 'pending', errorMessage: undefined })));
    try {
      const response = await PatternAPI.applyPattern(selectedPattern.id, {
        spreadsheet_id: String(spreadsheetId),
        sheet_id: activeSheetId,
      });
      patternJobStartRef.current = Date.now();
      setPatternJobId(response.job_id);
      setPatternJobStatus(response.status);
    } catch (err) {
      isReplayingRef.current = false;
      const message = extractErrorMessage(err, 'Failed to apply pattern');
      setApplyError(message);
      setIsApplying(false);
      toast.error(message);
    }
  }, [selectedPattern, spreadsheetId, activeSheetId]);

  useEffect(() => {
    if (!patternJobId) return;
    if (activeJobIdRef.current === patternJobId) return;
    activeJobIdRef.current = patternJobId;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const isTerminal = (job: { status: string; finishedAt?: string | null }) =>
      job.status === 'succeeded' || job.status === 'failed' || job.finishedAt != null;

    const poll = async () => {
      try {
        const job = await PatternAPI.getPatternJob(patternJobId);
        if (cancelled) return;
        setPatternJobStatus(job.status);
        setPatternJobProgress(job.progress ?? 0);
        setPatternJobError(job.error_message ?? null);
        setApplySteps((prev) =>
          prev.map((step) => {
            if (job.status === 'succeeded') return { ...step, status: 'success', errorMessage: undefined };
            if (job.status === 'failed' && job.current_step === step.seq) {
              return { ...step, status: 'error', errorMessage: job.error_message ?? 'Failed to apply step' };
            }
            if (job.current_step != null && step.seq < job.current_step) {
              return { ...step, status: 'success', errorMessage: undefined };
            }
            return { ...step, status: 'pending', errorMessage: undefined };
          })
        );
        if (isTerminal(job)) {
          activeJobIdRef.current = null;
          isReplayingRef.current = false;
        }
        if (job.status === 'succeeded') {
          setIsApplying(false);
          patternJobStartRef.current = null;
          gridRef.current?.refresh();
          applyHighlightStepsRef.current(applyStepsRef.current);
          return;
        }
        if (job.status === 'failed') {
          setIsApplying(false);
          setApplyError(job.error_message ?? 'Failed to apply pattern');
          setApplyFailedIndex(job.current_step != null ? Math.max(0, job.current_step - 1) : null);
          patternJobStartRef.current = null;
          return;
        }
        if (job.status === 'queued') {
          const startedAt = patternJobStartRef.current;
          if (startedAt && Date.now() - startedAt > 60000) {
            activeJobIdRef.current = null;
            isReplayingRef.current = false;
            const message = 'Pattern job is still queued after 60s. Worker may be offline.';
            setIsApplying(false);
            setApplyError(message);
            setPatternJobError(message);
            return;
          }
        }
      } catch (err) {
        if (cancelled) return;
        activeJobIdRef.current = null;
        isReplayingRef.current = false;
        setApplyError(extractErrorMessage(err, 'Failed to fetch job status'));
        setIsApplying(false);
        return;
      }
      timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (activeJobIdRef.current === patternJobId) activeJobIdRef.current = null;
    };
  }, [patternJobId]);

  const handleFormulaCommit = useCallback(
    (data: { row: number; col: number; formula: string }) => {
      if (isReplayingRef.current) return;
      const targetRow = data.row + 1;
      const targetCol = data.col + 1;
      const a1 = rowColToA1(targetRow, targetCol) ?? 'A1';
      updateAgentSteps((prev) => [
        ...prev,
        {
          id: createStepId(),
          type: 'APPLY_FORMULA',
          target: { row: targetRow, col: targetCol },
          a1,
          formula: data.formula,
          disabled: false,
          createdAt: new Date().toISOString(),
        },
      ]);
    },
    [updateAgentSteps]
  );

  const handleHeaderRenameCommit = useCallback(
    (payload: { rowIndex: number; colIndex: number; newValue: string; oldValue: string }) => {
      if (isReplayingRef.current) return;
      if (!shouldRecordHeaderRename(payload.rowIndex)) return;
      if (activeSheetId == null) return;
      updateAgentSteps((prev) => {
        const sheetState = renameDedupRef.current[activeSheetId] ?? {};
        const flat = flattenTimelineItems(prev);
        const result = recordRenameColumnStep(
          flat,
          {
            columnIndex: payload.colIndex,
            newName: payload.newValue,
            oldName: payload.oldValue,
            headerRowIndex: HEADER_ROW_INDEX,
          },
          sheetState,
          createStepId,
          Date.now(),
          RENAME_DEDUP_WINDOW_MS
        );
        renameDedupRef.current[activeSheetId] = result.state;
        return result.steps;
      });
    },
    [activeSheetId, updateAgentSteps]
  );

  const handleHighlightCommit = useCallback(
    (payload: ApplyHighlightParams) => {
      if (isReplayingRef.current) return;
      if (activeSheetId == null) return;
      updateAgentSteps((prev) => [
        ...prev,
        {
          id: createStepId(),
          type: 'APPLY_HIGHLIGHT',
          params: payload,
          disabled: false,
          createdAt: new Date().toISOString(),
        },
      ]);
    },
    [activeSheetId, updateAgentSteps]
  );

  const handleExportPattern = useCallback(
    async (name: string, selectedItems: TimelineItem[]) => {
      if (!spreadsheetId || selectedItems.length === 0) return false;
      const payload: CreatePatternPayload = {
        name,
        description: '',
        origin: {
          spreadsheet_id: String(spreadsheetId),
          sheet_id: activeSheetId ?? undefined,
        },
        steps: timelineItemsToCreateSteps(selectedItems),
      };
      setExportingPattern(true);
      try {
        const created = await PatternAPI.createPattern(payload);
        toast.success('Pattern saved');
        setPatterns((prev) => [created, ...prev]);
        return true;
      } catch (err) {
        toast.error(extractErrorMessage(err, 'Failed to save pattern'));
        return false;
      } finally {
        setExportingPattern(false);
      }
    },
    [activeSheetId, spreadsheetId]
  );

  const handleCreatePivotSheet = useCallback(
    async (sourceData: {
      cells: Map<string, { rawInput: string; computedString?: string | null }>;
      rowCount: number;
      colCount: number;
    }) => {
      if (!spreadsheetId || !activeSheetId) return;
      const sourceSheet = sheets.find((s) => s.id === activeSheetId);
      if (!sourceSheet) return;
      try {
        const sheetName = generatePivotSheetName(sheets.map((s) => s.name));
        const newSheet = await SpreadsheetAPI.createSheet(String(spreadsheetId), { name: sheetName });
        await SpreadsheetAPI.resizeSheet(String(spreadsheetId), newSheet.id, 100, 26);
        const initial = createEmptyPivotConfig(activeSheetId);
        await SpreadsheetAPI.upsertPivotConfig(String(spreadsheetId), newSheet.id, {
          sourceSheetId: activeSheetId,
          rows: initial.rows,
          columns: initial.columns,
          values: initial.values,
          showGrandTotalRow: initial.showGrandTotalRow,
        });
        const resp = await SpreadsheetAPI.listSheets(String(spreadsheetId));
        const list = resp.results || [];
        setSheets(list);
        setCreateSheetDefaultName(getNextSheetName(list));
        setPivotSourceDataBySheet((prev) => ({
          ...prev,
          [newSheet.id]: {
            ...sourceData,
            sourceSheetId: activeSheetId,
            sourceSheetName: sourceSheet.name,
          },
        }));
        setActiveSheetId(newSheet.id);
        setShowPivotEditor(true);
        toast.success(`Created pivot sheet: ${sheetName}`);
      } catch (err) {
        toast.error(extractErrorMessage(err, 'Failed to create pivot sheet'));
      }
    },
    [activeSheetId, sheets, spreadsheetId]
  );

  const handlePivotConfigChange = useCallback(
    async (newConfig: PivotConfig) => {
      if (!activeSheetId || !spreadsheetId) return;
      setPivotConfigsBySheet((prev) => ({ ...prev, [activeSheetId]: newConfig }));
      const pivotMeta = pivotSourceDataBySheet[activeSheetId];
      const sourceSheetId = pivotMeta?.sourceSheetId ?? newConfig.sourceSheetId;
      if (!sourceSheetId || !isPivotConfigValid(newConfig)) return;
      try {
        let sourceData = pivotSourceDataBySheet[activeSheetId];
        if (!sourceData) {
          const response = await SpreadsheetAPI.readCellRange(
            String(spreadsheetId),
            sourceSheetId,
            0,
            999,
            0,
            50
          );
          const sourceRowCount = response.sheet_row_count ?? response.row_count;
          const sourceColCount = response.sheet_column_count ?? response.column_count;
          const cells = new Map<string, { rawInput: string; computedString?: string | null }>();
          for (const cell of response.cells) {
            cells.set(`${cell.row_position}:${cell.column_position}`, {
              rawInput: cell.raw_input ?? '',
              computedString: cell.computed_string ?? null,
            });
          }
          const sourceSheet = sheets.find((s) => s.id === sourceSheetId);
          sourceData = {
            cells,
            rowCount: sourceRowCount,
            colCount: sourceColCount,
            sourceSheetId,
            sourceSheetName: sourceSheet?.name ?? 'Unknown',
          };
          setPivotSourceDataBySheet((prev) => ({ ...prev, [activeSheetId]: sourceData! }));
        }
        const columns: SourceColumn[] = [];
        for (let col = 0; col < sourceData.colCount; col++) {
          const cellData = sourceData.cells.get(`0:${col}`);
          const header = (cellData?.rawInput ?? '').trim();
          if (header) columns.push({ index: col, header });
        }
        const sourceRows: SourceRow[] = [];
        for (let row = 1; row < sourceData.rowCount; row++) {
          const rowRecord: SourceRow = {};
          let hasData = false;
          for (let col = 0; col < sourceData.colCount; col++) {
            const cellData = sourceData.cells.get(`${row}:${col}`);
            const value = cellData?.computedString ?? cellData?.rawInput ?? '';
            rowRecord[col] = value;
            if (value.trim()) hasData = true;
          }
          if (hasData) sourceRows.push(rowRecord);
        }
        const pivotResult = buildPivotTable(sourceRows, columns, newConfig);
        const prevDims = pivotDimensionsBySheet[activeSheetId] ?? { rowCount: 0, colCount: 0 };
        const setOps = pivotResultToCellOperations(pivotResult);
        const clearOps = generateClearOperationsForStaleCells(
          prevDims.rowCount,
          prevDims.colCount,
          pivotResult.rowCount,
          pivotResult.colCount
        );
        const allOps: Array<{ operation: 'set' | 'clear'; row: number; column: number; raw_input?: string }> = [
          ...setOps,
          ...clearOps,
        ];
        await SpreadsheetAPI.resizeSheet(
          String(spreadsheetId),
          activeSheetId,
          Math.max(pivotResult.rowCount + 10, prevDims.rowCount + 10, 100),
          Math.max(pivotResult.colCount + 5, prevDims.colCount + 5, 26)
        );
        await SpreadsheetAPI.batchUpdateCells(
          String(spreadsheetId),
          activeSheetId,
          allOps,
          false
        );
        setPivotDimensionsBySheet((prev) => ({
          ...prev,
          [activeSheetId]: { rowCount: pivotResult.rowCount, colCount: pivotResult.colCount },
        }));
        gridRef.current?.refresh();
      } catch (err) {
        toast.error('Failed to update pivot preview');
      }
      void (async () => {
        try {
          await SpreadsheetAPI.upsertPivotConfig(String(spreadsheetId), activeSheetId, {
            sourceSheetId,
            rows: newConfig.rows,
            columns: newConfig.columns,
            values: newConfig.values,
            showGrandTotalRow: newConfig.showGrandTotalRow,
          });
          await SpreadsheetAPI.recomputePivot(String(spreadsheetId), activeSheetId);
        } catch (err) {
          console.error('Background pivot persistence/recompute failed:', err);
        }
      })();
    },
    [activeSheetId, spreadsheetId, pivotSourceDataBySheet, pivotDimensionsBySheet, sheets]
  );

  const handleRefreshPivot = useCallback(() => {
    if (!activeSheetId) return;
    const config = pivotConfigsBySheet[activeSheetId];
    if (config) void handlePivotConfigChange(config);
  }, [activeSheetId, pivotConfigsBySheet, handlePivotConfigChange]);

  const isPivotSheet = useMemo(() => {
    if (!activeSheetId) return false;
    const sheet = sheets.find((s) => s.id === activeSheetId);
    return !!sheet && sheet.kind === 'pivot';
  }, [activeSheetId, sheets]);

  const handleSubmitCreateSheet = async (name: string) => {
    if (!spreadsheetId) return;
    try {
      const newSheet = await SpreadsheetAPI.createSheet(String(spreadsheetId), { name });
      const resp = await SpreadsheetAPI.listSheets(String(spreadsheetId));
      const list = resp.results || [];
      setSheets(list);
      setCreateSheetDefaultName(getNextSheetName(list));
      setActiveSheetId(newSheet.id);
      toast.success('Sheet created');
    } catch (err) {
      toast.error(extractErrorMessage(err, 'Failed to create sheet'));
      throw err;
    }
  };

  const handleRenameSheet = async (sheetId: number, newName: string) => {
    if (!spreadsheetId) return;
    setRenaming(true);
    try {
      const updated = await SpreadsheetAPI.updateSheet(String(spreadsheetId), sheetId, {
        name: newName,
      } as UpdateSheetRequest);
      setSheets((prev) =>
        prev.map((s) => (s.id === sheetId ? { ...s, name: updated.name } : s))
      );
      toast.success('Sheet renamed');
    } catch (err) {
      toast.error(extractErrorMessage(err, 'Failed to rename sheet'));
    } finally {
      setRenaming(false);
    }
  };

  const handleRenameSpreadsheet = async (newName: string) => {
    if (!spreadsheetId) return;
    setRenamingSpreadsheetSaving(true);
    try {
      const updated = await SpreadsheetAPI.updateSpreadsheet(String(spreadsheetId), {
        name: newName,
      });
      setSpreadsheet((prev) => (prev ? { ...prev, name: updated.name } : prev));
      toast.success('Spreadsheet renamed');
    } catch (err) {
      toast.error(extractErrorMessage(err, 'Failed to rename spreadsheet'));
    } finally {
      setRenamingSpreadsheetSaving(false);
    }
  };

  const handleConfirmDeleteSheet = async () => {
    if (!deleteConfirmSheet || !spreadsheetId || !projectId) return;
    setDeletingSheet(true);
    try {
      await SpreadsheetAPI.deleteSheet(String(projectId), String(spreadsheetId), deleteConfirmSheet.id);
      toast.success('Sheet deleted');
      const resp = await SpreadsheetAPI.listSheets(String(spreadsheetId));
      const list = resp.results || [];
      setSheets(list);
      if (list.length === 0) {
        setActiveSheetId(null);
      } else if (activeSheetId === deleteConfirmSheet.id) {
        const deletedIndex = sheets.findIndex((s) => s.id === deleteConfirmSheet.id);
        const next = list[deletedIndex] || list[deletedIndex - 1] || list[0];
        setActiveSheetId(next.id);
      }
    } catch (err) {
      toast.error(extractErrorMessage(err, 'Delete failed.'));
    } finally {
      setDeletingSheet(false);
      setDeleteConfirmSheet(null);
    }
  };

  const activeSheet = sheets.find((s) => s.id === activeSheetId);

  if (error) {
    return (
      <ProtectedRoute renderChildrenWhileLoading>
        <DashboardLayout alerts={[]}>
          <div className="mx-auto max-w-7xl px-6 py-8">
            <div className="flex flex-col items-center justify-center rounded-xl border border-rose-200 bg-rose-50 p-10 text-center">
              <AlertCircle className="h-6 w-6 text-rose-600" aria-hidden="true" />
              <p className="mt-3 text-sm font-semibold text-rose-700">Could not load spreadsheet</p>
              <p className="mt-1 text-xs text-rose-600">{error}</p>
              <Link
                href={buildUrl("/spreadsheets")}
                className="mt-4 inline-flex h-9 items-center rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-95"
              >
                Back to spreadsheets
              </Link>
            </div>
          </div>
        </DashboardLayout>
      </ProtectedRoute>
    );
  }

  if (!spreadsheet && !loading) {
    return (
      <ProtectedRoute renderChildrenWhileLoading>
        <DashboardLayout alerts={[]}>
          <div className="mx-auto max-w-7xl px-6 py-8">
            <div className="flex flex-col items-center justify-center rounded-xl bg-white p-10 text-center shadow-sm ring-1 ring-gray-100">
              <FileSpreadsheet className="h-7 w-7 text-gray-400" aria-hidden="true" />
              <p className="mt-3 text-sm font-semibold text-gray-900">Spreadsheet not found</p>
              <p className="mt-1 text-xs text-gray-500">The spreadsheet you&apos;re looking for doesn&apos;t exist.</p>
              <Link
                href={buildUrl("/spreadsheets")}
                className="mt-4 inline-flex h-9 items-center rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-95"
              >
                Back to spreadsheets
              </Link>
            </div>
          </div>
        </DashboardLayout>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute renderChildrenWhileLoading>
      <DashboardLayout alerts={[]}>
        <div className="mx-auto flex h-[calc(100vh-4rem)] w-full max-w-[1600px] flex-col gap-3 px-2 py-2 sm:h-[calc(100vh-6rem)] sm:gap-4 sm:px-6 sm:py-6">
          <SpreadsheetDetailHeader
            projectId={projectId}
            projectName={projectName}
            spreadsheetName={spreadsheet?.name ?? ''}
            saving={renamingSpreadsheetSaving}
            onRename={handleRenameSpreadsheet}
            loading={loading}
            presenceSlot={<PresenceAvatars users={remoteUsers} />}
            onAnalyzeWithAgent={handleAnalyzeWithAgent}
            analyzeDisabled={loading || activeSheetId == null || !spreadsheet}
            onOpenPatternGenerationAgent={handleOpenPatternGenerationAgent}
            patternGenerationAgentDisabled={loading || !spreadsheet}
            onOpenPivotGenerationAgent={handleOpenPivotGenerationAgent}
            pivotGenerationAgentDisabled={loading || activeSheetId == null || !spreadsheet}
          />

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-gray-100 sm:rounded-xl">
            <div className="flex min-h-0 flex-1 overflow-hidden">
              {activeSheet || loading ? (
                <div className="relative flex h-full min-h-0 min-w-0 w-full overflow-hidden">
                  <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
                    <SpreadsheetGrid
                      ref={gridRef}
                      spreadsheetId={String(spreadsheetId)}
                      sheetId={activeSheet?.id ?? 0}
                      loading={loading || !activeSheet}
                      spreadsheetName={spreadsheet?.name ?? undefined}
                      sheetName={activeSheet?.name}
                      frozenRowCount={activeSheet?.frozen_row_count ?? 0}
                      onSelectionChange={onSelectionChange}
                      collabClientId={collabClientId}
                      remotePresenceUsers={remoteUsers}
                      onFreezeHeaderChange={activeSheet ? ((val) => {
                        setSheets((prev) =>
                          prev.map((s) =>
                            s.id === activeSheet.id ? { ...s, frozen_row_count: val } : s
                          )
                        );
                      }) : undefined}
                      onFormulaCommit={handleFormulaCommit}
                      onHeaderRenameCommit={handleHeaderRenameCommit}
                      onHighlightCommit={handleHighlightCommit}
                      onInsertRowCommit={(payload: InsertRowParams) => {
                        updateAgentSteps((prev) => [
                          ...prev,
                          {
                            id: createStepId(),
                            type: 'INSERT_ROW',
                            params: payload,
                            disabled: false,
                            createdAt: new Date().toISOString(),
                          },
                        ]);
                      }}
                      onInsertColumnCommit={(payload: InsertColumnParams) => {
                        updateAgentSteps((prev) => [
                          ...prev,
                          {
                            id: createStepId(),
                            type: 'INSERT_COLUMN',
                            params: payload,
                            disabled: false,
                            createdAt: new Date().toISOString(),
                          },
                        ]);
                      }}
                      onDeleteColumnCommit={(payload: DeleteColumnParams) => {
                        updateAgentSteps((prev) => [
                          ...prev,
                          {
                            id: createStepId(),
                            type: 'DELETE_COLUMN',
                            params: payload,
                            disabled: false,
                            createdAt: new Date().toISOString(),
                          },
                        ]);
                      }}
                      onFillCommit={(payload: FillSeriesParams) => {
                        updateAgentSteps((prev) => [
                          ...prev,
                          {
                            id: createStepId(),
                            type: 'FILL_SERIES',
                            params: payload,
                            disabled: false,
                            createdAt: new Date().toISOString(),
                          },
                        ]);
                      }}
                      highlightCell={highlightCell}
                      highlightLocations={highlightLocations}
                      onHydrationStatusChange={(status) => setSheetHydrationReady(status === 'ready')}
                      onOpenPivotBuilder={handleCreatePivotSheet}
                    />
                  </div>
                  {activeSheet && isPivotSheet && showPivotEditor ? (
                    <PivotEditorPanel
                      config={
                        pivotConfigsBySheet[activeSheet.id] ||
                        createEmptyPivotConfig(pivotSourceDataBySheet[activeSheet.id]?.sourceSheetId || 0)
                      }
                      sourceSheetName={pivotSourceDataBySheet[activeSheet.id]?.sourceSheetName || 'Unknown'}
                      sourceColumns={(() => {
                        const sd = pivotSourceDataBySheet[activeSheet.id];
                        if (!sd) return [];
                        const cols: SourceColumn[] = [];
                        for (let col = 0; col < sd.colCount; col++) {
                          const cell = sd.cells.get(`0:${col}`);
                          const header = (cell?.rawInput ?? '').trim();
                          if (header) cols.push({ index: col, header });
                        }
                        return cols;
                      })()}
                      sourceRowCount={(() => {
                        const sd = pivotSourceDataBySheet[activeSheet.id];
                        if (!sd) return 0;
                        let count = 0;
                        for (let row = 1; row < sd.rowCount; row++) {
                          for (let col = 0; col < sd.colCount; col++) {
                            const cell = sd.cells.get(`${row}:${col}`);
                            if ((cell?.rawInput ?? '').trim()) {
                              count++;
                              break;
                            }
                          }
                        }
                        return count;
                      })()}
                      onConfigChange={handlePivotConfigChange}
                      onClose={() => setShowPivotEditor(false)}
                      onRefresh={handleRefreshPivot}
                    />
                  ) : (
                    <>
                    <PatternAgentPanelV2
                      loading={loading || patternsLoading}
                      items={activeSheet ? agentSteps : []}
                      patterns={patterns}
                      selectedPatternId={selectedPattern?.id ?? null}
                      applySteps={applySteps}
                      applyError={applyError}
                      applyFailedIndex={applyFailedIndex}
                      isApplying={isApplying}
                      exporting={exportingPattern}
                      onReorder={updateAgentSteps}
                      onUpdateStep={(id, updates) =>
                        updateAgentSteps((prev) => updateTimelineItemById(prev, id, updates))
                      }
                      onDeleteStep={(id) =>
                        updateAgentSteps((prev) => deleteTimelineItemById(prev, id))
                      }
                      onMoveStepOutOfGroup={(groupId, step) =>
                        updateAgentSteps((prev) => moveStepOutOfGroup(prev, groupId, step))
                      }
                      onHoverStep={(step) => {
                        if (step.type === 'APPLY_FORMULA') {
                          setHighlightCell({ row: step.target.row - 1, col: step.target.col - 1 });
                        }
                      }}
                      onClearHover={() => setHighlightCell(null)}
                      onExportPattern={handleExportPattern}
                      onSelectPattern={loadPatternDetail}
                      onDeletePattern={handleDeletePattern}
                      onApplyPattern={applyPatternSteps}
                      onRetryApply={applyPatternSteps}
                      disableApplyPattern={!sheetHydrationReady}
                      applyJobStatus={patternJobStatus}
                      applyJobProgress={patternJobProgress}
                      applyJobError={patternJobError}
                    />
                    </>
                  )}
                </div>
              ) : sheets.length === 0 ? (
                <div className="flex h-full w-full items-center justify-center p-10">
                  <div className="flex flex-col items-center justify-center text-center">
                    <FileSpreadsheet className="h-10 w-10 text-gray-300" aria-hidden="true" />
                    <p className="mt-3 text-sm font-semibold text-gray-900">No sheets yet</p>
                    <p className="mt-1 text-xs text-gray-500">Create a sheet to start editing.</p>
                    <button
                      type="button"
                      onClick={() => setCreateSheetOpen(true)}
                      className="mt-4 inline-flex h-9 items-center rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-95"
                    >
                      Create sheet
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
            <SheetTabBarBottom
              sheets={sheets}
              activeSheetId={activeSheetId}
              onSelect={setActiveSheetId}
              onCreate={() => {
                setCreateSheetDefaultName(getNextSheetName(sheets));
                setCreateSheetOpen(true);
              }}
              onRename={handleRenameSheet}
              onRequestDelete={setDeleteConfirmSheet}
              canDelete={(sheet) => sheets.length > 1 || sheet.id !== activeSheetId}
              renaming={renaming}
              loading={loading}
            />
          </div>
        </div>

        <CreateSheetDialog
          open={createSheetOpen}
          onOpenChange={setCreateSheetOpen}
          defaultName={createSheetDefaultName}
          existingNames={sheets.map((s) => s.name)}
          onSubmit={handleSubmitCreateSheet}
        />

        <ConfirmDialog
          isOpen={!!deleteConfirmSheet}
          type="danger"
          title="Delete sheet?"
          message={`"${deleteConfirmSheet?.name ?? ''}" will be hidden. This cannot be undone from the UI.`}
          confirmText={deletingSheet ? 'Deleting…' : 'Delete'}
          cancelText="Cancel"
          onConfirm={handleConfirmDeleteSheet}
          onCancel={() => setDeleteConfirmSheet(null)}
        />
      </DashboardLayout>
    </ProtectedRoute>
  );
}
