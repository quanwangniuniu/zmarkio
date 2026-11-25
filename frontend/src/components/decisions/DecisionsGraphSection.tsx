'use client';

import { ChevronRight, Loader2, Maximize2 } from 'lucide-react';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import DecisionTree, { type DecisionTreeHandle, type DecisionTreeViewMode } from '@/components/decisions/DecisionTree';
import DecisionFullscreenPanel from '@/components/decisions/DecisionFullscreenPanel';
import DecisionTreeDetailPanel from '@/components/decisions/DecisionTreeDetailPanel';
import DecisionTreeNavigator from '@/components/decisions/DecisionTreeNavigator';
import { nestedProjectPath } from '@/lib/projectNestedRoutes';

const DecisionTreeFlow = dynamic(() => import('@/components/decisions/DecisionTreeFlow'), {
  ssr: false,
  loading: () => (
    <div className="flex h-full min-h-[240px] items-center justify-center text-sm text-gray-500">
      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
      Loading graph…
    </div>
  ),
});
import {
  formatDecisionDayKey,
  type TimelineGranularity,
} from '@/components/decisions/decisionTreeLayout';
import { useDecisionGraphLinks } from '@/components/decisions/hooks/useDecisionGraphLinks';
import { computeDecisionGraphStats } from '@/components/decisions/decisionGraphStats';
import { hasSavedDecisionGraphLayout } from '@/components/decisions/decisionGraphLayoutStorage';
import {
  type DecisionMapMode,
} from '@/components/decisions/decisionMapUrlState';
import { useDecisionMapUrlState } from '@/components/decisions/hooks/useDecisionMapUrlState';
import { DecisionAPI } from '@/lib/api/decisionApi';
import type { DecisionGraphEdge, DecisionGraphNode, DecisionGraphResponse } from '@/types/decision';
import { toTopicKey } from '@/components/decisions/decisionValidation';

type DecisionGraphWindow = Window & {
  __decisionGraphZoomIn?: () => void;
  __decisionGraphZoomOut?: () => void;
};

interface Props {
  graph: DecisionGraphResponse | null;
  projectId?: number | string | null;
  canEdit: boolean;
  canCreate?: boolean;
  onEditDecision: (node: DecisionGraphNode) => void;
  onDeleteDecision?: (node: DecisionGraphNode) => void;
  onOpenFullPage?: (idOrSlug: number | string, projectId?: number | string | null) => void;
  createRequestKey?: number;
  onDecisionUpdated?: (opts?: {
    fullReload?: boolean;
    nodePatch?: { id: number } & Partial<DecisionGraphNode>;
    edges?: DecisionGraphEdge[];
  }) => void | Promise<void>;
}

