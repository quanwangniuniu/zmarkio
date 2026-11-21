'use client';

import {
  Circle,
  LayoutGrid,
  Pencil,
  Pin,
  Plus,
  Trash2,
  Workflow,
} from 'lucide-react';
import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import ReactFlow, {
  Background,
  BackgroundVariant,
  ConnectionLineType,
  Controls,
  Handle,
  MarkerType,
  Position,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type NodeChange,
  type Node,
  type NodeProps,
  type OnEdgesDelete,
  type ReactFlowInstance,
  type Viewport,
} from 'reactflow';
import 'reactflow/dist/style.css';
import DecisionGraphMiniMap from '@/components/decisions/DecisionGraphMiniMap';
import DecisionStatusPill from '@/components/decisions/DecisionStatusPill';
import { layoutDecisionGraphNodes, layoutDecisionGraphNodesAsync } from '@/components/decisions/decisionGraphLayout';
import {
  clearDecisionGraphPositions,
  loadDecisionGraphPositions,
  positionsFromNodes,
  saveDecisionGraphPositions,
  type SavedGraphPositions,
} from '@/components/decisions/decisionGraphLayoutStorage';
import { neighborNodeIds, PARTIAL_FIT_MIN_ZOOM } from '@/components/decisions/decisionGraphViewport';
import {
  isValidSavedViewport,
  loadDecisionGraphViewport,
  loadRememberGraphViewport,
  saveDecisionGraphViewport,
  saveRememberGraphViewport,
} from '@/components/decisions/decisionGraphViewportStorage';
import { resolveDecisionGraphViewport } from '@/components/decisions/decisionGraphViewportApply';
import {
  boundsForNodes,
  readContainerSize,
  viewportToFitBounds,
} from '@/components/decisions/decisionGraphViewportFit';
import type { DecisionTreeHandle } from '@/components/decisions/DecisionTree';
import type { DecisionGraphEdge, DecisionGraphNode, DecisionGraphTopic } from '@/types/decision';

interface DecisionTreeFlowProps {
  nodes: DecisionGraphNode[];
  edges: DecisionGraphEdge[];
  projectId?: number | null;
  topics?: DecisionGraphTopic[];
  canEdit?: boolean;
  canCreate?: boolean;
  selectedNodeId?: number | null;
  focusNodeId?: number | null;
  linkingEnabled?: boolean;
  linkingDisabled?: boolean;
  onSelectNode?: (id: number) => void;
  onCreateDecision?: () => void | Promise<void>;
  onCreateChildDecision?: (node: DecisionGraphNode) => void | Promise<void>;
  followUpError?: { parentId: number; message: string } | null;
  onEditDecision?: (node: DecisionGraphNode) => void;
  onDeleteDecision?: (node: DecisionGraphNode) => void;
  onCreateTopic?: (title: string) => void | Promise<void>;
  onRenameTopic?: (topic: string, title: string) => void | Promise<void>;
  onDeleteTopic?: (topic: string) => void | Promise<void>;
  onCreateLink?: (fromId: number, toId: number) => void;
  onRemoveLink?: (fromId: number, toId: number) => void;
  onZoomPercentChange?: (percent: number) => void;
  /** Bump to discard saved positions and re-apply auto layout (e.g. after bulk auto-link). */
  layoutResetKey?: number;
  /** When false, skip auto-fit / remember-viewport (hidden duplicate ReactFlow instance). */
  viewportActive?: boolean;
}

type DecisionNodeData = {
  kind: 'decision';
  decisionId: number;
  label: string;
  meta: string;
  status: DecisionGraphNode['status'];
  topicLabel?: string | null;
  riskLevel?: DecisionGraphNode['riskLevel'];
};

type FlowNodeData = DecisionNodeData;
type DecisionGraphWindow = Window & {
  __decisionGraphZoomIn?: () => void;
  __decisionGraphZoomOut?: () => void;
};

const GRAPH_EDGE_VISUAL = {
  stroke: '#94a3b8',
  strokeWidth: 2,
  strokeDasharray: '10 8',
} as const;

const EDGE_STYLES: Record<
  NonNullable<DecisionGraphEdge['edgeType']>,
  { stroke: string; strokeWidth: number; strokeDasharray?: string }
> = {
  FOLLOW_UP: { ...GRAPH_EDGE_VISUAL },
  RELATED: { ...GRAPH_EDGE_VISUAL },
};

const edgeVisuals = (edgeType?: DecisionGraphEdge['edgeType'] | null) =>
  EDGE_STYLES[edgeType ?? 'RELATED'] ?? EDGE_STYLES.RELATED;

const buildFlowEdge = (
  edge: DecisionGraphEdge,
  linkingEnabled: boolean,
  linkingDisabled: boolean,
): Edge => {
  const visuals = edgeVisuals(edge.edgeType);
  return {
    id: `decision-edge-${edge.from}-${edge.to}-${edge.edgeType ?? 'RELATED'}`,
    source: flowNodeIdForDecision(edge.from),
    target: flowNodeIdForDecision(edge.to),
    data: { from: edge.from, to: edge.to, edgeType: edge.edgeType ?? 'RELATED' },
    type: 'smoothstep',
    animated: false,
    zIndex: 1,
    style: {
      stroke: visuals.stroke,
      strokeWidth: visuals.strokeWidth,
      strokeDasharray: visuals.strokeDasharray,
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      color: visuals.stroke,
      width: 18,
      height: 18,
    },
    deletable: linkingEnabled && !linkingDisabled,
  };
};
const DECISION_NODE_WIDTH = 320;
const DECISION_NODE_HEIGHT = 128;
const READABLE_MAX_ZOOM = 1;
const GRAPH_MAX_ZOOM = 1.9;
const GRAPH_ZOOM_STEP = 0.05;
/** Must match viewportToFitBounds minZoom so setViewport is not clamped. */
const GRAPH_MIN_ZOOM = 0.1;
const LAYOUT_OPTIONS = { nodeWidth: DECISION_NODE_WIDTH, nodeHeight: DECISION_NODE_HEIGHT };

const titleForNode = (node: DecisionGraphNode) => {
  const title = (node.title || '').trim();
  return title || 'Untitled decision';
};

const sortDecisionNodes = (items: DecisionGraphNode[]) =>
  [...items].sort((a, b) => {
    const aSeq = a.projectSeq ?? Number.MAX_SAFE_INTEGER;
    const bSeq = b.projectSeq ?? Number.MAX_SAFE_INTEGER;
    if (aSeq !== bSeq) return aSeq - bSeq;
    return new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
  });

const flowNodeIdForDecision = (id: number) => `decision-${id}`;

const isDecisionNodeId = (id?: string | null) => Boolean(id?.startsWith('decision-'));
const decisionIdFromFlowNode = (id?: string | null) => {
  if (!isDecisionNodeId(id)) return null;
  const numericId = Number(id?.replace('decision-', ''));
  return Number.isFinite(numericId) ? numericId : null;
};

const DEFAULT_VIEWPORT: Viewport = { x: 0, y: 0, zoom: 0.5 };

const readInitialViewport = (projectId: number | null | undefined): Viewport => {
  if (typeof window === 'undefined' || !projectId || !loadRememberGraphViewport()) {
    return DEFAULT_VIEWPORT;
  }
  const saved = loadDecisionGraphViewport(projectId);
  return isValidSavedViewport(saved) ? saved : DEFAULT_VIEWPORT;
};

const FlowHandle = ({ type, position }: { type: 'source' | 'target'; position: Position }) => (
  <Handle
    type={type}
    position={position}
    className="!h-3.5 !w-3.5 !border-2 !border-white !bg-white !shadow-[0_0_0_1.5px_#a8a8a8]"
  />
);

const DecisionFlowNode = memo(function DecisionFlowNode({ data, selected }: NodeProps<DecisionNodeData>) {
  return (
    <div
      className={`relative flex h-[128px] w-[320px] flex-col rounded-xl border bg-white px-4 py-3 text-left shadow-[0_10px_28px_rgba(15,23,42,0.10)] transition ${
        selected
          ? 'border-[#7C3AED] ring-2 ring-[#7C3AED]/20'
          : 'border-slate-200 hover:border-[#3CCED7]/60'
      }`}
    >
      <FlowHandle type="target" position={Position.Left} />
      <FlowHandle type="source" position={Position.Right} />
      <div className="mb-2 flex items-center justify-between gap-2">
        <DecisionStatusPill status={data.status} />
        {data.meta ? (
          <span className="shrink-0 text-[12px] font-semibold tabular-nums text-slate-500">{data.meta}</span>
        ) : null}
      </div>
      <div className="line-clamp-2 text-[16px] font-semibold leading-snug text-slate-950">{data.label}</div>
      {data.topicLabel ? (
        <div className="mt-auto truncate pt-1.5 text-[12px] font-medium text-slate-500">{data.topicLabel}</div>
      ) : null}
    </div>
  );
});

const GraphZoomControls = ({
  viewportActive,
  zoomPercent,
  onZoomIn,
  onZoomOut,
}: {
  viewportActive: boolean;
  zoomPercent: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
}) => {
  const lastPointerZoomAtRef = useRef(0);

  const triggerZoom = useCallback(
    (direction: 'in' | 'out', source: 'pointer' | 'click') => {
      if (!viewportActive) return;
      const now = Date.now();
      if (source === 'click' && now - lastPointerZoomAtRef.current < 250) return;
      if (source === 'pointer') lastPointerZoomAtRef.current = now;
      if (direction === 'in') {
        onZoomIn();
      } else {
        onZoomOut();
      }
    },
    [onZoomIn, onZoomOut, viewportActive],
  );

  return (
    <div
      className="nodrag nopan nowheel absolute right-3 top-3 z-20 flex h-10 items-center gap-1 rounded-lg border border-slate-200 bg-white/95 px-1.5 shadow-md backdrop-blur"
      onPointerDown={(event) => event.stopPropagation()}
      onMouseDown={(event) => event.stopPropagation()}
      onWheel={(event) => event.stopPropagation()}
    >
      <button
        type="button"
        onPointerUp={(event) => {
          event.preventDefault();
          event.stopPropagation();
          triggerZoom('out', 'pointer');
        }}
        onClick={(event) => {
          event.stopPropagation();
          triggerZoom('out', 'click');
        }}
        disabled={!viewportActive}
        className="nodrag nopan inline-flex h-8 w-8 items-center justify-center rounded-md text-lg font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-45"
        aria-label="Graph zoom out"
      >
        −
      </button>
      <span className="min-w-[54px] text-center text-sm font-semibold tabular-nums text-slate-700">
        {zoomPercent}%
      </span>
      <button
        type="button"
        onPointerUp={(event) => {
          event.preventDefault();
          event.stopPropagation();
          triggerZoom('in', 'pointer');
        }}
        onClick={(event) => {
          event.stopPropagation();
          triggerZoom('in', 'click');
        }}
        disabled={!viewportActive}
        className="nodrag nopan inline-flex h-8 w-8 items-center justify-center rounded-md text-lg font-semibold text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-45"
        aria-label="Graph zoom in"
      >
        +
      </button>
    </div>
  );
};