export default function DecisionsGraphSection({
  graph,
  projectId,
  canEdit,
  canCreate = false,
  onEditDecision,
  onDeleteDecision,
  onOpenFullPage,
  createRequestKey = 0,
  onDecisionUpdated,
}: Props) {
  const { timelineMode, fullscreen, fullscreenSelectedId, updateMapUrl } = useDecisionMapUrlState();

  const [collapsed, setCollapsed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [startInEditMode, setStartInEditMode] = useState(false);
  const [provisionalDecisionId, setProvisionalDecisionId] = useState<number | null>(null);
  const [focusDateKey, setFocusDateKey] = useState<string | null>(null);
  const [focusNodeId, setFocusNodeId] = useState<number | null>(null);
  const [selectedTopic, setSelectedTopic] = useState<string | null>(null);
  const [viewResetKey, setViewResetKey] = useState(0);
  const [layoutResetKey, setLayoutResetKey] = useState(0);
  const [embeddedZoomPercent, setEmbeddedZoomPercent] = useState(100);
  const [fullscreenZoomPercent, setFullscreenZoomPercent] = useState(100);
  const embeddedTreeRef = useRef<DecisionTreeHandle>(null);
  const fullscreenTreeRef = useRef<DecisionTreeHandle>(null);
  const nodes = useMemo(() => graph?.nodes ?? [], [graph?.nodes]);
  const serverEdges = useMemo(() => graph?.edges ?? [], [graph?.edges]);
  const nodeCount = nodes.length;
  const topicCount = graph?.topics?.length ?? 0;
  const mapDataReady = nodeCount > 0 || topicCount > 0;

  const links = useDecisionGraphLinks(
    projectId,
    nodes,
    serverEdges,
    canEdit,
    (edges) => onDecisionUpdated?.({ edges }),
  );
  const { handleAutoLinkSequence, linkingEnabled } = links;
  const isGraphMode = timelineMode === 'tree';
  const graphStats = useMemo(
    () => computeDecisionGraphStats(nodes, links.edges),
    [links.edges, nodes],
  );

  const autoLinkAttemptedRef = useRef<number | null>(null);
  const numericProjectId = useMemo(
    () => (typeof projectId === 'string' ? parseInt(projectId, 10) : (projectId ?? null)),
    [projectId],
  );
  useEffect(() => {
    if (!isGraphMode || !canEdit || !linkingEnabled || nodes.length < 2) return;
    if (serverEdges.length > 0) return;
    if (hasSavedDecisionGraphLayout(numericProjectId)) return;
    if (!numericProjectId) return;
    if (autoLinkAttemptedRef.current === numericProjectId) return;
    autoLinkAttemptedRef.current = numericProjectId;

    void handleAutoLinkSequence().then((linked) => {
      if (linked) setLayoutResetKey((key) => key + 1);
    });
  }, [
    canEdit,
    handleAutoLinkSequence,
    isGraphMode,
    linkingEnabled,
    nodes.length,
    numericProjectId,
    serverEdges.length,
  ]);

  const selectedGraphNode = useMemo(
    () =>
      nodes.find(
        (n) =>
          String(n.id) === String(fullscreenSelectedId) ||
          n.slug === fullscreenSelectedId,
      ) ?? null,
    [nodes, fullscreenSelectedId],
  );

  const resolvedSelectedNodeId = useMemo(
    () =>
      selectedGraphNode?.id ??
      (typeof fullscreenSelectedId === 'number' ? fullscreenSelectedId : null) ??
      provisionalDecisionId,
    [selectedGraphNode, fullscreenSelectedId, provisionalDecisionId],
  );

  const handleSelectNode = (id: number) => {
    setStartInEditMode(false);
    // URLs are slug-only; emit the decision slug, never the numeric id.
    const slug = nodes.find((n) => n.id === id)?.slug;
    updateMapUrl({ fullscreen: true, decisionId: slug ?? id });
  };

  const handleEditNodeInTree = (node: DecisionGraphNode) => {
    setStartInEditMode(true);
    updateMapUrl({ fullscreen: true, decisionId: node.slug ?? node.id });
  };

  const handleDeleteNodeInTree = async (node: DecisionGraphNode) => {
    if (!canEdit) return;
    const title = node.title?.trim() || 'Untitled decision';
    if (!window.confirm(`Delete "${title}"?`)) return;
    try {
      await DecisionAPI.deleteDecision(node.slug ?? node.id, node.projectId ?? projectId ?? null);
      toast.success('Decision deleted');
      if (resolvedSelectedNodeId === node.id) {
        updateMapUrl({ decisionId: null });
        setStartInEditMode(false);
      }
      await onDecisionUpdated?.({ fullReload: true });
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as Error)?.message ||
        'Failed to delete decision';
      toast.error(detail);
    }
  };

  const openFullscreenMap = () => {
    setSelectedTopic(null);
    updateMapUrl({ fullscreen: true });
  };

  const handleTreeCreateDecision = useCallback(async () => {
    if (!projectId || creating || !canCreate) return;
    setCreating(true);
    try {
      const draft = await DecisionAPI.createDraft(projectId);
      if (draft.id == null) {
        throw new Error('Draft created without id');
      }
      updateMapUrl({ fullscreen: true, decisionId: draft.slug ?? draft.id });
      setProvisionalDecisionId(draft.id);
      setStartInEditMode(true);
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as Error)?.message ||
        'Failed to create decision';
      toast.error(detail);
    } finally {
      setCreating(false);
    }
  }, [canCreate, creating, projectId, updateMapUrl]);

  const handleTreeCreateChildDecision = useCallback(
    async (parentNode: DecisionGraphNode) => {
      if (!projectId || creating || !canCreate) return;
      setCreating(true);
      try {
        const draft = await DecisionAPI.createDraft(projectId);
        if (draft.id == null) {
          throw new Error('Draft created without id');
        }

        const parentTopic = parentNode.topic || null;
        await DecisionAPI.patchDraft(
          draft.slug ?? draft.id,
          {
            ...(parentTopic ? { topic: parentTopic } : {}),
            parentDecisionIds: [parentNode.id],
          },
          projectId,
        );

        toast.success('Follow-up decision added');
        await onDecisionUpdated?.({ fullReload: true });
        updateMapUrl({ fullscreen: true, decisionId: draft.slug ?? draft.id });
        setProvisionalDecisionId(draft.id);
        setStartInEditMode(true);
      } catch (err) {
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          (err as Error)?.message ||
          'Failed to create child decision';
        toast.error(detail);
      } finally {
        setCreating(false);
      }
    },
    [canCreate, creating, onDecisionUpdated, projectId, updateMapUrl],
  );

  const lastCreateRequestKey = useRef(createRequestKey);
  useEffect(() => {
    if (createRequestKey === lastCreateRequestKey.current) return;
    lastCreateRequestKey.current = createRequestKey;
    void handleTreeCreateDecision();
  }, [createRequestKey, handleTreeCreateDecision]);

  const jumpToDate = (dayKey: string) => {
    setSelectedTopic(null);
    setFocusNodeId(null);
    setFocusDateKey(dayKey);
  };

  const jumpToNode = (node: DecisionGraphNode) => {
    setSelectedTopic(null);
    setFocusDateKey(null);
    setFocusNodeId(node.id);
    if (fullscreen) {
      handleSelectNode(node.id);
    }
  };

  const jumpToTopic = (topic: string) => {
    setSelectedTopic(topic);
    setFocusDateKey(null);
    setFocusNodeId(null);
    updateMapUrl({ mode: 'auto' });
    requestAnimationFrame(() => {
      embeddedTreeRef.current?.jumpToTopic(topic);
      fullscreenTreeRef.current?.jumpToTopic(topic);
    });
  };

  const handleTimelineModeChange = (mode: DecisionMapMode) => {
    setSelectedTopic(mode === 'auto' ? selectedTopic : null);
    setFocusDateKey(null);
    setFocusNodeId(null);
    updateMapUrl({ mode });
    setViewResetKey((key) => key + 1);
  };

  useEffect(() => {
    if (viewResetKey === 0) return;
    requestAnimationFrame(() => {
      embeddedTreeRef.current?.resetView();
      fullscreenTreeRef.current?.resetView();
    });
  }, [timelineMode, viewResetKey]);

  const handleMoveDecisionToTopic = async (
    decisionId: number,
    fromProjectId: number | string,
    topic: string,
  ) => {
    try {
      await DecisionAPI.moveDecisionToTopic(decisionId, fromProjectId, topic);
      toast.success('Decision moved');
      await onDecisionUpdated?.({ fullReload: true });
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string; topic?: string } } })?.response?.data?.detail ||
        (err as { response?: { data?: { topic?: string } } })?.response?.data?.topic ||
        (err as Error)?.message ||
        'Failed to move decision';
      toast.error(detail);
    }
  };

  const handleRenameTopic = async (topic: string, title: string) => {
    if (!projectId) return;
    try {
      await DecisionAPI.renameDecisionTopic(projectId, topic, title);
      toast.success('Topic renamed');
      await onDecisionUpdated?.({ fullReload: true });
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string; title?: string } } })?.response?.data?.detail ||
        (err as { response?: { data?: { title?: string } } })?.response?.data?.title ||
        (err as Error)?.message ||
        'Failed to rename topic';
      toast.error(detail);
    }
  };

  const handleCreateTopic = async (title: string) => {
    if (!projectId) return;
    try {
      await DecisionAPI.createDecisionTopic(projectId, toTopicKey(title), title);
      toast.success('Topic added');
      await onDecisionUpdated?.({ fullReload: true });
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string; title?: string } } })?.response?.data?.detail ||
        (err as { response?: { data?: { title?: string } } })?.response?.data?.title ||
        (err as Error)?.message ||
        'Failed to add topic';
      toast.error(detail);
      // Keeps the new-topic input open with the error.
      throw new Error(detail);
    }
  };

  const handleDeleteTopic = async (topic: string) => {
    if (!projectId) return;
    try {
      await DecisionAPI.deleteDecisionTopic(projectId, topic);
      toast.success('Topic deleted');
      await onDecisionUpdated?.({ fullReload: true });
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as Error)?.message ||
        'Failed to delete topic';
      toast.error(detail);
    }
  };

  const treeCommon = {
    nodes,
    edges: [],
    topics: graph?.topics ?? [],
    projectId,
    mode: 'viewer' as const,
    focusDateKey,
    focusNodeId,
    timelineGranularity: timelineMode === 'auto' || timelineMode === 'tree' ? null : timelineMode,
    viewMode: 'topics' as DecisionTreeViewMode,
    onEditDecision,
    onCreateDecision: canCreate ? handleTreeCreateDecision : undefined,
    canDelete: canEdit,
    onDelete: onDeleteDecision,
    linkingEnabled: false,
    linkingDisabled: true,
    onMoveDecisionToTopic: canEdit ? handleMoveDecisionToTopic : undefined,
    onRenameTopic: canEdit ? handleRenameTopic : undefined,
    onCreateTopic: canEdit ? handleCreateTopic : undefined,
    onDeleteTopic: canEdit ? handleDeleteTopic : undefined,
    getDecisionUrl: (idOrSlug: number | string, pid?: number | string | null) =>
      pid ? nestedProjectPath(pid, `/decisions/${idOrSlug}`) : `/decisions/${idOrSlug}`,
    getReviewUrl: (idOrSlug: number | string, pid?: number | string | null) =>
      pid ? nestedProjectPath(pid, `/decisions/${idOrSlug}/review`) : `/decisions/${idOrSlug}/review`,
  };

  const embeddedViewport = {
    zoomPercent: embeddedZoomPercent,
    onZoomIn: () => {
      if (isGraphMode && typeof window !== 'undefined') {
        (window as DecisionGraphWindow).__decisionGraphZoomIn?.();
        return;
      }
      embeddedTreeRef.current?.zoomIn();
    },
    onZoomOut: () => {
      if (isGraphMode && typeof window !== 'undefined') {
        (window as DecisionGraphWindow).__decisionGraphZoomOut?.();
        return;
      }
      embeddedTreeRef.current?.zoomOut();
    },
    onJumpToToday: () => embeddedTreeRef.current?.jumpToToday(),
    showToday: true,
  };

  const fullscreenViewport = {
    zoomPercent: fullscreenZoomPercent,
    onZoomIn: () => {
      if (isGraphMode && typeof window !== 'undefined') {
        (window as DecisionGraphWindow).__decisionGraphZoomIn?.();
        return;
      }
      fullscreenTreeRef.current?.zoomIn();
    },
    onZoomOut: () => {
      if (isGraphMode && typeof window !== 'undefined') {
        (window as DecisionGraphWindow).__decisionGraphZoomOut?.();
        return;
      }
      fullscreenTreeRef.current?.zoomOut();
    },
    onJumpToToday: () => fullscreenTreeRef.current?.jumpToToday(),
    showToday: true,
  };

  const embeddedTree = isGraphMode ? (
    <DecisionTreeFlow
      ref={embeddedTreeRef}
      nodes={nodes}
      edges={links.edges}
      projectId={numericProjectId}
      topics={graph?.topics ?? []}
      canEdit={canEdit}
      canCreate={canCreate}
      focusNodeId={focusNodeId}
      selectedNodeId={fullscreen ? resolvedSelectedNodeId : null}
      linkingEnabled={links.linkingEnabled}
      linkingDisabled={links.linkingDisabled}
      onCreateDecision={canCreate ? handleTreeCreateDecision : undefined}
      onCreateChildDecision={canCreate ? handleTreeCreateChildDecision : undefined}
      onEditDecision={handleEditNodeInTree}
      onDeleteDecision={handleDeleteNodeInTree}
      onCreateTopic={canEdit ? handleCreateTopic : undefined}
      onRenameTopic={canEdit ? handleRenameTopic : undefined}
      onDeleteTopic={canEdit ? handleDeleteTopic : undefined}
      onCreateLink={links.handleCreateLink}
      onRemoveLink={links.handleRemoveLink}
      onZoomPercentChange={setEmbeddedZoomPercent}
      onSelectNode={handleSelectNode}
      layoutResetKey={layoutResetKey}
      viewportActive={!fullscreen}
    />
  ) : (
    <DecisionTree
      ref={embeddedTreeRef}
      {...treeCommon}
      autoFocusToday={!focusDateKey && focusNodeId == null}
      onZoomPercentChange={setEmbeddedZoomPercent}
      onSelectNode={handleSelectNode}
    />
  );

  const fullscreenTree = isGraphMode ? (
    <DecisionTreeFlow
      ref={fullscreenTreeRef}
      nodes={nodes}
      edges={links.edges}
      projectId={numericProjectId}
      topics={graph?.topics ?? []}
      canEdit={canEdit}
      canCreate={canCreate}
      focusNodeId={focusNodeId}
      selectedNodeId={fullscreen ? resolvedSelectedNodeId : null}
      linkingEnabled={links.linkingEnabled}
      linkingDisabled={links.linkingDisabled}
      onCreateDecision={canCreate ? handleTreeCreateDecision : undefined}
      onCreateChildDecision={canCreate ? handleTreeCreateChildDecision : undefined}
      onEditDecision={handleEditNodeInTree}
      onDeleteDecision={handleDeleteNodeInTree}
      onCreateTopic={canEdit ? handleCreateTopic : undefined}
      onRenameTopic={canEdit ? handleRenameTopic : undefined}
      onDeleteTopic={canEdit ? handleDeleteTopic : undefined}
      onCreateLink={links.handleCreateLink}
      onRemoveLink={links.handleRemoveLink}
      onZoomPercentChange={setFullscreenZoomPercent}
      onSelectNode={handleSelectNode}
      layoutResetKey={layoutResetKey}
      viewportActive={fullscreen}
    />
  ) : (
    <DecisionTree
      ref={fullscreenTreeRef}
      {...treeCommon}
      autoFocusToday={!focusDateKey && focusNodeId == null && resolvedSelectedNodeId == null}
      onZoomPercentChange={setFullscreenZoomPercent}
      selectedNodeId={resolvedSelectedNodeId}
      onSelectNode={handleSelectNode}
    />
  );

  const discardProvisionalDecision = async (opts?: { closeFullscreen?: boolean }) => {
    if (provisionalDecisionId == null) return;
    const id = provisionalDecisionId;
    setProvisionalDecisionId(null);
    try {
      await DecisionAPI.deleteDecision(id, projectId ?? null);
      await onDecisionUpdated?.({ fullReload: true });
    } catch {
      toast.error('Failed to discard unsaved decision');
    } finally {
      updateMapUrl(
        opts?.closeFullscreen
          ? { fullscreen: false, decisionId: null }
          : { decisionId: null },
      );
      setStartInEditMode(false);
      if (opts?.closeFullscreen) {
        setSelectedTopic(null);
      }
    }
  };

  const closeFullscreen = () => {
    if (provisionalDecisionId != null) {
      void discardProvisionalDecision({ closeFullscreen: true });
      return;
    }
    setSelectedTopic(null);
    setStartInEditMode(false);
    updateMapUrl({ fullscreen: false, decisionId: null });
  };

  if (!mapDataReady && !fullscreen) {
    return null;
  }

  const heightClass = nodeCount <= 2 && topicCount <= 2 ? 'h-[360px]' : 'h-[600px]';

  const fullscreenPanel = (
    <DecisionFullscreenPanel
      open={fullscreen}
      onClose={closeFullscreen}
      onBack={closeFullscreen}
      title="Decision Map"
      splitLayout
    >
      {creating && resolvedSelectedNodeId == null ? (
        <aside className="flex h-full w-[400px] shrink-0 flex-col items-center justify-center gap-2 border-r border-gray-200 bg-gray-50 text-sm text-gray-500">
          <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
          Creating decision…
        </aside>
      ) : null}
      {resolvedSelectedNodeId != null ? (
        <DecisionTreeDetailPanel
          key={resolvedSelectedNodeId}
          decisionId={resolvedSelectedNodeId}
          decisionSlug={selectedGraphNode?.slug ?? null}
          projectId={projectId ?? selectedGraphNode?.projectId ?? null}
          graphNodeStatus={selectedGraphNode?.status ?? 'DRAFT'}
          canEdit={canEdit}
          startInEditMode={startInEditMode}
          onStartInEditModeConsumed={() => setStartInEditMode(false)}
          isProvisional={resolvedSelectedNodeId === provisionalDecisionId}
          onProvisionalSaved={() => setProvisionalDecisionId(null)}
          onDiscardProvisional={discardProvisionalDecision}
          onClose={() => {
            if (resolvedSelectedNodeId === provisionalDecisionId) {
              void discardProvisionalDecision();
              return;
            }
            setSelectedTopic(null);
            setStartInEditMode(false);
            updateMapUrl({ decisionId: null });
          }}
          onOpenFullPage={onOpenFullPage}
          onUpdated={onDecisionUpdated}
        />
      ) : null}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col bg-white">
        <div className="shrink-0 border-b border-gray-100 bg-gray-50/80 px-3 py-2">
          <DecisionTreeNavigator
            nodes={nodes}
            topics={graph?.topics ?? []}
            onJumpToDate={jumpToDate}
            onJumpToNode={jumpToNode}
            onJumpToTopic={jumpToTopic}
            selectedTopic={selectedTopic}
            viewport={fullscreenViewport}
            timelineMode={timelineMode}
            onTimelineModeChange={handleTimelineModeChange}
          />
        </div>
        <div className="relative min-h-0 flex-1">
          {!mapDataReady ? (
            <div className="flex h-full min-h-[240px] items-center justify-center text-sm text-gray-500">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
              Loading decision map…
            </div>
          ) : (
            <div className="absolute inset-0">{fullscreenTree}</div>
          )}
        </div>
      </div>
    </DecisionFullscreenPanel>
  );

  return (
    <>
      {mapDataReady && !fullscreen ? (
      <div className="border-b border-gray-100 px-5 py-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="min-w-0 text-[13px] font-semibold uppercase text-slate-900" style={{ letterSpacing: 0 }}>
            Decision Map
          </h3>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={openFullscreenMap}
              aria-label="Open decision map fullscreen"
              title="Fullscreen"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
            >
              <Maximize2 className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => setCollapsed((v) => !v)}
              aria-expanded={!collapsed}
              aria-label={collapsed ? 'Expand decision map' : 'Collapse decision map'}
              title={collapsed ? 'Expand' : 'Collapse'}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-500 transition hover:bg-slate-50 hover:text-slate-900"
            >
              <ChevronRight
                className={`h-4 w-4 transition-transform duration-200 ${collapsed ? '' : 'rotate-90'}`}
              />
            </button>
          </div>
        </div>
        {!collapsed && isGraphMode ? (
          <p className="mb-2 text-[12px] text-slate-500">
            {graphStats.nodeCount} decisions · {graphStats.edgeCount} links · {graphStats.rootCount} roots ·{' '}
            {graphStats.orphanCount} standalone
          </p>
        ) : null}
        {!collapsed && (
          <>
            <div className={`relative w-full ${heightClass}`}>{embeddedTree}</div>
          </>
        )}
      </div>
      ) : null}

      {fullscreenPanel}
    </>
  );
}