const nodeTypes = {
  decision: DecisionFlowNode,
};

const defaultEdgeOptions = {
  type: 'smoothstep',
  style: {
    stroke: GRAPH_EDGE_VISUAL.stroke,
    strokeWidth: GRAPH_EDGE_VISUAL.strokeWidth,
    strokeDasharray: GRAPH_EDGE_VISUAL.strokeDasharray,
  },
  markerEnd: {
    type: MarkerType.ArrowClosed,
    color: GRAPH_EDGE_VISUAL.stroke,
  },
};

const DecisionTreeFlow = forwardRef<DecisionTreeHandle, DecisionTreeFlowProps>(function DecisionTreeFlow(
  {
    nodes,
    edges,
    projectId = null,
    canEdit = false,
    canCreate = false,
    selectedNodeId = null,
    focusNodeId = null,
    linkingEnabled = false,
    linkingDisabled = false,
    onSelectNode,
    onCreateDecision,
    onCreateChildDecision,
    followUpError,
    onEditDecision,
    onDeleteDecision,
    onCreateLink,
    onRemoveLink,
    onZoomPercentChange,
    layoutResetKey = 0,
    viewportActive = true,
  },
  ref,
) {
  const reactFlowRef = useRef<ReactFlowInstance | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const layoutRequestRef = useRef(0);
  const skipViewportSaveRef = useRef(true);
  const persistViewportTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingViewportRef = useRef<Viewport | null>(null);
  const [selectedFlowNodeId, setSelectedFlowNodeId] = useState<string | null>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [localZoomPercent, setLocalZoomPercent] = useState(100);
  const [rememberViewport, setRememberViewport] = useState(() => {
    if (typeof window === 'undefined') return false;
    return loadRememberGraphViewport();
  });
  const userPositionedNodesRef = useRef<Set<string>>(new Set());
  const userHasArrangedRef = useRef(false);
  const layoutReadyRef = useRef(false);
  const rememberViewportRef = useRef(false);
  const savedViewportRestoredRef = useRef(false);
  /** Auto-fit runs once per page load; not on drag/resize. Reset on project or layout reset. */
  const initialViewportAppliedRef = useRef(false);
  const applyTieredViewportRef = useRef<
    (opts?: { duration?: number; nodeIds?: string[]; persistAfter?: boolean }) => void
  >(() => {});
  rememberViewportRef.current = rememberViewport;
  const flowNodesRef = useRef<Node<FlowNodeData>[]>([]);
  const flowEdgesRef = useRef<Edge[]>([]);
  /** ELK positions without user pins — used only for viewport fit, not rendering. */
  const layoutNodesForViewportRef = useRef<Node<FlowNodeData>[]>([]);
  const draftNodesRef = useRef<Node<FlowNodeData>[]>([]);
  const draftEdgesRef = useRef<Edge[]>([]);
  const persistLayoutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [layoutPositionsEpoch, setLayoutPositionsEpoch] = useState(0);
  const tidyLayoutInFlightRef = useRef(false);

  const savedPositions = useMemo(() => {
    void layoutPositionsEpoch;
    return loadDecisionGraphPositions(projectId);
  }, [layoutPositionsEpoch, projectId]);

  const initialViewport = useMemo(() => readInitialViewport(projectId), [projectId]);

  useEffect(() => {
    layoutReadyRef.current = false;
    initialViewportAppliedRef.current = false;
    layoutNodesForViewportRef.current = [];
    savedViewportRestoredRef.current = Boolean(
      rememberViewport &&
        projectId &&
        isValidSavedViewport(loadDecisionGraphViewport(projectId)),
    );
  }, [projectId, layoutResetKey, rememberViewport]);

  const refreshViewportLayoutNodes = useCallback(() => {
    const draftNodes = draftNodesRef.current;
    const draftEdges = draftEdgesRef.current;
    if (draftNodes.length === 0) return;
    void layoutDecisionGraphNodesAsync(draftNodes, draftEdges, LAYOUT_OPTIONS, null).then(
      (layouted) => {
        layoutNodesForViewportRef.current = layouted;
      },
    );
  }, []);

  const persistViewportNow = useCallback(
    (viewport: Viewport) => {
      if (!viewportActive || !rememberViewportRef.current || !projectId || skipViewportSaveRef.current) {
        return;
      }
      saveDecisionGraphViewport(projectId, {
        x: viewport.x,
        y: viewport.y,
        zoom: viewport.zoom,
      });
    },
    [projectId, viewportActive],
  );

  const schedulePersistViewport = useCallback(
    (viewport: Viewport) => {
      if (!viewportActive || !rememberViewportRef.current || !projectId || skipViewportSaveRef.current) return;
      pendingViewportRef.current = viewport;
      if (persistViewportTimerRef.current) clearTimeout(persistViewportTimerRef.current);
      persistViewportTimerRef.current = setTimeout(() => {
        if (pendingViewportRef.current) {
          persistViewportNow(pendingViewportRef.current);
          pendingViewportRef.current = null;
        }
      }, 100);
    },
    [persistViewportNow, projectId, viewportActive],
  );

  const publishZoomPercent = useCallback(
    (zoom: number) => {
      const percent = Math.round(zoom * 100);
      setLocalZoomPercent(percent);
      onZoomPercentChange?.(percent);
    },
    [onZoomPercentChange],
  );

  const syncZoomPercent = useCallback(() => {
    const viewport = reactFlowRef.current?.getViewport();
    if (!viewport) return;
    publishZoomPercent(viewport.zoom);
  }, [publishZoomPercent]);

  useEffect(
    () => () => {
      if (persistViewportTimerRef.current) {
        clearTimeout(persistViewportTimerRef.current);
        persistViewportTimerRef.current = null;
      }
      if (!rememberViewportRef.current || !projectId) return;
      const pending = pendingViewportRef.current;
      if (pending && isValidSavedViewport(pending)) {
        saveDecisionGraphViewport(projectId, pending);
        pendingViewportRef.current = null;
      }
      const current = reactFlowRef.current?.getViewport();
      if (current && isValidSavedViewport(current)) {
        saveDecisionGraphViewport(projectId, current);
      }
    },
    [projectId],
  );

  useEffect(() => {
    if (!viewportActive || !rememberViewport) return;
    const onPageHide = () => {
      if (!projectId) return;
      const current = reactFlowRef.current?.getViewport();
      if (current && isValidSavedViewport(current)) {
        saveDecisionGraphViewport(projectId, current);
      }
    };
    window.addEventListener('pagehide', onPageHide);
    return () => window.removeEventListener('pagehide', onPageHide);
  }, [projectId, rememberViewport, viewportActive]);

  const releaseViewportSaveGuard = useCallback(() => {
    window.setTimeout(() => {
      skipViewportSaveRef.current = false;
    }, 400);
  }, []);

  const handleRememberViewportChange = useCallback(
    (checked: boolean) => {
      if (!viewportActive) return;
      setRememberViewport(checked);
      rememberViewportRef.current = checked;
      saveRememberGraphViewport(checked);
      if (checked && projectId) {
        skipViewportSaveRef.current = false;
        const current = reactFlowRef.current?.getViewport();
        if (current) {
          persistViewportNow(current);
          savedViewportRestoredRef.current = true;
        }
      } else {
        savedViewportRestoredRef.current = false;
        initialViewportAppliedRef.current = false;
        applyTieredViewportRef.current();
      }
    },
    [persistViewportNow, projectId, viewportActive],
  );

  useEffect(() => {
    userPositionedNodesRef.current.clear();
    userHasArrangedRef.current = false;
    if (!savedPositions) return;
    Object.keys(savedPositions).forEach((nodeId) => userPositionedNodesRef.current.add(nodeId));
    userHasArrangedRef.current = true;
  }, [projectId, savedPositions]);

  const schedulePersistLayout = useCallback(() => {
    if (!projectId || !userHasArrangedRef.current) return;
    if (persistLayoutTimerRef.current) clearTimeout(persistLayoutTimerRef.current);
    persistLayoutTimerRef.current = setTimeout(() => {
      saveDecisionGraphPositions(projectId, positionsFromNodes(flowNodesRef.current));
    }, 400);
  }, [projectId]);

  useEffect(
    () => () => {
      if (persistLayoutTimerRef.current) clearTimeout(persistLayoutTimerRef.current);
    },
    [],
  );

  const decisionById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  const { draftNodes, draftEdges, initialFlowNodes } = useMemo(() => {
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const draftNodes: Node<FlowNodeData>[] = sortDecisionNodes(nodes).map((node) => ({
      id: flowNodeIdForDecision(node.id),
      type: 'decision',
      position: { x: 0, y: 0 },
      width: DECISION_NODE_WIDTH,
      height: DECISION_NODE_HEIGHT,
      selected: selectedNodeId === node.id,
      data: {
        kind: 'decision',
        decisionId: node.id,
        label: titleForNode(node),
        meta: node.projectSeq ? `#${node.projectSeq}` : '',
        status: node.status,
        topicLabel: node.topicLabel ?? node.topic ?? null,
        riskLevel: node.riskLevel ?? null,
      },
      draggable: true,
      connectable: linkingEnabled && !linkingDisabled,
    }));

    const draftEdges: Edge[] = edges.flatMap((edge) => {
      if (!nodeById.has(edge.from) || !nodeById.has(edge.to)) return [];
      return [buildFlowEdge(edge, linkingEnabled, linkingDisabled)];
    });

    const pinnedPositions = savedPositions ?? null;
    const layoutedNodes = layoutDecisionGraphNodes(
      draftNodes,
      draftEdges,
      LAYOUT_OPTIONS,
      pinnedPositions,
    );

    return { draftNodes, draftEdges, initialFlowNodes: layoutedNodes };
  }, [edges, linkingDisabled, linkingEnabled, nodes, savedPositions, selectedNodeId]);

  draftNodesRef.current = draftNodes;
  draftEdgesRef.current = draftEdges;

  /** Re-run ELK only when nodes/reset/positions change — not on edge-only updates. */
  const layoutStructureKey = useMemo(
    () =>
      [
        draftNodes.map((node) => node.id).join(','),
        layoutResetKey,
        layoutPositionsEpoch,
        projectId ?? 'none',
      ].join('|'),
    [draftNodes, layoutPositionsEpoch, layoutResetKey, projectId],
  );

  const [flowNodes, setFlowNodes, applyNodeChanges] = useNodesState<FlowNodeData>(initialFlowNodes);
  const [flowEdges, setFlowEdges, applyEdgeChanges] = useEdgesState(draftEdges);
  flowNodesRef.current = flowNodes;
  const prevLayoutResetKeyRef = useRef(layoutResetKey);

  useEffect(() => {
    if (layoutResetKey === prevLayoutResetKeyRef.current) return;
    prevLayoutResetKeyRef.current = layoutResetKey;
    userPositionedNodesRef.current.clear();
    setFlowNodes(initialFlowNodes);
  }, [initialFlowNodes, layoutResetKey, setFlowNodes]);

  useEffect(() => {
    setFlowNodes((currentNodes) => {
      if (currentNodes.length === 0) {
        return initialFlowNodes;
      }

      const currentById = new Map(currentNodes.map((node) => [node.id, node]));
      const nextIds = new Set(initialFlowNodes.map((node) => node.id));
      userPositionedNodesRef.current.forEach((nodeId) => {
        if (!nextIds.has(nodeId)) userPositionedNodesRef.current.delete(nodeId);
      });

      return initialFlowNodes.map((node) => {
        const current = currentById.get(node.id);
        if (!current) return node;

        // Keep cards where the user left them; only brand-new nodes use dagre positions.
        return {
          ...node,
          position: current.position,
          positionAbsolute: current.positionAbsolute,
          dragging: current.dragging,
        };
      });
    });
  }, [initialFlowNodes, setFlowNodes]);

  useEffect(() => {
    setFlowEdges(draftEdges);
    if (userHasArrangedRef.current) schedulePersistLayout();
    refreshViewportLayoutNodes();
  }, [draftEdges, refreshViewportLayoutNodes, schedulePersistLayout, setFlowEdges]);

  const renderedNodes = useMemo(
    () =>
      flowNodes.map((node) => ({
        ...node,
        selected: node.id === selectedFlowNodeId || node.data.decisionId === selectedNodeId,
      })),
    [flowNodes, selectedFlowNodeId, selectedNodeId],
  );
  flowEdgesRef.current = flowEdges;

  const applyTieredViewport = useCallback(
    (opts?: { duration?: number; nodeIds?: string[]; persistAfter?: boolean }) => {
      if (!viewportActive) return;
      const instance = reactFlowRef.current;
      const currentNodes = flowNodesRef.current;
      const currentEdges = flowEdgesRef.current;
      if (!instance || currentNodes.length === 0) return;

      const { width, height } = readContainerSize(containerRef.current);
      if (width < 120 || height < 120) return;
      if (!layoutReadyRef.current && !selectedNodeId && !focusNodeId && !opts?.nodeIds?.length) {
        return;
      }

      skipViewportSaveRef.current = true;
      const duration = opts?.duration ?? 0;

      let viewport: Viewport | null = null;

      if (opts?.nodeIds?.length) {
        const bounds = boundsForNodes(
          currentNodes,
          opts.nodeIds,
          DECISION_NODE_WIDTH,
          DECISION_NODE_HEIGHT,
        );
        if (bounds) {
          viewport = viewportToFitBounds(bounds, width, height, {
            padding: 0.2,
            minZoom: PARTIAL_FIT_MIN_ZOOM,
            maxZoom: READABLE_MAX_ZOOM,
          });
        }
      } else {
        const layoutNodesForFit =
          layoutNodesForViewportRef.current.length > 0
            ? layoutNodesForViewportRef.current
            : layoutDecisionGraphNodes(currentNodes, currentEdges, LAYOUT_OPTIONS, null);
        viewport = resolveDecisionGraphViewport({
          nodes: currentNodes,
          edges: currentEdges,
          layoutNodesForFit,
          container: containerRef.current,
          projectId,
          rememberViewport: rememberViewportRef.current,
          selectedNodeId,
          focusNodeId,
          flowNodeIdForDecision,
        });
      }

      if (!viewport) {
        releaseViewportSaveGuard();
        return;
      }

      instance.setViewport(viewport, { duration });
      window.setTimeout(() => {
        if (opts?.persistAfter && rememberViewportRef.current && projectId) {
          persistViewportNow(instance.getViewport());
        }
        if (rememberViewportRef.current && !opts?.nodeIds && projectId) {
          const saved = loadDecisionGraphViewport(projectId);
          const applied = instance.getViewport();
          savedViewportRestoredRef.current = Boolean(
            isValidSavedViewport(saved) &&
              Math.abs(applied.x - saved!.x) < 0.5 &&
              Math.abs(applied.y - saved!.y) < 0.5 &&
              Math.abs(applied.zoom - saved!.zoom) < 0.001,
          );
        } else if (!opts?.nodeIds && !opts?.persistAfter) {
          initialViewportAppliedRef.current = true;
        }
        publishZoomPercent(instance.getViewport().zoom);
        releaseViewportSaveGuard();
      }, duration + 40);
    },
    [
      focusNodeId,
      persistViewportNow,
      publishZoomPercent,
      projectId,
      releaseViewportSaveGuard,
      selectedNodeId,
      viewportActive,
    ],
  );

  applyTieredViewportRef.current = applyTieredViewport;

  const maybeApplyInitialViewport = useCallback(() => {
    if (!viewportActive || !layoutReadyRef.current) return;

    if (rememberViewportRef.current) {
      if (savedViewportRestoredRef.current) return;
      applyTieredViewport();
      return;
    }

    if (initialViewportAppliedRef.current) return;
    applyTieredViewport();
  }, [applyTieredViewport, viewportActive]);

  const scheduleApplyInitialViewport = useCallback(() => {
    window.setTimeout(() => maybeApplyInitialViewport(), 160);
  }, [maybeApplyInitialViewport]);

  const fitView = useCallback(
    (nodeIds?: string[]) => {
      applyTieredViewport({ duration: 250, nodeIds, persistAfter: true });
    },
    [applyTieredViewport],
  );

  useEffect(() => {
    const requestId = layoutRequestRef.current + 1;
    layoutRequestRef.current = requestId;

    const pinned: SavedGraphPositions = { ...(savedPositions ?? {}) };
    userPositionedNodesRef.current.forEach((nodeId) => {
      const node = flowNodesRef.current.find((item) => item.id === nodeId);
      if (node) {
        pinned[node.id] = { x: node.position.x, y: node.position.y };
      }
    });

    void Promise.all([
      layoutDecisionGraphNodesAsync(draftNodesRef.current, draftEdgesRef.current, LAYOUT_OPTIONS, pinned),
      layoutDecisionGraphNodesAsync(draftNodesRef.current, draftEdgesRef.current, LAYOUT_OPTIONS, null),
    ]).then(([layouted, layoutForViewport]) => {
      if (layoutRequestRef.current !== requestId) return;
      layoutNodesForViewportRef.current = layoutForViewport;
      setFlowNodes((currentNodes) => {
        const currentById = new Map(currentNodes.map((node) => [node.id, node]));
        return layouted.map((node) => {
          const current = currentById.get(node.id);
          if (current && userPositionedNodesRef.current.has(node.id)) {
            return {
              ...node,
              position: current.position,
              positionAbsolute: current.positionAbsolute,
              dragging: current.dragging,
            };
          }
          return node;
        });
      });
      layoutReadyRef.current = true;
      if (!initialViewportAppliedRef.current && !savedViewportRestoredRef.current) {
        scheduleApplyInitialViewport();
      }
    }).catch(() => {
      layoutReadyRef.current = true;
      if (!initialViewportAppliedRef.current && !savedViewportRestoredRef.current) {
        scheduleApplyInitialViewport();
      }
    });
    // draftEdges/draftNodes refs omitted — only layoutStructureKey triggers relayout.
  }, [layoutStructureKey, savedPositions, scheduleApplyInitialViewport, setFlowNodes]);

  const selectedFlowNode = useMemo(
    () => renderedNodes.find((node) => node.id === selectedFlowNodeId) ?? null,
    [renderedNodes, selectedFlowNodeId],
  );

  const selectedDecision = useMemo(
    () =>
      selectedFlowNode
        ? decisionById.get(selectedFlowNode.data.decisionId) ?? null
        : null,
    [decisionById, selectedFlowNode],
  );

  useEffect(() => {
    if (!focusNodeId || !viewportActive || rememberViewportRef.current) return;
    fitView(neighborNodeIds(flowNodeIdForDecision(focusNodeId), flowEdgesRef.current, 1));
  }, [fitView, focusNodeId, viewportActive]);

  useEffect(() => {
    syncZoomPercent();
  }, [syncZoomPercent]);

  const handleGraphZoomIn = useCallback(() => {
    const instance = reactFlowRef.current;
    if (!instance || !viewportActive) return;
    const current = instance.getViewport();
    const nextZoom = Math.min(GRAPH_MAX_ZOOM, current.zoom + GRAPH_ZOOM_STEP);
    const bounds = containerRef.current?.getBoundingClientRect();
    const width = bounds?.width || 1;
    const height = bounds?.height || 1;
    const centerX = (width / 2 - current.x) / current.zoom;
    const centerY = (height / 2 - current.y) / current.zoom;
    const nextViewport = {
      x: width / 2 - centerX * nextZoom,
      y: height / 2 - centerY * nextZoom,
      zoom: nextZoom,
    };
    instance.setViewport(nextViewport, { duration: 120 });
    window.setTimeout(() => {
      const viewport = instance.getViewport();
      persistViewportNow(viewport);
      schedulePersistViewport(viewport);
      publishZoomPercent(viewport.zoom);
    }, 140);
  }, [persistViewportNow, publishZoomPercent, schedulePersistViewport, viewportActive]);

  const handleGraphZoomOut = useCallback(() => {
    const instance = reactFlowRef.current;
    if (!instance || !viewportActive) return;
    const current = instance.getViewport();
    const nextZoom = Math.max(GRAPH_MIN_ZOOM, current.zoom - GRAPH_ZOOM_STEP);
    const bounds = containerRef.current?.getBoundingClientRect();
    const width = bounds?.width || 1;
    const height = bounds?.height || 1;
    const centerX = (width / 2 - current.x) / current.zoom;
    const centerY = (height / 2 - current.y) / current.zoom;
    const nextViewport = {
      x: width / 2 - centerX * nextZoom,
      y: height / 2 - centerY * nextZoom,
      zoom: nextZoom,
    };
    instance.setViewport(nextViewport, { duration: 120 });
    window.setTimeout(() => {
      const viewport = instance.getViewport();
      persistViewportNow(viewport);
      schedulePersistViewport(viewport);
      publishZoomPercent(viewport.zoom);
    }, 140);
  }, [persistViewportNow, publishZoomPercent, schedulePersistViewport, viewportActive]);

  useEffect(() => {
    if (!viewportActive) return;
    const graphWindow = window as DecisionGraphWindow;
    graphWindow.__decisionGraphZoomIn = handleGraphZoomIn;
    graphWindow.__decisionGraphZoomOut = handleGraphZoomOut;
    window.addEventListener('decision-graph-zoom-in', handleGraphZoomIn);
    window.addEventListener('decision-graph-zoom-out', handleGraphZoomOut);
    return () => {
      if (graphWindow.__decisionGraphZoomIn === handleGraphZoomIn) {
        delete graphWindow.__decisionGraphZoomIn;
      }
      if (graphWindow.__decisionGraphZoomOut === handleGraphZoomOut) {
        delete graphWindow.__decisionGraphZoomOut;
      }
      window.removeEventListener('decision-graph-zoom-in', handleGraphZoomIn);
      window.removeEventListener('decision-graph-zoom-out', handleGraphZoomOut);
    };
  }, [handleGraphZoomIn, handleGraphZoomOut, viewportActive]);

  useImperativeHandle(
    ref,
    () => ({
      jumpToToday: () => fitView(),
      jumpToTopic: (topic: string) => {
        const topicNodeIds = flowNodes
          .filter((node) => decisionById.get(node.data.decisionId)?.topic === topic)
          .map((node) => node.id);
        fitView(topicNodeIds.length > 0 ? topicNodeIds : undefined);
      },
      resetView: () => fitView(),
      zoomIn: handleGraphZoomIn,
      zoomOut: handleGraphZoomOut,
    }),
    [decisionById, fitView, flowNodes, handleGraphZoomIn, handleGraphZoomOut],
  );

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node<FlowNodeData>) => {
      setSelectedFlowNodeId(node.id);
    },
    [],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      let shouldPersist = false;
      changes.forEach((change) => {
        if (change.type === 'position' && change.dragging === false) {
          userPositionedNodesRef.current.add(change.id);
          userHasArrangedRef.current = true;
          shouldPersist = true;
        }
      });
      applyNodeChanges(changes);
      if (shouldPersist) schedulePersistLayout();
    },
    [applyNodeChanges, schedulePersistLayout],
  );

  const handleNodeDoubleClick = useCallback(
    (_event: React.MouseEvent, node: Node<FlowNodeData>) => {
      onSelectNode?.(node.data.decisionId);
    },
    [onSelectNode],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const fromId = decisionIdFromFlowNode(connection.source);
      const toId = decisionIdFromFlowNode(connection.target);
      if (!fromId || !toId || fromId === toId) return;
      userHasArrangedRef.current = true;
      onCreateLink?.(fromId, toId);
    },
    [onCreateLink],
  );

  const handleEdgesDelete: OnEdgesDelete = useCallback(
    (deletedEdges) => {
      userHasArrangedRef.current = true;
      deletedEdges.forEach((edge) => {
        const from = (edge.data as { from?: number } | undefined)?.from;
        const to = (edge.data as { to?: number } | undefined)?.to;
        if (from && to) onRemoveLink?.(from, to);
      });
      schedulePersistLayout();
    },
    [onRemoveLink, schedulePersistLayout],
  );

  const handleEdgeClick = useCallback(
    (_event: React.MouseEvent, edge: Edge) => {
      if (!linkingEnabled || linkingDisabled) return;
      const from = (edge.data as { from?: number } | undefined)?.from;
      const to = (edge.data as { to?: number } | undefined)?.to;
      if (from && to) onRemoveLink?.(from, to);
    },
    [linkingDisabled, linkingEnabled, onRemoveLink],
  );

  const handleEditSelectedDecision = useCallback(() => {
    if (!selectedDecision) return;
    if (onEditDecision) {
      onEditDecision(selectedDecision);
      return;
    }
    onSelectNode?.(selectedDecision.id);
  }, [onEditDecision, onSelectNode, selectedDecision]);

  const handleDeleteSelectedDecision = useCallback(() => {
    if (!canEdit || !onDeleteDecision || !selectedDecision) return;
    onDeleteDecision(selectedDecision);
  }, [canEdit, onDeleteDecision, selectedDecision]);

  const handleTidyLayout = useCallback(() => {
    if (!projectId || tidyLayoutInFlightRef.current) return;
    tidyLayoutInFlightRef.current = true;

    clearDecisionGraphPositions(projectId);
    userPositionedNodesRef.current.clear();
    userHasArrangedRef.current = false;

    layoutRequestRef.current += 1;
    const requestId = layoutRequestRef.current;

    void layoutDecisionGraphNodesAsync(draftNodes, draftEdges, LAYOUT_OPTIONS, null)
      .then((layouted) => {
        if (layoutRequestRef.current !== requestId) return;
        layoutNodesForViewportRef.current = layouted;
        setFlowNodes(layouted);
        saveDecisionGraphPositions(projectId, positionsFromNodes(layouted));
        setLayoutPositionsEpoch((epoch) => epoch + 1);
        layoutReadyRef.current = true;
        if (!rememberViewportRef.current) {
          initialViewportAppliedRef.current = false;
          fitView();
        }
      })
      .catch(() => {
        layoutReadyRef.current = true;
      })
      .finally(() => {
        tidyLayoutInFlightRef.current = false;
      });
  }, [draftEdges, draftNodes, fitView, projectId, setFlowNodes]);

  return (
    <div ref={containerRef} className="relative h-full w-full overflow-hidden rounded-lg border border-slate-200 bg-[#f8fafc]">
      <div className="absolute left-3 top-3 z-20 flex flex-col items-start gap-1.5">
        <button
          type="button"
          onClick={() => setToolsOpen((open) => !open)}
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white/95 text-slate-600 shadow-md backdrop-blur transition hover:border-[#3CCED7] hover:bg-[#F7FEFF] hover:text-[#16828C]"
          title={toolsOpen ? 'Hide graph tools' : 'Graph tools'}
          aria-expanded={toolsOpen}
          aria-label="Graph tools"
        >
          <Workflow className="h-4 w-4" aria-hidden="true" />
        </button>

        {toolsOpen ? (
          <div className="w-[168px] overflow-hidden rounded-lg border border-slate-200 bg-white/95 shadow-lg backdrop-blur">
            <div className="flex flex-col gap-0.5 p-1.5">
              <button
                type="button"
                onClick={() => void onCreateDecision?.()}
                disabled={!canCreate || !onCreateDecision}
                className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-[11px] font-bold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-45"
                title="New decision"
              >
                <Circle className="h-3.5 w-3.5 shrink-0 text-[#A855F7]" aria-hidden="true" />
                New decision
              </button>
              <button
                type="button"
                onClick={() => selectedDecision && void onCreateChildDecision?.(selectedDecision)}
                disabled={!canCreate || !selectedDecision || !onCreateChildDecision}
                className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-[11px] font-bold text-[#16828C] transition hover:bg-[#E8FBFC] disabled:cursor-not-allowed disabled:opacity-45"
                title="Add follow-up"
              >
                <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                Add follow-up
              </button>
              {followUpError && followUpError.parentId === selectedDecision?.id && (
                <p role="alert" className="max-w-64 px-2 text-xs text-red-600">
                  {followUpError.message}
                </p>
              )}
              <div className="my-0.5 h-px bg-slate-100" />
              <button
                type="button"
                onClick={handleEditSelectedDecision}
                disabled={!canEdit || !selectedDecision}
                className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-[11px] font-bold text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-45"
                title="Edit selected decision"
              >
                <Pencil className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
                Edit
              </button>
              <button
                type="button"
                onClick={handleDeleteSelectedDecision}
                disabled={!canEdit || !selectedDecision}
                className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-[11px] font-bold text-slate-700 transition hover:bg-red-50 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-45"
                title="Delete selected decision"
              >
                <Trash2 className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
                Delete
              </button>
              <div className="my-0.5 h-px bg-slate-100" />
              <button
                type="button"
                onClick={handleTidyLayout}
                className="flex h-8 items-center gap-2 rounded-md px-2 text-left text-[11px] font-bold text-slate-700 transition hover:bg-slate-100"
                title="Re-run auto layout — shortens long edges from manual drags"
              >
                <LayoutGrid className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden="true" />
                Tidy layout
              </button>
              <button
                type="button"
                onClick={() => handleRememberViewportChange(!rememberViewport)}
                disabled={!viewportActive}
                className={`flex h-8 items-center gap-2 rounded-md px-2 text-left text-[11px] font-bold transition disabled:cursor-not-allowed disabled:opacity-45 ${
                  rememberViewport
                    ? 'bg-[#E8FBFC] text-[#16828C] hover:bg-[#dff8fa]'
                    : 'text-slate-700 hover:bg-slate-100'
                }`}
                title={
                  rememberViewport
                    ? 'Pinned — pan and zoom are saved for this project'
                    : 'Pin view — save pan and zoom for next visit'
                }
                aria-pressed={rememberViewport}
                aria-label="Pin view"
              >
                <Pin
                  className={`h-3.5 w-3.5 shrink-0 ${rememberViewport ? 'fill-current text-[#16828C]' : 'text-slate-500'}`}
                  aria-hidden="true"
                />
                Pin view
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <ReactFlow
        nodes={renderedNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        defaultEdgeOptions={defaultEdgeOptions}
        defaultViewport={initialViewport}
        minZoom={GRAPH_MIN_ZOOM}
        maxZoom={GRAPH_MAX_ZOOM}
        fitViewOptions={{ padding: 0.12, minZoom: GRAPH_MIN_ZOOM, maxZoom: READABLE_MAX_ZOOM }}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        zoomOnDoubleClick={false}
        nodesDraggable
        nodesConnectable={linkingEnabled && !linkingDisabled}
        edgesFocusable={linkingEnabled && !linkingDisabled}
        edgesUpdatable={false}
        connectionLineType={ConnectionLineType.SmoothStep}
        connectionLineStyle={{ stroke: GRAPH_EDGE_VISUAL.stroke, strokeWidth: 2.2, strokeDasharray: '10 8' }}
        onInit={(instance) => {
          reactFlowRef.current = instance;
          publishZoomPercent(instance.getViewport().zoom);
          window.setTimeout(() => publishZoomPercent(instance.getViewport().zoom), 250);
          if (!rememberViewportRef.current || !projectId || savedViewportRestoredRef.current) return;
          const saved = loadDecisionGraphViewport(projectId);
          if (!isValidSavedViewport(saved)) return;
          skipViewportSaveRef.current = true;
          instance.setViewport(saved, { duration: 0 });
          window.setTimeout(() => {
            savedViewportRestoredRef.current = true;
            publishZoomPercent(instance.getZoom());
            releaseViewportSaveGuard();
          }, 40);
        }}
        onMoveEnd={(_event, viewport) => {
          publishZoomPercent(viewport.zoom);
          if (rememberViewportRef.current) {
            persistViewportNow(viewport);
          }
        }}
        onNodesChange={handleNodesChange}
        onEdgesChange={applyEdgeChanges}
        onNodeClick={handleNodeClick}
        onNodeDoubleClick={handleNodeDoubleClick}
        onConnect={handleConnect}
        onEdgesDelete={handleEdgesDelete}
        onEdgeClick={handleEdgeClick}
        proOptions={{ hideAttribution: true }}
      >
        <GraphZoomControls
          viewportActive={viewportActive}
          zoomPercent={localZoomPercent}
          onZoomIn={handleGraphZoomIn}
          onZoomOut={handleGraphZoomOut}
        />
        <Background variant={BackgroundVariant.Lines} gap={32} size={1} color="#e2e8f0" />
        <DecisionGraphMiniMap />
        <Controls showInteractive={false} onFitView={() => fitView()} />
      </ReactFlow>
    </div>
  );
});

export default DecisionTreeFlow;
