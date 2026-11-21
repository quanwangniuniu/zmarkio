'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import Link from 'next/link';
import { nestedProjectPath } from '@/lib/projectNestedRoutes';
import { useBuildUrl } from '@/lib/buildUrl';
import dagre from '@dagrejs/dagre';
import { CheckCircle2, FileText, Link2, PencilLine, Plus, Trash2, X } from 'lucide-react';
import DecisionStatusPill from '@/components/decisions/DecisionStatusPill';
import { DECISION_RISK } from '@/lib/decisions/decisionConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle, optionLabel } from '@/lib/orgCustomization/optionConfig';
import {
  bucketDateKey,
  bucketLabel,
  bucketTooltip,
  columnForDayKey,
  expandBucketRange,
  formatDecisionDayKey,
  buildDecisionEdgePath,
  getDecisionEdgeEndpoints,
  pickTimelineGranularity,
  sortBucketKeys,
  sortNodesStable,
  type TimelineGranularity,
} from '@/components/decisions/decisionTreeLayout';
import type { DecisionGraphEdge, DecisionGraphNode, DecisionGraphTopic, DecisionStatus } from '@/types/decision';

export interface DecisionTreeHandle {
  jumpToToday: () => void;
  jumpToTopic: (topic: string) => void;
  resetView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

interface DecisionTreeProps {
  nodes: DecisionGraphNode[];
  edges: DecisionGraphEdge[];
  topics?: DecisionGraphTopic[];
  projectId?: number | string | null;
  mode?: 'viewer' | 'selector' | 'link-editor';
  onAddDecision?: (decision: DecisionGraphNode) => void;
  selectedSeqs?: Set<number> | number[];
  focusSeq?: number | null;
  /** Scroll viewport to this decision id (e.g. title search). */
  focusNodeId?: number | null;
  onEditDecision?: (decision: DecisionGraphNode) => void;
  onCreateDecision?: () => void;
  autoFocusToday?: boolean;
  focusDateKey?: string | null;
  timelineGranularity?: TimelineGranularity | null;
  canReview?: boolean;
  removedSeqs?: Set<number> | number[];
  onToggleLink?: (decision: DecisionGraphNode) => void;
  onEditLinks?: (decision: DecisionGraphNode) => void;
  onDelete?: (decision: DecisionGraphNode) => void;
  canDelete?: boolean;
  selectedNodeId?: number | null;
  onSelectNode?: (id: number) => void;
  /** When true, show link handles and clickable edges; drag to connect, click edge to unlink */
  linkingEnabled?: boolean;
  /** When true, disable link handles and edge unlink (e.g. while saving) */
  linkingDisabled?: boolean;
  onCreateLink?: (fromId: number, toId: number) => void;
  onRemoveLink?: (fromId: number, toId: number) => void;
  /** Override URL builder for detail popover link. Defaults to /decisions/{slug}. */
  getDecisionUrl?: (idOrSlug: number | string, projectId?: number | string | null) => string;
  /** Override URL builder for review popover link. Defaults to /decisions/{slug}/review. */
  getReviewUrl?: (idOrSlug: number | string, projectId?: number | string | null) => string;
  /** Notifies parent when zoom % changes (for toolbar display). */
  onZoomPercentChange?: (percent: number) => void;
  /** Controls non-timeline map layout. Topics keeps topic columns; Tree uses links as parent-child structure. */
  viewMode?: DecisionTreeViewMode;
  /** Move a decision into another topic column in Topics view. */
  onMoveDecisionToTopic?: (decisionId: number, projectId: number | string, topic: string) => void | Promise<void>;
  /** Rename a topic column title in Topics view. */
  onRenameTopic?: (topic: string, title: string) => void | Promise<void>;
  /** Create an empty topic column in Topics view. */
  onCreateTopic?: (title: string) => void | Promise<void>;
  /** Delete an empty topic column in Topics view. */
  onDeleteTopic?: (topic: string) => void | Promise<void>;
}

const defaultGetDecisionUrl = (idOrSlug: number | string, projectId?: number | string | null) =>
  projectId
    ? nestedProjectPath(projectId, `/decisions/${idOrSlug}`)
    : `/decisions/${idOrSlug}`;

const defaultGetReviewUrl = (idOrSlug: number | string, projectId?: number | string | null) =>
  projectId
    ? nestedProjectPath(projectId, `/decisions/${idOrSlug}/review`)
    : `/decisions/${idOrSlug}/review`;

type PositionedNode = DecisionGraphNode & { x: number; y: number; dateKey: string };
type PositionedTreeGroup = {
  key: string;
  title: string;
  count: number;
  x: number;
  y: number;
  expanded: boolean;
  childIds: number[];
};
type DateColumn = {
  dateKey: string;
  x: number;
  count: number;
  granularity: TimelineGranularity;
  layoutKind?: 'time' | 'cluster' | 'tree';
  topic?: string | null;
  primaryLabel?: string;
  secondaryLabel?: string;
};
export type DecisionTreeViewMode = 'topics' | 'tree';

const NODE_WIDTH = 300;
const NODE_HEIGHT = 96;
const TREE_GROUP_WIDTH = 260;
const TREE_GROUP_HEIGHT = 82;
const COLUMN_GAP = 120;
const COLUMN_PADDING_X = 24;
const ROW_GAP = 32;
const PADDING = 56;
const EXTRA_SCROLL = 240;
const HEADER_BAND_HEIGHT = 48;
const BAND_TOP_PADDING = 20;
const NODE_Y_BASE = BAND_TOP_PADDING + HEADER_BAND_HEIGHT + 14;
const BASE_ZOOM = 0.7;
const ZOOM_MIN = BASE_ZOOM * 0.5;
const ZOOM_MAX = BASE_ZOOM * 2.0;
const DEFAULT_ZOOM = BASE_ZOOM;
const ZOOM_STEP = BASE_ZOOM * 0.1;
const EDGE_END_GAP = 6;
const EDGE_STROKE_NORMAL = '#b8c3d7';
const EDGE_STROKE_HOVER = '#3CCED7';
const LINK_DROP_PAD = 16;
const LINK_DRAG_THRESHOLD = 4;
const LINK_AUTO_SCROLL_MARGIN = 72;
const LINK_AUTO_SCROLL_SPEED = 20;

const formatDateKey = formatDecisionDayKey;

const formatLocalDateKey = (date: Date) => {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const formatDateLabel = (dateKey: string) => {
  if (dateKey === 'Unknown') return 'Unknown';
  const date = new Date(`${dateKey}T00:00:00`);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
  }).format(date);
};

const formatDateLabelFull = (dateKey: string) => {
  if (dateKey === 'Unknown') return 'Unknown';
  const date = new Date(`${dateKey}T00:00:00`);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(date);
};

const formatDateLabelMinimal = (dateKey: string) => {
  if (dateKey === 'Unknown') return 'Unknown';
  const date = new Date(`${dateKey}T00:00:00`);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  return new Intl.DateTimeFormat('en-US', {
    day: 'numeric',
  }).format(date);
};

const formatColumnLabel = (column: DateColumn, labelMode: 'full' | 'short' | 'minimal') => {
  if (column.layoutKind === 'cluster' || column.layoutKind === 'tree') {
    return {
      primary: column.primaryLabel || column.dateKey,
      secondary: column.secondaryLabel || '',
    };
  }

  if (column.dateKey === 'Unknown') {
    return { primary: 'Unknown', secondary: '' };
  }

  if (column.granularity === 'day') {
    const date = new Date(`${column.dateKey}T00:00:00`);
    if (Number.isNaN(date.getTime())) {
      return { primary: column.dateKey, secondary: '' };
    }
    const weekday = new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(date);
    if (labelMode === 'minimal') {
      return { primary: formatDateLabelMinimal(column.dateKey), secondary: weekday };
    }
    return {
      primary: weekday,
      secondary: labelMode === 'full' ? formatDateLabelFull(column.dateKey) : formatDateLabel(column.dateKey),
    };
  }

  if (column.granularity === 'week') {
    const label = bucketLabel(column.dateKey, column.granularity);
    return { primary: 'Week', secondary: label };
  }

  const [year, month] = column.dateKey.split('-').map(Number);
  const date = new Date(year, (month ?? 1) - 1, 1);
  if (Number.isNaN(date.getTime())) {
    return { primary: bucketLabel(column.dateKey, column.granularity), secondary: '' };
  }
  return {
    primary: new Intl.DateTimeFormat('en-US', { month: 'short' }).format(date),
    secondary: String(year),
  };
};

const granularityLabel = (granularity: TimelineGranularity) =>
  granularity === 'day' ? 'Day view' : granularity === 'week' ? 'Week view' : 'Month view';

const topicGroupKey = (node: DecisionGraphNode) => node.topic || 'other';
const topicGroupLabel = (node: DecisionGraphNode) => node.topicLabel || 'Other';

const formatDateTooltip = (dateKey: string) => {
  if (dateKey === 'Unknown') return 'Unknown';
  const date = new Date(`${dateKey}T00:00:00`);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  const weekday = new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
  }).format(date);
  return `${dateKey} (${weekday})`;
};

const getWeekdayStyle = (dateKey: string) => {
  if (dateKey === 'Unknown') {
    return {
      backgroundColor: '#f1f5f9',
      borderColor: '#e2e8f0',
      color: '#0f172a',
      tickColor: '#cbd5f5',
    };
  }
  const date = new Date(`${dateKey}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return {
      backgroundColor: '#f1f5f9',
      borderColor: '#e2e8f0',
      color: '#0f172a',
      tickColor: '#cbd5f5',
    };
  }
  const day = date.getDay();
  switch (day) {
    case 1: // Mon
      return {
        backgroundColor: '#e0f2fe',
        borderColor: '#bae6fd',
        color: '#0c4a6e',
        tickColor: '#7dd3fc',
      };
    case 2: // Tue
      return {
        backgroundColor: '#ecfccb',
        borderColor: '#d9f99d',
        color: '#365314',
        tickColor: '#a3e635',
      };
    case 3: // Wed
      return {
        backgroundColor: '#fef3c7',
        borderColor: '#fde68a',
        color: '#92400e',
        tickColor: '#fbbf24',
      };
    case 4: // Thu
      return {
        backgroundColor: '#ede9fe',
        borderColor: '#ddd6fe',
        color: '#4c1d95',
        tickColor: '#c4b5fd',
      };
    case 5: // Fri
      return {
        backgroundColor: '#ffe4e6',
        borderColor: '#fecdd3',
        color: '#9f1239',
        tickColor: '#fda4af',
      };
    case 6: // Sat
      return {
        backgroundColor: '#dcfce7',
        borderColor: '#bbf7d0',
        color: '#166534',
        tickColor: '#86efac',
      };
    case 0: // Sun
    default:
      return {
        backgroundColor: '#e2e8f0',
        borderColor: '#cbd5f5',
        color: '#1e293b',
        tickColor: '#94a3b8',
      };
  }
};

const clamp = (min: number, value: number, max: number) =>
  Math.min(max, Math.max(min, value));

const statusColor = (status: string) => {
  switch (status) {
    case 'DRAFT':
      return 'bg-gray-100 text-gray-700';
    case 'AWAITING_APPROVAL':
      return 'bg-amber-50 text-amber-700';
    case 'COMMITTED':
      return 'bg-emerald-50 text-emerald-700';
    case 'REVIEWED':
      return 'bg-violet-50 text-violet-700';
    case 'ARCHIVED':
      return 'bg-gray-100 text-gray-500';
    default:
      return 'bg-gray-100 text-gray-700';
  }
};

const riskLeftBorder = (risk?: string | null) => {
  switch (risk) {
    case 'HIGH':
      return 'border-l-[3px] border-l-rose-400';
    case 'MEDIUM':
      return 'border-l-[3px] border-l-amber-400';
    case 'LOW':
      return 'border-l-[3px] border-l-sky-400';
    default:
      return 'border-l-[3px] border-l-gray-200';
  }
};

const riskPillStyle = (risk?: string | null) => {
  switch (risk) {
    case 'HIGH':
      return 'bg-rose-50 text-rose-700';
    case 'MEDIUM':
      return 'bg-amber-50 text-amber-700';
    case 'LOW':
      return 'bg-sky-50 text-sky-700';
    default:
      return 'bg-gray-100 text-gray-500';
  }
};

const statusCardOpacity = (status: string) => {
  switch (status) {
    case 'ARCHIVED':
      return 'opacity-60';
    default:
      return '';
  }
};

const DecisionTree = forwardRef<DecisionTreeHandle, DecisionTreeProps>(function DecisionTree(
  {
    nodes,
    edges,
    topics,
    projectId,
    mode = 'viewer',
    onAddDecision,
    onEditDecision,
    selectedSeqs,
    focusSeq,
    focusNodeId,
    onCreateDecision,
    autoFocusToday = false,
    focusDateKey,
    timelineGranularity: timelineGranularityOverride,
    canReview = false,
    removedSeqs,
    onToggleLink,
    onEditLinks,
    onDelete,
    canDelete = false,
    selectedNodeId,
    onSelectNode,
    linkingEnabled = false,
    linkingDisabled = false,
    onCreateLink,
    onRemoveLink,
    getDecisionUrl,
    getReviewUrl,
    onZoomPercentChange,
    viewMode = 'topics',
    onMoveDecisionToTopic,
    onRenameTopic,
    onCreateTopic,
    onDeleteTopic,
  },
  ref,
) {
  useOptionConfigVersion();
  const buildUrl = useBuildUrl();
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const dragState = useRef({
    dragging: false,
    moved: false,
    startX: 0,
    startY: 0,
    scrollLeft: 0,
    scrollTop: 0,
  });
  const [scale, setScale] = useState(DEFAULT_ZOOM);
  const [popover, setPopover] = useState<{
    node: DecisionGraphNode;
    x: number;
    y: number;
  } | null>(null);
  const [headerTooltip, setHeaderTooltip] = useState<{
    dateKey: string;
    x: number;
    y: number;
    count: number;
  } | null>(null);
  const [linkDragFrom, setLinkDragFrom] = useState<{
    nodeId: number;
    node: PositionedNode;
  } | null>(null);
  const [linkDragPointer, setLinkDragPointer] = useState<{ x: number; y: number } | null>(null);
  const [linkSourceId, setLinkSourceId] = useState<number | null>(null);
  const [linkHoverTargetId, setLinkHoverTargetId] = useState<number | null>(null);
  const [hoveredEdgeIdx, setHoveredEdgeIdx] = useState<number | null>(null);
  const [moveDragNodeId, setMoveDragNodeId] = useState<number | null>(null);
  const [moveHoverTopic, setMoveHoverTopic] = useState<string | null>(null);
  const [editingTopic, setEditingTopic] = useState<{ topic: string; value: string } | null>(null);
  const [newTopicTitle, setNewTopicTitle] = useState<string | null>(null);
  const [newTopicError, setNewTopicError] = useState<string | null>(null);
  const [expandedTreeGroups, setExpandedTreeGroups] = useState<Set<string>>(new Set());
  const [topicGuideDismissed, setTopicGuideDismissed] = useState(false);
  const [topicGuidePage, setTopicGuidePage] = useState<0 | 1 | 2>(0);
  const linkPointerStartRef = useRef<{ x: number; y: number } | null>(null);
  const linkSuppressClickRef = useRef(false);
  const movePointerStartRef = useRef<{ x: number; y: number } | null>(null);
  const movePointerFromRef = useRef<{ decisionId: number; projectId: number | string; topic: string } | null>(null);
  const moveSuppressClickRef = useRef(false);
  const newTopicInputRef = useRef<HTMLInputElement | null>(null);
  const viewportSnapshotRef = useRef<{ left: number; top: number } | null>(null);
  const userPannedViewportRef = useRef(false);

  const selectedSeqSet = useMemo(() => {
    if (!selectedSeqs) return new Set<number>();
    return selectedSeqs instanceof Set ? selectedSeqs : new Set(selectedSeqs);
  }, [selectedSeqs]);

  const removedSeqSet = useMemo(() => {
    if (!removedSeqs) return new Set<number>();
    return removedSeqs instanceof Set ? removedSeqs : new Set(removedSeqs);
  }, [removedSeqs]);

  const todayKey = useMemo(() => formatLocalDateKey(new Date()), []);
  const isTreeLayout = viewMode === 'tree' && !timelineGranularityOverride;
  const isTopicLayout = viewMode === 'topics' && !timelineGranularityOverride;

  const { positionedNodes, positionedTreeGroups, dateColumns, timelineGranularity } = useMemo(() => {
    const dayKeys = nodes.map((node) => formatDateKey(node.createdAt));
    const granularity = timelineGranularityOverride ?? pickTimelineGranularity([...new Set(dayKeys)]);

    if (isTreeLayout) {
      const groupLabels = new Map<string, string>();
      topics?.forEach((topic) => {
        if (!topic.topic) return;
        groupLabels.set(topic.topic, topic.title || topic.defaultTitle || topic.topic);
      });
      const byGroup = new Map<string, DecisionGraphNode[]>();
      nodes.forEach((node) => {
        const key = topicGroupKey(node);
        if (!byGroup.has(key)) byGroup.set(key, []);
        byGroup.get(key)?.push(node);
        if (!groupLabels.has(key)) {
          groupLabels.set(key, topicGroupLabel(node));
        }
      });

      const nodeById = new Map(nodes.map((node) => [node.id, node]));
      const positioned: PositionedNode[] = [];
      const positionedGroups: PositionedTreeGroup[] = [];
      const depthCounts = new Map<number, number>();
      let maxDepth = 0;
      const dagreGraph = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
      dagreGraph.setGraph({
        rankdir: 'LR',
        nodesep: ROW_GAP + 26,
        ranksep: COLUMN_GAP + 64,
        marginx: PADDING,
        marginy: NODE_Y_BASE,
      });
      const nodeGroupKey = new Map<number, string>();
      const groupNodeIdsByKey = new Map<string, Set<number>>();
      byGroup.forEach((groupNodes, groupKey) => {
        const ids = new Set(groupNodes.map((node) => node.id));
        groupNodeIdsByKey.set(groupKey, ids);
        groupNodes.forEach((node) => nodeGroupKey.set(node.id, groupKey));
      });

      Array.from(byGroup.keys())
        .sort((a, b) => (groupLabels.get(a) || a).localeCompare(groupLabels.get(b) || b))
        .forEach((groupKey) => {
          const groupNodes = sortNodesStable(byGroup.get(groupKey) || []);
          const expanded = expandedTreeGroups.has(groupKey);
          dagreGraph.setNode(`group:${groupKey}`, {
            width: TREE_GROUP_WIDTH,
            height: TREE_GROUP_HEIGHT,
          });

          if (expanded && groupNodes.length > 0) {
            groupNodes.forEach((node) => {
              dagreGraph.setNode(`decision:${node.id}`, {
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
              });
            });
          }
        });

      const expandedNodeIds = new Set<number>();
      byGroup.forEach((groupNodes, groupKey) => {
        if (!expandedTreeGroups.has(groupKey)) return;
        groupNodes.forEach((node) => expandedNodeIds.add(node.id));
      });

      const incomingByGroup = new Map<string, Map<number, number>>();
      byGroup.forEach((groupNodes, groupKey) => {
        const incoming = new Map<number, number>();
        groupNodes.forEach((node) => incoming.set(node.id, 0));
        incomingByGroup.set(groupKey, incoming);
      });
      edges.forEach((edge) => {
        const fromGroup = nodeGroupKey.get(edge.from);
        const toGroup = nodeGroupKey.get(edge.to);
        if (!fromGroup || fromGroup !== toGroup || edge.from === edge.to) return;
        if (!expandedNodeIds.has(edge.from) || !expandedNodeIds.has(edge.to)) return;
        dagreGraph.setEdge(`decision:${edge.from}`, `decision:${edge.to}`);
        const incoming = incomingByGroup.get(toGroup);
        if (incoming) incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1);
      });

      byGroup.forEach((groupNodes, groupKey) => {
        if (!expandedTreeGroups.has(groupKey)) return;
        const incoming = incomingByGroup.get(groupKey) ?? new Map<number, number>();
        const roots = sortNodesStable(groupNodes).filter((node) => (incoming.get(node.id) ?? 0) === 0);
        const startNodes = roots.length > 0 ? roots : sortNodesStable(groupNodes);
        startNodes.forEach((node) => {
          dagreGraph.setEdge(`group:${groupKey}`, `decision:${node.id}`);
        });
      });

      dagre.layout(dagreGraph);

      Array.from(byGroup.keys())
        .sort((a, b) => (groupLabels.get(a) || a).localeCompare(groupLabels.get(b) || b))
        .forEach((groupKey) => {
          const groupNodes = sortNodesStable(byGroup.get(groupKey) || []);
          const expanded = expandedTreeGroups.has(groupKey);
          const groupLayout = dagreGraph.node(`group:${groupKey}`) as { x?: number; y?: number } | undefined;
          const childIds: number[] = [];

          if (expanded) {
            groupNodes.forEach((node) => {
              const layout = dagreGraph.node(`decision:${node.id}`) as { x?: number; y?: number } | undefined;
              if (!layout || layout.x == null || layout.y == null) return;
              const graphNode = nodeById.get(node.id);
              if (!graphNode) return;
              const depth = Math.max(1, Math.round((layout.x - (groupLayout?.x ?? layout.x)) / (NODE_WIDTH + COLUMN_GAP)));
              maxDepth = Math.max(maxDepth, depth);
              depthCounts.set(depth, (depthCounts.get(depth) ?? 0) + 1);
              positioned.push({
                ...graphNode,
                dateKey: `tree:${depth}`,
                x: PADDING + layout.x - NODE_WIDTH / 2,
                y: NODE_Y_BASE + layout.y - NODE_HEIGHT / 2,
              });
              childIds.push(node.id);
            });
          }

          positionedGroups.push({
            key: groupKey,
            title: groupLabels.get(groupKey) || groupKey,
            count: groupNodes.length,
            x: PADDING + (groupLayout?.x ?? TREE_GROUP_WIDTH / 2) - TREE_GROUP_WIDTH / 2,
            y: NODE_Y_BASE + (groupLayout?.y ?? TREE_GROUP_HEIGHT / 2) - TREE_GROUP_HEIGHT / 2,
            expanded,
            childIds,
          });
        });

      const columns: DateColumn[] = [];
      for (let depth = 0; depth <= maxDepth; depth += 1) {
        const count = depth === 0 ? positionedGroups.length : depthCounts.get(depth) ?? 0;
        columns.push({
          dateKey: `tree:${depth}`,
          x: PADDING + depth * (NODE_WIDTH + COLUMN_GAP),
          count,
          granularity,
          layoutKind: 'tree',
          primaryLabel: depth === 0 ? 'Categories' : `Step ${depth}`,
          secondaryLabel:
            depth === 0
              ? `${count} group${count === 1 ? '' : 's'}`
              : `${count} decision${count === 1 ? '' : 's'}`,
        });
      }

      return {
        positionedNodes: positioned,
        positionedTreeGroups: positionedGroups,
        dateColumns: columns,
        timelineGranularity: granularity,
        layoutLabel: 'Tree',
      };
    }

    if (isTopicLayout) {
      const byTopic = new Map<string, DecisionGraphNode[]>();
      const topicLabels = new Map<string, string>();
      topics?.forEach((topic) => {
        if (!topic.topic) return;
        if (!byTopic.has(topic.topic)) byTopic.set(topic.topic, []);
        topicLabels.set(topic.topic, topic.title || topic.defaultTitle || topic.topic);
      });
      nodes.forEach((node) => {
        const key = topicGroupKey(node);
        if (!byTopic.has(key)) byTopic.set(key, []);
        byTopic.get(key)?.push(node);
        if (!topicLabels.has(key)) topicLabels.set(key, topicGroupLabel(node));
      });

      let all: PositionedNode[] = [];
      const columns: DateColumn[] = [];
      Array.from(byTopic.keys())
        .sort((a, b) => (topicLabels.get(a) || a).localeCompare(topicLabels.get(b) || b))
        .forEach((topicKey, index) => {
          const columnNodes = [...(byTopic.get(topicKey) || [])].sort((a, b) => {
            const ta = new Date(a.createdAt).getTime() || 0;
            const tb = new Date(b.createdAt).getTime() || 0;
            if (ta !== tb) return ta - tb;
            return (a.projectSeq ?? a.id) - (b.projectSeq ?? b.id);
          });
          const dateKey = `topic:${topicKey}`;
          columns.push({
            dateKey,
            x: PADDING + index * (NODE_WIDTH + COLUMN_GAP),
            count: columnNodes.length,
            granularity,
            layoutKind: 'cluster',
            topic: topicKey,
            primaryLabel: topicLabels.get(topicKey) || 'Other',
            secondaryLabel: `${columnNodes.length} decision${columnNodes.length === 1 ? '' : 's'}`,
          });
          columnNodes.forEach((node, rowIndex) => {
            all.push({
              ...node,
              dateKey,
              x: PADDING + index * (NODE_WIDTH + COLUMN_GAP),
              y: NODE_Y_BASE + rowIndex * (NODE_HEIGHT + ROW_GAP),
            });
          });
        });

      return {
        positionedNodes: all,
        positionedTreeGroups: [],
        dateColumns: columns,
        timelineGranularity: granularity,
        layoutLabel: 'Topics',
      };
    }

    const byBucket = new Map<string, DecisionGraphNode[]>();
    nodes.forEach((node) => {
      const dayKey = formatDateKey(node.createdAt);
      const bucketKey = bucketDateKey(dayKey, granularity);
      if (!byBucket.has(bucketKey)) byBucket.set(bucketKey, []);
      byBucket.get(bucketKey)?.push(node);
    });

    const bucketKeys = sortBucketKeys(Array.from(byBucket.keys()), granularity);
    const rangeBucketKeys = timelineGranularityOverride
      ? sortBucketKeys(
          Array.from(new Set([...bucketKeys, bucketDateKey(todayKey, granularity)])),
          granularity,
        )
      : bucketKeys;
    const sortedBuckets = timelineGranularityOverride
      ? expandBucketRange(rangeBucketKeys, granularity)
      : bucketKeys;

    let all: PositionedNode[] = [];
    const columns: DateColumn[] = [];
    sortedBuckets.forEach((bucketKey, dateIndex) => {
      const columnNodes = sortNodesStable(byBucket.get(bucketKey) || []);
      columns.push({
        dateKey: bucketKey,
        x: PADDING + dateIndex * (NODE_WIDTH + COLUMN_GAP),
        count: columnNodes.length,
        granularity,
      });

      columnNodes.forEach((node, rowIndex) => {
        all.push({
          ...node,
          dateKey: bucketKey,
          x: PADDING + dateIndex * (NODE_WIDTH + COLUMN_GAP),
          y: NODE_Y_BASE + rowIndex * (NODE_HEIGHT + ROW_GAP),
        });
      });
    });

    if (sortedBuckets.length === 0 && nodes.length > 0) {
      columns.push({
        dateKey: 'Unknown',
        x: PADDING,
        count: nodes.length,
        granularity,
      });
      sortNodesStable(nodes).forEach((node, index) => {
        all.push({
          ...node,
          dateKey: 'Unknown',
          x: PADDING,
          y: NODE_Y_BASE + index * (NODE_HEIGHT + ROW_GAP),
        });
      });
    }

    return {
      positionedNodes: all,
      positionedTreeGroups: [],
      dateColumns: columns,
      timelineGranularity: granularity,
      layoutLabel: timelineGranularityOverride ? granularityLabel(granularity) : `Auto Map · ${granularityLabel(granularity)}`,
    };
  }, [edges, expandedTreeGroups, isTopicLayout, isTreeLayout, nodes, timelineGranularityOverride, todayKey, topics]);

  const todayBucketKey = useMemo(
    () => bucketDateKey(todayKey, timelineGranularity),
    [todayKey, timelineGranularity],
  );

  const scrollToColumn = useCallback(
    (column: DateColumn | undefined) => {
      const viewport = viewportRef.current;
      if (!column || !viewport) return;
      const targetX = (column.x + NODE_WIDTH / 2) * scale;
      const targetY = (NODE_Y_BASE / 2) * scale;
      requestAnimationFrame(() => {
        viewport.scrollLeft = Math.max(0, targetX - viewport.clientWidth / 2);
        viewport.scrollTop = Math.max(0, targetY - viewport.clientHeight / 2);
      });
    },
    [scale],
  );

  const scrollToNode = useCallback(
    (node: PositionedNode | undefined) => {
      const viewport = viewportRef.current;
      if (!node || !viewport) return;
      const targetX = (node.x + NODE_WIDTH / 2) * scale;
      const targetY = (node.y + NODE_HEIGHT / 2) * scale;
      requestAnimationFrame(() => {
        viewport.scrollLeft = Math.max(0, targetX - viewport.clientWidth / 2);
        viewport.scrollTop = Math.max(0, targetY - viewport.clientHeight / 2);
      });
    },
    [scale],
  );

  const scrollToStart = useCallback(() => {
    const firstColumn = dateColumnsRef.current[0];
    if (firstColumn) {
      scrollToColumn(firstColumn);
      return;
    }
    const viewport = viewportRef.current;
    if (!viewport) return;
    requestAnimationFrame(() => {
      viewport.scrollLeft = 0;
      viewport.scrollTop = 0;
    });
  }, [scrollToColumn]);

  const headerLayout = useMemo(() => {
    const preferredSpacing = NODE_WIDTH + COLUMN_GAP;
    const spacing = preferredSpacing * scale;
    let labelMode: 'full' | 'short' | 'minimal' = 'short';
    if (spacing >= 120) labelMode = 'full';
    else if (spacing >= 80) labelMode = 'short';
    else labelMode = 'minimal';
    const densityEvery =
      spacing < 80 ? clamp(2, Math.ceil(80 / Math.max(spacing, 1)), 6) : 1;
    const fontSize = clamp(13, Math.round(spacing / 9), 16);
    return { labelMode, densityEvery, fontSize, spacing };
  }, [scale]);

  const nodeCountByColumn = useMemo(() => {
    const map = new Map<string, number>();
    dateColumns.forEach((col) => {
      const count = positionedNodes.filter((n) => n.dateKey === col.dateKey).length;
      map.set(col.dateKey, count);
    });
    return map;
  }, [dateColumns, positionedNodes]);

  const contentSize = useMemo(() => {
    const maxX = Math.max(0, ...positionedNodes.map((node) => node.x + NODE_WIDTH));
    const maxY = Math.max(0, ...positionedNodes.map((node) => node.y + NODE_HEIGHT));
    const maxGroupX = Math.max(0, ...positionedTreeGroups.map((group) => group.x + TREE_GROUP_WIDTH));
    const maxGroupY = Math.max(0, ...positionedTreeGroups.map((group) => group.y + TREE_GROUP_HEIGHT));
    const hasActionPanel = Boolean(onCreateDecision) || (isTopicLayout && Boolean(onCreateTopic));
    const createPanelWidth = hasActionPanel ? NODE_WIDTH + COLUMN_PADDING_X * 2 + COLUMN_GAP : 0;
    const emptyMinWidth = hasActionPanel && positionedNodes.length === 0
      ? PADDING + NODE_WIDTH + COLUMN_PADDING_X * 2 + PADDING
      : 0;
    const emptyMinHeight = hasActionPanel && positionedNodes.length === 0
      ? NODE_Y_BASE + NODE_HEIGHT + PADDING
      : 0;
    return {
      width: Math.max(emptyMinWidth, maxX, maxGroupX, PADDING + NODE_WIDTH) + createPanelWidth + PADDING + EXTRA_SCROLL,
      height: Math.max(emptyMinHeight, maxY, maxGroupY, NODE_Y_BASE + TREE_GROUP_HEIGHT) + PADDING + EXTRA_SCROLL,
    };
  }, [positionedNodes, positionedTreeGroups, onCreateDecision, onCreateTopic, isTopicLayout]);

  const nodeMap = useMemo(() => {
    return positionedNodes.reduce<Record<number, PositionedNode>>((acc, node) => {
      acc[node.id] = node;
      return acc;
    }, {});
  }, [positionedNodes]);

  const positionedNodesRef = useRef(positionedNodes);
  positionedNodesRef.current = positionedNodes;
  const dateColumnsRef = useRef(dateColumns);
  dateColumnsRef.current = dateColumns;

  const clientToCanvas = useMemo(() => {
    return (clientX: number, clientY: number) => {
      const content = contentRef.current;
      if (!content) return null;
      const rect = content.getBoundingClientRect();
      return {
        x: (clientX - rect.left) / scale,
        y: (clientY - rect.top) / scale,
      };
    };
  }, [scale]);

  const findNodeAtCanvas = useCallback(
    (canvasX: number, canvasY: number) => {
      return (
        positionedNodes.find(
          (n) =>
            canvasX >= n.x - LINK_DROP_PAD &&
            canvasX <= n.x + NODE_WIDTH + LINK_DROP_PAD &&
            canvasY >= n.y - LINK_DROP_PAD &&
            canvasY <= n.y + NODE_HEIGHT + LINK_DROP_PAD,
        ) ?? null
      );
    },
    [positionedNodes],
  );

  const findTopicColumnAtCanvas = useCallback((canvasX: number) => {
    return (
      dateColumnsRef.current.find((column) => {
        if (column.layoutKind !== 'cluster' || !column.topic) return false;
        return canvasX >= column.x - COLUMN_GAP / 2 && canvasX <= column.x + NODE_WIDTH + COLUMN_GAP / 2;
      }) ?? null
    );
  }, []);

  const handleMovePointerDown = useCallback((event: React.PointerEvent, node: PositionedNode) => {
    if (!onMoveDecisionToTopic || node.projectId == null || !isTopicLayout || mode !== 'viewer') return;
    event.stopPropagation();
    setPopover(null);
    moveSuppressClickRef.current = false;
    movePointerStartRef.current = { x: event.clientX, y: event.clientY };
    movePointerFromRef.current = { decisionId: node.id, projectId: node.projectId, topic: node.topic || 'other' };
    setMoveDragNodeId(node.id);
    try {
      (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    } catch {
      // ignore if capture unsupported
    }
  }, [isTopicLayout, mode, onMoveDecisionToTopic]);

  const clearMoveDrag = useCallback(() => {
    setMoveDragNodeId(null);
    setMoveHoverTopic(null);
    movePointerStartRef.current = null;
    movePointerFromRef.current = null;
  }, []);

  const autoScrollViewportForLink = useCallback((clientX: number, clientY: number) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    if (clientX < rect.left + LINK_AUTO_SCROLL_MARGIN) {
      viewport.scrollLeft -= LINK_AUTO_SCROLL_SPEED;
    } else if (clientX > rect.right - LINK_AUTO_SCROLL_MARGIN) {
      viewport.scrollLeft += LINK_AUTO_SCROLL_SPEED;
    }
    if (clientY < rect.top + LINK_AUTO_SCROLL_MARGIN) {
      viewport.scrollTop -= LINK_AUTO_SCROLL_SPEED;
    } else if (clientY > rect.bottom - LINK_AUTO_SCROLL_MARGIN) {
      viewport.scrollTop += LINK_AUTO_SCROLL_SPEED;
    }
  }, []);

  useEffect(() => {
    if (!moveDragNodeId || !onMoveDecisionToTopic || !isTopicLayout) return;

    const onMove = (event: PointerEvent) => {
      const start = movePointerStartRef.current;
      const from = movePointerFromRef.current;
      if (!start || !from) return;
      if (Math.hypot(event.clientX - start.x, event.clientY - start.y) <= LINK_DRAG_THRESHOLD) return;
      moveSuppressClickRef.current = true;
      autoScrollViewportForLink(event.clientX, event.clientY);
      const pt = clientToCanvas(event.clientX, event.clientY);
      const column = pt ? findTopicColumnAtCanvas(pt.x) : null;
      setMoveHoverTopic(column?.topic ?? null);
    };

    const onUp = (event: PointerEvent) => {
      const from = movePointerFromRef.current;
      const moved = moveSuppressClickRef.current;
      const pt = clientToCanvas(event.clientX, event.clientY);
      const targetColumn = pt ? findTopicColumnAtCanvas(pt.x) : null;
      clearMoveDrag();
      if (!from || !moved || !targetColumn?.topic || targetColumn.topic === from.topic) return;
      void onMoveDecisionToTopic(from.decisionId, from.projectId, targetColumn.topic);
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [
    autoScrollViewportForLink,
    clearMoveDrag,
    clientToCanvas,
    findTopicColumnAtCanvas,
    moveDragNodeId,
    onMoveDecisionToTopic,
    isTopicLayout,
  ]);

  const beginLinkPointer = useCallback((e: React.PointerEvent, node: PositionedNode) => {
    if (!linkingEnabled || linkingDisabled) return;
    e.stopPropagation();
    e.preventDefault();
    linkSuppressClickRef.current = false;
    linkPointerStartRef.current = { x: e.clientX, y: e.clientY };
    setLinkDragFrom({ nodeId: node.id, node });
    setLinkDragPointer(null);
    setLinkSourceId(null);
    setLinkHoverTargetId(null);
    try {
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    } catch {
      // ignore if capture unsupported
    }
  }, [linkingEnabled, linkingDisabled]);

  const saveViewport = useCallback(() => {
    const vp = viewportRef.current;
    if (vp) {
      viewportSnapshotRef.current = { left: vp.scrollLeft, top: vp.scrollTop };
    }
  }, []);

  const restoreViewport = useCallback(() => {
    const vp = viewportRef.current;
    const snap = viewportSnapshotRef.current;
    if (!vp || !snap) return;
    requestAnimationFrame(() => {
      vp.scrollLeft = snap.left;
      vp.scrollTop = snap.top;
    });
  }, []);

  const completeLink = useCallback(
    async (fromId: number, toId: number) => {
      if (!onCreateLink || fromId === toId) return;
      saveViewport();
      try {
        await onCreateLink(fromId, toId);
      } finally {
        restoreViewport();
      }
    },
    [onCreateLink, saveViewport, restoreViewport],
  );

  const completeUnlink = useCallback(
    async (fromId: number, toId: number) => {
      if (!onRemoveLink) return;
      saveViewport();
      try {
        await onRemoveLink(fromId, toId);
      } finally {
        restoreViewport();
      }
    },
    [onRemoveLink, saveViewport, restoreViewport],
  );

  const saveTopicTitle = useCallback(async () => {
    if (!editingTopic || !onRenameTopic) return;
    const title = editingTopic.value.trim();
    setEditingTopic(null);
    if (!title) return;
    await onRenameTopic(editingTopic.topic, title);
  }, [editingTopic, onRenameTopic]);

  const saveNewTopic = useCallback(async () => {
    if (!onCreateTopic || newTopicTitle == null) return;

    const title = (newTopicInputRef.current?.value ?? newTopicTitle).trim();
    setNewTopicError(null);

    if (!title) {
      setNewTopicTitle(null);
      return;
    }

    const topic = title.toLowerCase();

    if (!/^[a-z0-9_-]+$/.test(topic)) {
      setNewTopicError('Use letters, numbers, hyphens, or underscores. Example: Summer-Promo.');
      return;
    }

    if (topic.length > 64) {
      setNewTopicError('Topic must be 64 characters or fewer.');
      return;
    }

    setNewTopicTitle(null);
    await onCreateTopic(title);
  }, [newTopicTitle, onCreateTopic]);

  useEffect(() => {
    if (!linkDragFrom) return;

    const onMove = (e: PointerEvent) => {
      const start = linkPointerStartRef.current;
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > LINK_DRAG_THRESHOLD) {
        linkSuppressClickRef.current = true;
      }
      autoScrollViewportForLink(e.clientX, e.clientY);
      const pt = clientToCanvas(e.clientX, e.clientY);
      if (!pt) return;
      setLinkDragPointer(pt);
      const target = findNodeAtCanvas(pt.x, pt.y);
      setLinkHoverTargetId(
        target && target.id !== linkDragFrom.nodeId ? target.id : null,
      );
    };

    const onUp = (e: PointerEvent) => {
      const pt = clientToCanvas(e.clientX, e.clientY);
      const from = linkDragFrom;
      const moved = linkSuppressClickRef.current;
      linkPointerStartRef.current = null;
      setLinkDragFrom(null);
      setLinkDragPointer(null);
      setLinkHoverTargetId(null);

      if (!from || !moved) return;

      if (!pt) return;
      const target = findNodeAtCanvas(pt.x, pt.y);
      if (target && target.id !== from.nodeId) {
        void completeLink(from.nodeId, target.id);
      }
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [
    linkDragFrom,
    clientToCanvas,
    findNodeAtCanvas,
    completeLink,
    autoScrollViewportForLink,
  ]);

  useEffect(() => {
    if (!linkSourceId && !linkDragFrom) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setLinkSourceId(null);
        setLinkDragFrom(null);
        setLinkDragPointer(null);
        setLinkHoverTargetId(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [linkSourceId, linkDragFrom]);

  useLayoutEffect(() => {
    if (!viewportSnapshotRef.current) return;
    restoreViewport();
  }, [edges, positionedNodes, restoreViewport]);

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const isZoomGesture = event.ctrlKey || event.metaKey;
    if (!isZoomGesture) return;
    event.preventDefault();
    const delta = event.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    const nextScale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, scale + delta));
    if (nextScale === scale) return;

    const rect = viewport.getBoundingClientRect();
    const offsetX = event.clientX - rect.left + viewport.scrollLeft;
    const offsetY = event.clientY - rect.top + viewport.scrollTop;
    const scaleRatio = nextScale / scale;
    const nextScrollLeft = offsetX * scaleRatio - (event.clientX - rect.left);
    const nextScrollTop = offsetY * scaleRatio - (event.clientY - rect.top);
    viewport.scrollLeft = nextScrollLeft;
    viewport.scrollTop = nextScrollTop;
    setScale(nextScale);
  };

  const handleZoom = useCallback((direction: 'in' | 'out') => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const delta = direction === 'in' ? ZOOM_STEP : -ZOOM_STEP;
    const nextScale = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, scale + delta));
    if (nextScale === scale) return;
    const centerX = viewport.scrollLeft + viewport.clientWidth / 2;
    const centerY = viewport.scrollTop + viewport.clientHeight / 2;
    const scaleRatio = nextScale / scale;
    viewport.scrollLeft = centerX * scaleRatio - viewport.clientWidth / 2;
    viewport.scrollTop = centerY * scaleRatio - viewport.clientHeight / 2;
    setScale(nextScale);
  }, [scale]);

  useEffect(() => {
    onZoomPercentChange?.(Math.round((scale / BASE_ZOOM) * 100));
  }, [scale, onZoomPercentChange]);

  const handleMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest?.('[data-decision-link-handle]')) return;
    if (target.closest?.('[data-decision-node]')) return;
    if (target.closest?.('button')) return;
    const viewport = viewportRef.current;
    if (!viewport) return;
    dragState.current = {
      dragging: true,
      moved: false,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: viewport.scrollLeft,
      scrollTop: viewport.scrollTop,
    };
  };

  const handleMouseMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const viewport = viewportRef.current;
    if (!viewport || !dragState.current.dragging) return;
    const dx = event.clientX - dragState.current.startX;
    const dy = event.clientY - dragState.current.startY;
    if (!dragState.current.moved && Math.hypot(dx, dy) > 4) {
      dragState.current.moved = true;
    }
    const nextScrollLeft = dragState.current.scrollLeft - dx;
    const nextScrollTop = dragState.current.scrollTop - dy;
    viewport.scrollLeft = nextScrollLeft;
    viewport.scrollTop = nextScrollTop;
    userPannedViewportRef.current = true;
  };

  const handleMouseUp = () => {
    dragState.current.dragging = false;
    window.setTimeout(() => {
      dragState.current.moved = false;
    }, 0);
  };

  const handleNodeClick = (node: DecisionGraphNode, event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    dragState.current.dragging = false;
    dragState.current.moved = false;
    if (linkSuppressClickRef.current) {
      linkSuppressClickRef.current = false;
      return;
    }
    if (moveSuppressClickRef.current) {
      moveSuppressClickRef.current = false;
      return;
    }
    if (linkingEnabled && !linkingDisabled && linkSourceId != null) {
      setPopover(null);
      if (node.id === linkSourceId) {
        setLinkSourceId(null);
        return;
      }
      void completeLink(linkSourceId, node.id);
      setLinkSourceId(null);
      return;
    }
    if (mode === 'link-editor') {
      onToggleLink?.(node);
      return;
    }
    if (onSelectNode) {
      setPopover(null);
      onSelectNode(node.id);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    setPopover({
      node,
      x: rect.right + 12,
      y: rect.top,
    });
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (!(event.target instanceof HTMLElement)) return;
      if (event.target.closest('[data-decision-popover]')) return;
      if (event.target.closest('[data-decision-node]')) return;
      setPopover(null);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  useEffect(() => {
    if (mode === 'link-editor') {
      setPopover(null);
    }
  }, [mode]);

  useEffect(() => {
    if (!focusSeq) return;
    scrollToNode(
      positionedNodesRef.current.find((node) => node.projectSeq === focusSeq),
    );
  }, [focusSeq, scrollToNode]);

  useEffect(() => {
    if (!focusNodeId) return;
    scrollToNode(positionedNodesRef.current.find((node) => node.id === focusNodeId));
  }, [focusNodeId, scrollToNode]);

  const todayColumn = dateColumns.find((column) => column.dateKey === todayBucketKey);
  const todayNode = positionedNodes.find(
    (node) => formatDateKey(node.createdAt) === todayKey,
  );
  const autoFocusDone = useRef(false);

  useImperativeHandle(
    ref,
    () => ({
      jumpToToday: () => {
        if (todayNode) {
          scrollToNode(todayNode);
          return;
        }
        if (todayColumn) scrollToColumn(todayColumn);
      },
      jumpToTopic: (topic: string) => {
        const column = dateColumnsRef.current.find((item) => item.layoutKind === 'cluster' && item.topic === topic);
        if (column) scrollToColumn(column);
      },
      resetView: scrollToStart,
      zoomIn: () => handleZoom('in'),
      zoomOut: () => handleZoom('out'),
    }),
    [todayColumn, todayNode, scrollToColumn, scrollToNode, scrollToStart, handleZoom],
  );

  useEffect(() => {
    if (!autoFocusToday || autoFocusDone.current || userPannedViewportRef.current) return;
    if (!todayNode && !todayColumn) return;
    autoFocusDone.current = true;
    if (todayNode) {
      scrollToNode(todayNode);
      return;
    }
    scrollToColumn(todayColumn);
  }, [autoFocusToday, todayColumn, todayNode, scrollToColumn, scrollToNode]);

  useEffect(() => {
    if (!focusDateKey) return;
    const matchingNode = positionedNodesRef.current.find(
      (node) => formatDateKey(node.createdAt) === focusDateKey,
    );
    if (matchingNode) {
      scrollToNode(matchingNode);
      return;
    }
    const decisionColumns = dateColumnsRef.current.filter((column) => column.count > 0);
    if (decisionColumns.length === 0) return;
    const column = columnForDayKey(focusDateKey, decisionColumns, timelineGranularity);
    scrollToColumn(column as DateColumn | undefined);
  }, [focusDateKey, timelineGranularity, scrollToColumn, scrollToNode]);

  const headerStyleDateKey = (column: DateColumn) => {
    if (column.layoutKind === 'cluster' || column.layoutKind === 'tree') return todayKey;
    if (column.granularity === 'day') return column.dateKey;
    if (column.granularity === 'month') return `${column.dateKey}-01`;
    if (column.dateKey.startsWith('week:')) return column.dateKey.slice(5);
    return column.dateKey;
  };

  const showTopicMotionGuide =
    !topicGuideDismissed &&
    isTopicLayout &&
    mode === 'viewer' &&
    (onCreateTopic || onMoveDecisionToTopic);

  useEffect(() => {
    if (!showTopicMotionGuide) return;
    const timer = window.setInterval(() => {
      setTopicGuidePage((page) => (page === 2 ? 0 : ((page + 1) as 0 | 1 | 2)));
    }, 7000);
    return () => window.clearInterval(timer);
  }, [showTopicMotionGuide]);

  const topicGuideCopy =
    topicGuidePage === 0
      ? {
          title: 'Jump to a topic',
          subtitle: 'Use the Topics dropdown to find a topic column quickly.',
        }
      : topicGuidePage === 1
        ? {
          title: 'Move a decision',
          subtitle: 'Drag a card from one topic into another topic.',
        }
      : {
          title: 'Link decisions',
          subtitle: 'Drag from one link handle to another card.',
        };

  return (
    <div className="relative h-full w-full overflow-hidden rounded-lg border border-gray-200 bg-[#f8fafc] shadow-sm">
      {showTopicMotionGuide ? (
        <div className="absolute bottom-3 left-3 z-30 w-[340px] rounded-lg border border-slate-200/80 bg-white/95 p-3.5 shadow-[0_18px_44px_rgba(15,23,42,0.16)] backdrop-blur-sm">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="inline-flex items-center gap-1.5 rounded-full border border-[#3CCED7]/30 bg-[#F7FEFF] px-2.5 py-1.5 text-[11px] font-semibold text-[#256D75]">
                <span className="h-1.5 w-1.5 rounded-full bg-[#3CCED7]" />
                <span>{topicGuideCopy.title}</span>
              </div>
              <div className="mt-1 text-[10px] font-medium leading-tight text-slate-700">{topicGuideCopy.subtitle}</div>
            </div>
            <button
              type="button"
              onClick={() => setTopicGuideDismissed(true)}
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              aria-label="Close topic demo"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="topic-guide-surface relative mt-3 h-48 overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
            {topicGuidePage === 0 ? (
              <>
                <div className="topic-guide-dropdown absolute left-7 right-7 top-12 rounded-lg border border-slate-200 bg-white/90 p-2 shadow-[0_10px_24px_rgba(15,23,42,0.10)]">
                  <div className="topic-guide-select flex h-8 items-center gap-2 rounded-md border border-[#3CCED7]/35 bg-[#F7FEFF] px-2">
                    <div className="h-3.5 w-3.5 rounded bg-[#3CCED7]/20" />
                    <span className="relative min-w-0 flex-1 truncate text-[10px] font-semibold text-slate-700">
                      <span className="topic-guide-label-default block">Topics</span>
                      <span className="topic-guide-label-selected absolute inset-0 block truncate">Google Search</span>
                    </span>
                    <span className="topic-guide-caret text-[10px] text-slate-400">⌄</span>
                  </div>
                  <div className="topic-guide-menu mt-1.5 space-y-1 overflow-hidden">
                    <div className="flex items-center justify-between rounded bg-slate-50 px-2 py-1 text-[9px] font-medium text-slate-400">
                      <span>Audit</span>
                      <span>3</span>
                    </div>
                    <div className="topic-guide-topic-option flex items-center justify-between rounded border border-[#3CCED7]/35 bg-[#E8FBFC] px-2 py-1 text-[9px] font-semibold text-[#16828C]">
                      <span>Google Search</span>
                      <span>5</span>
                    </div>
                    <div className="flex items-center justify-between rounded bg-slate-50 px-2 py-1 text-[9px] font-medium text-slate-400">
                      <span>TikTok</span>
                      <span>6</span>
                    </div>
                  </div>
                </div>
              </>
            ) : topicGuidePage === 1 ? (
              <>
                <div className="absolute left-5 top-12 h-[124px] w-[130px] rounded-lg border border-dashed border-slate-300 bg-white/75 shadow-sm">
                  <div className="mx-auto mt-3 flex h-6 w-[90px] items-center justify-center rounded-md border border-slate-200 bg-white text-[10px] font-semibold text-slate-700 shadow-sm">
                    Topic 1
                  </div>
                  <div className="mx-auto mt-3 h-2.5 w-[72px] rounded bg-slate-200" />
                  <div className="mx-auto mt-1.5 h-2 w-12 rounded bg-slate-100" />
                </div>
                <div className="absolute right-5 top-12 h-[124px] w-[130px] rounded-lg border border-[#3CCED7]/50 bg-white/85 shadow-sm">
                  <div className="mx-auto mt-3 flex h-6 w-[90px] items-center justify-center rounded-md border border-[#3CCED7]/30 bg-white text-[10px] font-semibold text-slate-700 shadow-sm">
                    Topic 2
                  </div>
                  <div className="mx-auto mt-3 h-2.5 w-[72px] rounded bg-[#3CCED7]/20" />
                  <div className="mx-auto mt-1.5 h-2 w-12 rounded bg-[#3CCED7]/10" />
                </div>
                <div className="topic-guide-card absolute left-[38px] top-[106px] h-12 w-[94px] overflow-hidden rounded-lg border border-[#3CCED7]/50 bg-[#F7FEFF] shadow-[0_12px_28px_rgba(60,206,215,0.24)]">
                  <div className="absolute inset-y-0 left-0 w-1 bg-[#3CCED7]" />
                  <div className="ml-3 mr-2 mt-3 h-2 rounded bg-gradient-to-r from-slate-900 to-[#256D75]" />
                  <div className="ml-3 mr-3 mt-2 h-1.5 rounded bg-[#BEEFF3]" />
                  <div className="ml-3 mt-1.5 h-1.5 w-10 rounded bg-[#E1F8FA]" />
                </div>
                <div className="topic-guide-arrow absolute left-[148px] top-[128px] h-px w-9 bg-[#3CCED7]" />
              </>
            ) : (
              <>
                <svg className="absolute left-[60px] top-[52px] h-24 w-[220px]" viewBox="0 0 220 96" aria-hidden="true">
                  <path
                    className="topic-guide-link"
                    d="M 34 62 C 82 10, 138 10, 186 62"
                    fill="none"
                    stroke="#3CCED7"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                  />
                  <circle className="topic-guide-dot" cx="34" cy="62" r="4" fill="#3CCED7" />
                </svg>
                <div className="absolute left-7 top-[104px] h-14 w-[120px] rounded-lg border border-slate-200 bg-white shadow-[0_10px_24px_rgba(15,23,42,0.10)]">
                  <div className="absolute right-2 top-4 flex h-6 w-6 items-center justify-center rounded-full border border-[#3CCED7]/50 bg-white text-[11px] font-semibold text-[#3CCED7] shadow-sm">↔</div>
                  <div className="mx-3 mt-3 h-2 w-14 rounded bg-slate-900" />
                  <div className="mx-3 mt-2 h-1.5 w-12 rounded bg-slate-200" />
                </div>
                <div className="absolute right-7 top-[104px] h-14 w-[120px] rounded-lg border border-slate-200 bg-white shadow-[0_10px_24px_rgba(15,23,42,0.10)]">
                  <div className="absolute left-2 top-4 flex h-6 w-6 items-center justify-center rounded-full border border-[#3CCED7]/50 bg-white text-[11px] font-semibold text-[#3CCED7] shadow-sm">↔</div>
                  <div className="ml-12 mr-3 mt-3 h-2 rounded bg-slate-900" />
                  <div className="ml-12 mr-6 mt-2 h-1.5 rounded bg-slate-200" />
                </div>
              </>
            )}
          </div>
          <div className="relative mt-2.5 h-7 text-[10px] font-medium text-slate-500">
            <div
              key={`topic-guide-progress-${topicGuidePage}`}
              className="topic-guide-progress absolute left-0 top-0 h-px rounded-full bg-[#3CCED7]"
            />
            <div className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2" aria-label="Demo pages">
              <button
                type="button"
                onClick={() => setTopicGuidePage(0)}
                className={`h-1.5 w-1.5 rounded-full transition ${
                  topicGuidePage === 0 ? 'bg-[#3CCED7]' : 'bg-slate-300 hover:bg-slate-400'
                }`}
                aria-label="Show topic dropdown demo"
              />
              <button
                type="button"
                onClick={() => setTopicGuidePage(1)}
                className={`h-1.5 w-1.5 rounded-full transition ${
                  topicGuidePage === 1 ? 'bg-[#3CCED7]' : 'bg-slate-300 hover:bg-slate-400'
                }`}
                aria-label="Show move topic demo"
              />
              <button
                type="button"
                onClick={() => setTopicGuidePage(2)}
                className={`h-1.5 w-1.5 rounded-full transition ${
                  topicGuidePage === 2 ? 'bg-[#3CCED7]' : 'bg-slate-300 hover:bg-slate-400'
                }`}
                aria-label="Show link decisions demo"
              />
            </div>
            <button
              type="button"
              onClick={() => setTopicGuidePage((page) => (page === 2 ? 0 : ((page + 1) as 0 | 1 | 2)))}
              className="absolute right-0 top-0 inline-flex h-7 w-7 items-center justify-center rounded-md border border-slate-200 bg-white text-[14px] text-slate-500 shadow-sm transition hover:border-[#3CCED7]/40 hover:text-slate-800"
              aria-label="Next demo page"
            >
              →
            </button>
          </div>
          <style jsx>{`
            .topic-guide-surface {
              background-image:
                linear-gradient(rgba(148, 163, 184, 0.12) 1px, transparent 1px),
                linear-gradient(90deg, rgba(148, 163, 184, 0.12) 1px, transparent 1px);
              background-size: 26px 26px;
            }
            .topic-guide-progress {
              animation: topic-guide-progress 7s linear infinite;
            }
            .topic-guide-card {
              animation: topic-guide-move 3.4s ease-in-out infinite;
            }
            .topic-guide-dropdown {
              animation: topic-guide-dropdown 7s ease-in-out infinite;
            }
            .topic-guide-select {
              animation: topic-guide-select 7s ease-in-out infinite;
            }
            .topic-guide-label-default {
              animation: topic-guide-label-default 7s steps(1, end) infinite;
            }
            .topic-guide-label-selected {
              animation: topic-guide-label-selected 7s steps(1, end) infinite;
            }
            .topic-guide-caret {
              animation: topic-guide-caret 7s ease-in-out infinite;
            }
            .topic-guide-menu {
              animation: topic-guide-menu 7s ease-in-out infinite;
            }
            .topic-guide-topic-option {
              animation: topic-guide-topic-pulse 7s ease-in-out infinite;
            }
            .topic-guide-link {
              stroke-dasharray: 190;
              stroke-dashoffset: 190;
              animation: topic-guide-link 3.4s ease-in-out infinite;
            }
            .topic-guide-dot {
              animation: topic-guide-dot 3.4s ease-in-out infinite;
            }
            .topic-guide-arrow::after {
              content: '';
              position: absolute;
              right: -1px;
              top: -3px;
              width: 7px;
              height: 7px;
              border-right: 1.5px solid #3CCED7;
              border-top: 1.5px solid #3CCED7;
              transform: rotate(45deg);
            }
            @keyframes topic-guide-move {
              0%,
              30% {
                transform: translateX(0);
              }
              62%,
              84% {
                transform: translateX(142px) scale(1.02);
              }
              100% {
                transform: translateX(0);
              }
            }
            @keyframes topic-guide-progress {
              0% {
                width: 0%;
                opacity: 0.35;
              }
              8% {
                opacity: 1;
              }
              100% {
                width: 100%;
                opacity: 1;
              }
            }
            @keyframes topic-guide-dropdown {
              0%,
              22% {
                transform: translateY(0);
              }
              36%,
              70% {
                transform: translateY(-2px);
              }
              100% {
                transform: translateY(0);
              }
            }
            @keyframes topic-guide-select {
              0%,
              22% {
                border-color: rgba(60, 206, 215, 0.35);
                box-shadow: none;
              }
              32%,
              70% {
                border-color: rgba(60, 206, 215, 0.72);
                box-shadow: 0 0 0 3px rgba(60, 206, 215, 0.12);
              }
              86%,
              100% {
                border-color: rgba(60, 206, 215, 0.45);
                box-shadow: none;
              }
            }
            @keyframes topic-guide-label-default {
              0%,
              68% {
                opacity: 1;
              }
              69%,
              100% {
                opacity: 0;
              }
            }
            @keyframes topic-guide-label-selected {
              0%,
              68% {
                opacity: 0;
              }
              69%,
              100% {
                opacity: 1;
              }
            }
            @keyframes topic-guide-caret {
              0%,
              26% {
                transform: rotate(0deg);
              }
              34%,
              68% {
                transform: rotate(180deg);
              }
              76%,
              100% {
                transform: rotate(0deg);
              }
            }
            @keyframes topic-guide-menu {
              0%,
              25% {
                max-height: 0;
                opacity: 0;
                margin-top: 0;
              }
              34%,
              68% {
                max-height: 90px;
                opacity: 1;
                margin-top: 0.375rem;
              }
              78%,
              100% {
                max-height: 0;
                opacity: 0;
                margin-top: 0;
              }
            }
            @keyframes topic-guide-topic-pulse {
              0%,
              38% {
                transform: translateY(0);
                box-shadow: none;
              }
              50%,
              68% {
                transform: translateY(-1px);
                box-shadow: 0 6px 16px rgba(60, 206, 215, 0.18);
              }
              100% {
                transform: translateY(0);
                box-shadow: none;
              }
            }
            @keyframes topic-guide-link {
              0%,
              44% {
                stroke-dashoffset: 190;
                opacity: 0.3;
              }
              60%,
              88% {
                stroke-dashoffset: 0;
                opacity: 1;
              }
              100% {
                stroke-dashoffset: 190;
                opacity: 0.3;
              }
            }
            @keyframes topic-guide-dot {
              0%,
              44% {
                transform: translateX(0);
                opacity: 0;
              }
              60%,
              88% {
                transform: translateX(124px);
                opacity: 1;
              }
              100% {
                transform: translateX(0);
                opacity: 0;
              }
            }
          `}</style>
        </div>
      ) : null}
      {onZoomPercentChange == null ? (
        <div className="absolute right-3 top-3 z-10 flex items-center gap-2">
          {todayColumn ? (
            <button
              type="button"
              onClick={() => scrollToColumn(todayColumn)}
              className="h-8 rounded-md border border-gray-200 bg-white px-2.5 text-[11px] font-medium text-gray-600 shadow-sm transition hover:border-[#3CCED7]/50 hover:text-gray-900"
            >
              Today
            </button>
          ) : null}
          <div className="flex h-8 items-center gap-0.5 rounded-md border border-gray-200 bg-white px-1 shadow-sm">
            <button
              type="button"
              onClick={() => handleZoom('out')}
              className="inline-flex h-6 w-6 items-center justify-center rounded text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
              aria-label="Zoom out"
            >
              −
            </button>
            <span className="min-w-[40px] text-center text-[11px] font-medium tabular-nums text-gray-600">
              {Math.round((scale / BASE_ZOOM) * 100)}%
            </span>
            <button
              type="button"
              onClick={() => handleZoom('in')}
              className="inline-flex h-6 w-6 items-center justify-center rounded text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
              aria-label="Zoom in"
            >
              +
            </button>
          </div>
        </div>
      ) : null}
      <div
        ref={viewportRef}
        onWheel={handleWheel}
        onScroll={() => {
          userPannedViewportRef.current = true;
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{
          backgroundImage:
            'linear-gradient(rgba(148, 163, 184, 0.14) 1px, transparent 1px), linear-gradient(90deg, rgba(148, 163, 184, 0.14) 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
        className={`h-full w-full overflow-auto ${dragState.current.dragging ? 'cursor-grabbing' : 'cursor-grab'}`}
      >
        <div
          ref={contentRef}
          style={{
            width: contentSize.width,
            height: contentSize.height,
            transform: `scale(${scale})`,
            transformOrigin: 'top left',
          }}
          className="relative"
        >
          {dateColumns.map((column) => {
            const nodeCount = nodeCountByColumn.get(column.dateKey) ?? 0;
            if (nodeCount === 0) return null;
            const effectiveCount = Math.max(nodeCount, 1);
            const panelHeight = Math.max(160, HEADER_BAND_HEIGHT + effectiveCount * (NODE_HEIGHT + ROW_GAP) + ROW_GAP);
            return (
              <div
                key={`panel-${column.dateKey}`}
                className={`absolute rounded-lg border bg-white/75 shadow-[0_1px_3px_rgba(15,23,42,0.05)] backdrop-blur-sm transition ${
                  moveHoverTopic != null && column.topic === moveHoverTopic
                    ? 'border-[#3CCED7] bg-[#e9fbfc]/90 shadow-[0_0_0_3px_rgba(60,206,215,0.16)]'
                    : 'border-white'
                }`}
                style={{
                  left: column.x - COLUMN_PADDING_X,
                  top: BAND_TOP_PADDING,
                  width: NODE_WIDTH + COLUMN_PADDING_X * 2,
                  height: panelHeight,
                  pointerEvents: 'none',
                  zIndex: 0,
                }}
              />
            );
          })}

          {dateColumns.length === 0 && onCreateDecision && (
            <div
              className="absolute rounded-lg border border-white bg-white/75 shadow-[0_1px_3px_rgba(15,23,42,0.05)] backdrop-blur-sm"
              style={{
                left: PADDING - COLUMN_PADDING_X,
                top: BAND_TOP_PADDING,
                width: NODE_WIDTH + COLUMN_PADDING_X * 2,
                height: HEADER_BAND_HEIGHT + NODE_HEIGHT + ROW_GAP * 2,
                pointerEvents: 'none',
                zIndex: 0,
              }}
            />
          )}

          {(onCreateDecision || (isTopicLayout && onCreateTopic)) ? (() => {
            const lastCol = dateColumns[dateColumns.length - 1];
            const createPanelX = lastCol
              ? lastCol.x + NODE_WIDTH + COLUMN_PADDING_X + COLUMN_GAP - COLUMN_PADDING_X
              : PADDING - COLUMN_PADDING_X;
            const hasCreatePanel = Boolean(onCreateDecision);
            const hasTopicPanel = isTopicLayout && Boolean(onCreateTopic);
            return (
              <>
                {hasCreatePanel ? (
                  <div
                    className="group absolute flex items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white/55 transition hover:border-[#3CCED7]/70 hover:bg-white hover:shadow-[0_8px_20px_rgba(15,23,42,0.08)]"
                    style={{
                      left: createPanelX,
                      top: BAND_TOP_PADDING,
                      width: NODE_WIDTH + COLUMN_PADDING_X * 2,
                      height: 112,
                      zIndex: 25,
                      pointerEvents: 'auto',
                    }}
                  >
                    <button
                      type="button"
                      onClick={onCreateDecision}
                      className="inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium text-gray-500 transition group-hover:text-[#159aa3]"
                    >
                      <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-gray-100 text-gray-500 transition group-hover:bg-[#3CCED7]/10 group-hover:text-[#159aa3]">
                        <Plus className="h-3.5 w-3.5" />
                      </span>
                      Create Decision
                    </button>
                  </div>
                ) : null}
                {hasTopicPanel ? (
                  <div
                    className="group absolute flex items-center justify-center rounded-lg border border-dashed border-slate-300 bg-white/55 transition hover:border-[#3CCED7]/70 hover:bg-white hover:shadow-[0_8px_20px_rgba(15,23,42,0.08)]"
                    style={{
                      left: createPanelX,
                      top: BAND_TOP_PADDING + (hasCreatePanel ? 128 : 0),
                      width: NODE_WIDTH + COLUMN_PADDING_X * 2,
                      height: 96,
                      zIndex: 25,
                      pointerEvents: 'auto',
                    }}
                  >
                    {newTopicTitle != null ? (
                      <div className="flex w-full flex-wrap items-center gap-1.5 px-3">
                        <input
                          data-new-topic-input
                          ref={newTopicInputRef}
                          autoFocus
                          value={newTopicTitle}
                          onChange={(event) => {
                            setNewTopicTitle(event.target.value);
                            setNewTopicError(null);
                          }}
                          onInput={(event) => setNewTopicTitle(event.currentTarget.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.preventDefault();
                              void saveNewTopic();
                            }
                            if (event.key === 'Escape') {
                              event.preventDefault();
                              setNewTopicTitle(null);
                            }
                          }}
                          className="h-7 w-40 rounded-md border border-slate-200 bg-white px-2 text-xs font-medium text-slate-800 outline-none transition focus:border-[#3CCED7]"
                          maxLength={80}
                        />
                        <button
                          data-new-topic-save
                          type="button"
                          onMouseDown={(event) => {
                            event.preventDefault();
                            void saveNewTopic();
                          }}
                          onClick={() => void saveNewTopic()}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-emerald-600 transition hover:bg-emerald-50"
                          aria-label="Save topic"
                        >
                          <CheckCircle2 className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setNewTopicTitle(null)}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                          aria-label="Cancel topic"
                        >
                          <X className="h-4 w-4" />
                        </button>
                        {newTopicError && (
                          <p role="alert" className="basis-full text-xs text-rose-600">
                            {newTopicError}
                          </p>
                        )}
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => {
                          setNewTopicError(null);
                          setNewTopicTitle('');
                        }}
                        className="inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium text-slate-400 transition group-hover:text-[#159aa3]"
                      >
                        <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-100 text-slate-400 transition group-hover:bg-[#3CCED7]/10 group-hover:text-[#159aa3]">
                          <Plus className="h-3.5 w-3.5" />
                        </span>
                        Add Topic
                      </button>
                    )}
                  </div>
                ) : null}
              </>
            );
          })() : null}

          {dateColumns.length > 0 ? (
            <div
              className="absolute h-px bg-slate-200/80"
              style={{
                left: PADDING - COLUMN_PADDING_X,
                top: BAND_TOP_PADDING + HEADER_BAND_HEIGHT / 2,
                width: Math.max(0, contentSize.width - (PADDING - COLUMN_PADDING_X) * 2),
                zIndex: 1,
                pointerEvents: 'none',
              }}
            />
          ) : null}

          <svg
            className="absolute inset-0 h-full w-full"
            style={{ pointerEvents: linkingEnabled && !linkingDisabled ? 'auto' : 'none', zIndex: 10 }}
          >
            <defs>
              <marker
                id="decision-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="5"
                markerHeight="5"
                orient="auto"
              >
                <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill={EDGE_STROKE_NORMAL} />
              </marker>
              <marker
                id="decision-arrow-hover"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="5"
                markerHeight="5"
                orient="auto"
              >
                <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill={EDGE_STROKE_HOVER} />
              </marker>
              <filter id="edge-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feDropShadow dx="0" dy="0" stdDeviation="2.5" floodColor="#3CCED7" floodOpacity="0.32" />
              </filter>
            </defs>
            <g style={{ pointerEvents: 'none' }}>
              {positionedTreeGroups.flatMap((group) =>
                group.childIds
                  .map((childId) => nodeMap[childId])
                  .filter((child): child is PositionedNode => Boolean(child) && child.dateKey === 'tree:1')
                  .map((child) => {
                    const startX = group.x + TREE_GROUP_WIDTH;
                    const startY = group.y + TREE_GROUP_HEIGHT / 2;
                    const endX = child.x - EDGE_END_GAP;
                    const endY = child.y + NODE_HEIGHT / 2;
                    const midX = startX + Math.max(48, (endX - startX) / 2);
                    const path = `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`;
                    return (
                      <path
                        key={`tree-group-edge-${group.key}-${child.id}`}
                        d={path}
                        stroke={EDGE_STROKE_NORMAL}
                        strokeWidth={1.75}
                        fill="none"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        markerEnd="url(#decision-arrow)"
                      />
                    );
                  }),
              )}
              {edges.map((edge, idx) => {
                const fromNode = nodeMap[edge.from];
                const toNode = nodeMap[edge.to];
                if (!fromNode || !toNode) return null;
                const path = buildDecisionEdgePath(
                  getDecisionEdgeEndpoints(
                    fromNode,
                    toNode,
                    NODE_WIDTH,
                    NODE_HEIGHT,
                    EDGE_END_GAP,
                  ),
                );
                const hovered = linkingEnabled && !linkingDisabled && hoveredEdgeIdx === idx;
                return (
                  <path
                    key={`edge-${idx}`}
                    d={path}
                    stroke={hovered ? EDGE_STROKE_HOVER : EDGE_STROKE_NORMAL}
                    strokeWidth={hovered ? 3 : 1.75}
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    markerEnd={hovered ? 'url(#decision-arrow-hover)' : 'url(#decision-arrow)'}
                    filter={hovered ? 'url(#edge-glow)' : undefined}
                  />
                );
              })}
              {linkDragFrom && (
                <path
                  d={(() => {
                    const pointer = linkDragPointer ?? {
                      x: linkDragFrom.node.x + NODE_WIDTH,
                      y: linkDragFrom.node.y + NODE_HEIGHT / 2,
                    };
                    const fakeTarget = {
                      x: pointer.x - NODE_WIDTH / 2,
                      y: pointer.y - NODE_HEIGHT / 2,
                    };
                    return buildDecisionEdgePath(
                      getDecisionEdgeEndpoints(
                        linkDragFrom.node,
                        fakeTarget,
                        NODE_WIDTH,
                        NODE_HEIGHT,
                        EDGE_END_GAP,
                      ),
                    );
                  })()}
                  stroke={linkHoverTargetId ? EDGE_STROKE_HOVER : EDGE_STROKE_NORMAL}
                  strokeWidth={linkHoverTargetId ? 2.75 : 2}
                  fill="none"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  markerEnd="url(#decision-arrow)"
                />
              )}
            </g>
            {linkingEnabled && !linkingDisabled && onRemoveLink && (
              <g style={{ pointerEvents: 'auto' }}>
                {edges.map((edge, idx) => {
                  const fromNode = nodeMap[edge.from];
                  const toNode = nodeMap[edge.to];
                  if (!fromNode || !toNode) return null;
                  const path = buildDecisionEdgePath(
                    getDecisionEdgeEndpoints(
                      fromNode,
                      toNode,
                      NODE_WIDTH,
                      NODE_HEIGHT,
                      EDGE_END_GAP,
                    ),
                  );
                  return (
                    <path
                      key={`edge-hit-${idx}`}
                      d={path}
                      fill="none"
                      stroke="transparent"
                      strokeWidth={16}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      cursor="pointer"
                      onMouseEnter={() => setHoveredEdgeIdx(idx)}
                      onMouseLeave={() => setHoveredEdgeIdx(null)}
                      onClick={() => void completeUnlink(edge.from, edge.to)}
                      aria-label={`Remove link between decisions`}
                    />
                  );
                })}
              </g>
            )}
          </svg>

          <div className="pointer-events-none absolute inset-0" style={{ zIndex: 20 }}>
            {dateColumns.map((column, index) => {
              const { labelMode, densityEvery, fontSize } = headerLayout;
              const showLabel = index % densityEvery === 0;
              const label = formatColumnLabel(column, labelMode);
              const labelText = label.secondary ? `${label.primary} ${label.secondary}` : label.primary;
              const isTopicColumn = column.layoutKind === 'cluster' && Boolean(column.topic);
              const chipHeight = isTopicColumn ? 56 : label.secondary ? 40 : 32;
              const chipY = BAND_TOP_PADDING + (HEADER_BAND_HEIGHT - chipHeight) / 2;
              const centerX = column.x + NODE_WIDTH / 2;
              const canDeleteTopic =
                isTopicColumn &&
                column.count === 0 &&
                Boolean(onDeleteTopic);
              const topicActionWidth =
                isTopicColumn
                  ? 24 + (onRenameTopic ? 22 : 0) + (canDeleteTopic ? 22 : 0)
                  : 24;
              const chipWidth = Math.max(
                isTopicColumn ? 260 : column.granularity === 'week' ? 128 : 78,
                labelText.length * (fontSize * 0.48) + topicActionWidth,
              );
              const isEditingTopic = Boolean(
                column.layoutKind === 'cluster' &&
                column.topic &&
                editingTopic?.topic === column.topic,
              );

              if (!showLabel) {
                const { tickColor } = getWeekdayStyle(headerStyleDateKey(column));
                return (
                  <div
                    key={`header-${column.dateKey}`}
                    className="absolute"
                    style={{
                      left: centerX - 4,
                      top: chipY + chipHeight / 2 - 2,
                      width: 8,
                      height: 8,
                      borderRadius: 999,
                      backgroundColor: tickColor,
                    }}
                  />
                );
              }

              return (
                <div
                  key={`header-${column.dateKey}`}
                  className={`group pointer-events-auto absolute flex items-center justify-center gap-1.5 rounded-lg leading-none shadow-sm transition ${
                    isTopicColumn
                      ? 'border border-slate-200 bg-white/95 text-slate-600 hover:border-[#3CCED7]/50 hover:shadow-[0_8px_20px_rgba(15,23,42,0.10)]'
                      : 'border border-slate-200 bg-white/95 text-slate-500 hover:border-[#3CCED7]/50 hover:text-slate-800'
                  }`}
                  style={{
                    left: centerX - chipWidth / 2,
                    top: chipY,
                    width: chipWidth,
                    height: chipHeight,
                    fontSize,
                    letterSpacing: 0,
                  }}
                  onMouseEnter={(event) => {
                    if (isEditingTopic) return;
                    const rect = event.currentTarget.getBoundingClientRect();
                    setHeaderTooltip({
                      dateKey:
                        column.layoutKind === 'cluster'
                          ? `${label.primary} · ${label.secondary}`
                          : column.layoutKind === 'tree'
                          ? `${label.primary} · ${label.secondary}`
                          : column.granularity === 'day'
                          ? column.dateKey
                          : bucketTooltip(column.dateKey, column.granularity, column.count),
                      x: rect.left + rect.width / 2,
                      y: rect.top - 8,
                      count: column.count,
                    });
                  }}
                  onMouseLeave={() => setHeaderTooltip(null)}
                >
                  {isEditingTopic ? (
                    <input
                      autoFocus
                      value={editingTopic?.value ?? ''}
                      onChange={(event) =>
                        setEditingTopic((current) =>
                          current ? { ...current, value: event.target.value } : current,
                        )
                      }
                      onBlur={() => void saveTopicTitle()}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') {
                          event.preventDefault();
                          void saveTopicTitle();
                        }
                        if (event.key === 'Escape') {
                          event.preventDefault();
                          setEditingTopic(null);
                        }
                      }}
                      onClick={(event) => event.stopPropagation()}
                      className="h-8 min-w-0 flex-1 rounded border border-[#3CCED7]/50 bg-white px-2 text-center text-[14px] font-semibold text-slate-900 outline-none"
                      maxLength={80}
                    />
                  ) : isTopicColumn ? (
                    <>
                      <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden px-2">
                        <div className="h-9 w-1.5 shrink-0 rounded-full bg-[#3CCED7]" />
                        <span className="flex min-w-0 flex-col overflow-hidden text-left">
                          <span className="max-w-full truncate text-[15px] font-semibold leading-tight text-slate-900">
                            {label.primary}
                          </span>
                          <span className="mt-1.5 text-[12px] font-medium leading-none text-slate-500">
                            {column.count} decision{column.count === 1 ? '' : 's'}
                          </span>
                        </span>
                      </div>
                      <div className="mr-1 flex shrink-0 items-center gap-0.5">
                        {column.topic && onRenameTopic ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setHeaderTooltip(null);
                              setEditingTopic({ topic: column.topic || 'other', value: label.primary });
                            }}
                            aria-label={`Rename ${label.primary}`}
                            title="Rename"
                            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                          >
                            <PencilLine className="h-3.5 w-3.5" />
                          </button>
                        ) : null}
                        {canDeleteTopic && column.topic ? (
                          <button
                            type="button"
                            onClick={(event) => {
                              event.stopPropagation();
                              setHeaderTooltip(null);
                              void onDeleteTopic?.(column.topic || 'other');
                            }}
                            aria-label={`Delete empty topic ${label.primary}`}
                            title="Delete empty topic"
                            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-rose-50 hover:text-rose-500"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        ) : null}
                      </div>
                    </>
                  ) : (
                    <>
                      <span className="flex min-w-0 flex-col items-center overflow-hidden">
                        <span className="max-w-full truncate text-[11px] font-semibold">{label.primary}</span>
                        {label.secondary ? (
                          <span className="mt-1 max-w-full truncate text-[10px] font-medium text-slate-400">
                            {label.secondary}
                          </span>
                        ) : null}
                      </span>
                      {column.layoutKind === 'cluster' && column.topic && onRenameTopic ? (
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setHeaderTooltip(null);
                            setEditingTopic({ topic: column.topic || 'other', value: label.primary });
                          }}
                          aria-label={`Rename ${label.primary}`}
                          title="Rename"
                          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-slate-400 opacity-0 transition hover:bg-slate-100 hover:text-slate-700 group-hover:opacity-100"
                        >
                          <PencilLine className="h-3 w-3" />
                        </button>
                      ) : null}
                      {canDeleteTopic && column.topic ? (
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            setHeaderTooltip(null);
                            void onDeleteTopic?.(column.topic || 'other');
                          }}
                          aria-label={`Delete empty topic ${label.primary}`}
                          title="Delete empty topic"
                          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-slate-400 opacity-0 transition hover:bg-rose-50 hover:text-rose-500 group-hover:opacity-100"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      ) : null}
                    </>
                  )}
                </div>
              );
            })}
          </div>

          {positionedTreeGroups.map((group, index) => {
            const accentColors = [
              'from-[#E8FBFC] to-[#F6FEFF] text-[#126B72] ring-[#3CCED7]/45',
              'from-[#EEF2FF] to-white text-[#3730A3] ring-indigo-200',
              'from-[#F0FDF4] to-white text-[#166534] ring-emerald-200',
              'from-[#FFF7ED] to-white text-[#9A3412] ring-orange-200',
            ];
            const accent = accentColors[index % accentColors.length];
            return (
              <button
                key={`tree-group-${group.key}`}
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setExpandedTreeGroups((current) => {
                    const next = new Set(current);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  });
                }}
                className={`group absolute flex flex-col justify-center rounded-[18px] bg-gradient-to-br px-4 text-left shadow-[0_10px_26px_rgba(15,23,42,0.10)] ring-2 transition hover:-translate-y-[1px] hover:shadow-[0_18px_36px_rgba(15,23,42,0.14)] ${accent}`}
                style={{
                  width: TREE_GROUP_WIDTH,
                  height: TREE_GROUP_HEIGHT,
                  left: group.x,
                  top: group.y,
                  zIndex: 22,
                }}
                aria-expanded={group.expanded}
                aria-label={`${group.expanded ? 'Collapse' : 'Expand'} ${group.title}`}
                title={group.expanded ? 'Collapse group' : 'Expand group'}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0">
                    <span className="block truncate text-[17px] font-semibold leading-tight">
                      {group.title}
                    </span>
                    <span className="mt-1 block text-[13px] font-medium opacity-70">
                      {group.count} decision{group.count === 1 ? '' : 's'}
                    </span>
                  </span>
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/80 text-[22px] font-semibold shadow-sm transition group-hover:bg-white">
                    {group.expanded ? '−' : '+'}
                  </span>
                </div>
              </button>
            );
          })}

          {positionedNodes.map((node) => {
            const canMoveNode =
              Boolean(onMoveDecisionToTopic) &&
              isTopicLayout &&
              node.projectId != null &&
              mode === 'viewer';
            const linkDragActive = Boolean(linkDragFrom);
            const isLinkDragSource = linkDragFrom?.nodeId === node.id;
            const isExistingLinkTarget = Boolean(
              linkDragFrom &&
                edges.some(
                  (edge) =>
                    (edge.from === linkDragFrom.nodeId && edge.to === node.id) ||
                    (edge.to === linkDragFrom.nodeId && edge.from === node.id),
                ),
            );
            const canDropLinkTarget = linkDragActive && !isLinkDragSource && !isExistingLinkTarget;
            const isBlockedLinkTarget = linkDragActive && (isLinkDragSource || isExistingLinkTarget);
            return (
              <div
                key={node.id}
                className="group absolute"
                draggable={false}
                onDragEnd={clearMoveDrag}
                style={{
                  width: NODE_WIDTH,
                  height: NODE_HEIGHT,
                  left: node.x,
                  top: node.y,
                  zIndex: 20,
                }}
              >
              {linkingEnabled && !linkingDisabled && (
                <button
                  type="button"
                  data-decision-link-handle
                  aria-label={`Link ${node.title?.trim() || 'decision'} to another card`}
                  title="Click to pick source, then click target · or drag from this card"
                  className="absolute right-0 top-0 z-30 flex h-full w-10 cursor-crosshair items-center justify-center"
                  onPointerDown={(e) => beginLinkPointer(e, node)}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (linkSuppressClickRef.current) {
                      linkSuppressClickRef.current = false;
                      return;
                    }
                    setLinkSourceId((prev) => (prev === node.id ? null : node.id));
                  }}
                >
                  <span
                    className={`inline-flex h-7 w-7 items-center justify-center rounded-full border bg-white shadow-sm transition ${
                      linkSourceId === node.id || linkHoverTargetId === node.id
                        ? 'border-[#3CCED7] bg-[#3CCED7] text-white'
                        : canDropLinkTarget
                          ? 'border-[#3CCED7] bg-[#E8FBFC] text-[#16828C] opacity-100'
                        : 'border-[#3CCED7]/50 text-[#3CCED7] opacity-50 group-hover:opacity-100'
                    }`}
                  >
                    <Link2 className="h-3.5 w-3.5" strokeWidth={2.5} />
                  </span>
                </button>
              )}
              <button
                type="button"
                data-decision-node
                onMouseDown={(event) => event.stopPropagation()}
                onPointerDown={
                  canMoveNode
                    ? (event) => handleMovePointerDown(event, node)
                    : linkingEnabled && !linkingDisabled
                      ? (event) => beginLinkPointer(event, node)
                      : undefined
                }
                onClick={(event) => handleNodeClick(node, event)}
                title={
                  linkingEnabled && !linkingDisabled && linkSourceId != null
                    ? 'Click to connect to selected card'
                    : undefined
                }
                className={`flex h-full w-full flex-col justify-between gap-2 overflow-hidden rounded-lg bg-white/95 px-3.5 py-2.5 text-left shadow-[0_2px_5px_rgba(15,23,42,0.06)] transition-all duration-150 will-change-transform hover:-translate-y-[1px] hover:bg-white hover:shadow-[0_10px_24px_rgba(15,23,42,0.10)] ${
                  canMoveNode ? 'cursor-grab active:cursor-grabbing' : linkingEnabled && !linkingDisabled ? 'cursor-pointer' : ''
                } ${moveDragNodeId === node.id ? 'opacity-50' : ''} ${
                  isBlockedLinkTarget ? 'opacity-45 saturate-50' : ''
                } ${
                  canDropLinkTarget ? 'bg-[#F7FEFF] ring-1 ring-[#3CCED7]/30' : ''
                } ${statusCardOpacity(node.status)} ${riskLeftBorder(node.riskLevel)} ${
                  linkSourceId === node.id
                    ? 'ring-2 ring-[#3CCED7] shadow-[0_0_0_3px_rgba(60,206,215,0.2)]'
                    : linkHoverTargetId === node.id
                      ? 'ring-2 ring-dashed ring-[#3CCED7] shadow-[0_0_0_3px_rgba(60,206,215,0.15)]'
                    : mode === 'link-editor' && node.projectSeq && removedSeqSet.has(node.projectSeq)
                    ? 'ring-2 ring-rose-200'
                    : mode === 'link-editor' && node.projectSeq && selectedSeqSet.has(node.projectSeq)
                      ? 'ring-2 ring-emerald-200'
                      : mode === 'selector' && node.projectSeq && selectedSeqSet.has(node.projectSeq)
                        ? 'ring-2 ring-emerald-200'
                        : selectedNodeId != null && node.id === selectedNodeId
                          ? 'ring-2 ring-[#3CCED7]'
                          : 'ring-1 ring-slate-200 hover:ring-slate-300'
                } ${
                  focusSeq && node.projectSeq === focusSeq
                    ? 'ring-2 ring-[#3CCED7]'
                    : ''
                }`}
              >
                <div className="flex min-h-0 items-start gap-2">
                  <p
                    className="min-w-0 flex-1 line-clamp-2 break-words text-[15px] font-semibold leading-snug text-slate-950"
                    title={node.title?.trim() || 'Untitled'}
                  >
                    {node.title?.trim() || 'Untitled'}
                  </p>
                  {node.projectSeq ? (
                    <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[12px] font-semibold tabular-nums text-slate-500">
                      #{node.projectSeq}
                    </span>
                  ) : null}
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <DecisionStatusPill status={node.status as DecisionStatus} />
                  {node.riskLevel ? (
                    <span
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[12px] font-medium ${riskPillStyle(node.riskLevel)}`}
                      style={optionColorStyle(DECISION_RISK.colors[node.riskLevel])}
                    >
                      {optionLabel(DECISION_RISK, node.riskLevel)}
                    </span>
                  ) : null}
                </div>
              </button>
              {canDelete && onDelete && mode === 'viewer' ? (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(node);
                  }}
                  className="absolute -right-2 -top-2 z-10 flex h-5 w-5 items-center justify-center rounded-full bg-white text-rose-500 opacity-0 shadow-sm ring-1 ring-rose-200 transition-all hover:bg-rose-50 group-hover:opacity-100"
                  aria-label="Delete decision"
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
              </div>
            );
          })}

        </div>
      </div>

      {popover ? (
        <div
          data-decision-popover
          className="fixed z-50 w-64 rounded-lg border border-slate-200 bg-white p-4 shadow-[0_18px_40px_rgba(15,23,42,0.16)]"
          style={{ left: popover.x, top: popover.y }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm font-semibold text-gray-900">
              {popover.node.title || 'Untitled'}
            </div>
            {onEditLinks ? (
              <button
                type="button"
                onClick={() => {
                  setPopover(null);
                  onEditLinks(popover.node);
                }}
                disabled={!popover.node.projectSeq}
                title="Edit Links"
                aria-label="Edit Links"
                className={`inline-flex h-6 w-6 items-center justify-center rounded-md border text-xs ${
                  popover.node.projectSeq
                    ? 'border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700'
                    : 'cursor-not-allowed border-gray-100 text-gray-300'
                }`}
              >
                <Link2 className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusColor(
                popover.node.status
              )}`}
            >
              {popover.node.status}
            </span>
            {popover.node.riskLevel ? (
              <span className="rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">
                {optionLabel(DECISION_RISK, popover.node.riskLevel)}
              </span>
            ) : null}
          </div>
          <div className="mt-2 text-xs text-gray-500">
            Created: {new Date(popover.node.createdAt).toLocaleString()}
          </div>
          <div className="mt-3">
            <div className="flex items-center gap-2">
              {mode === 'selector' ? (
                <button
                  type="button"
                  onClick={() => onAddDecision?.(popover.node)}
                  disabled={
                    !popover.node.projectSeq ||
                    selectedSeqSet.has(popover.node.projectSeq)
                  }
                  className={`inline-flex items-center rounded-md px-3 py-1.5 text-xs font-semibold ${
                    !popover.node.projectSeq ||
                    selectedSeqSet.has(popover.node.projectSeq)
                      ? 'cursor-not-allowed bg-gray-200 text-gray-500'
                      : 'bg-emerald-600 text-white hover:bg-emerald-700'
                  }`}
                >
                  {!popover.node.projectSeq ||
                  selectedSeqSet.has(popover.node.projectSeq)
                    ? 'Added'
                    : '+ Add'}
                </button>
              ) : null}
              {popover.node.status === 'DRAFT' && onEditDecision ? (
                <button
                  type="button"
                  onClick={() => {
                    setPopover(null);
                    onEditDecision(popover.node);
                  }}
                  className="inline-flex w-[80px] items-center justify-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-semibold text-amber-700 hover:border-amber-300"
                >
                  <PencilLine className="h-3.5 w-3.5" />
                  Edit
                </button>
              ) : null}
              {popover.node.status === 'COMMITTED' && canReview ? (
                <Link
                  href={buildUrl((getReviewUrl ?? defaultGetReviewUrl)(popover.node.slug ?? popover.node.id, projectId))}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex w-[80px] items-center justify-center gap-1.5 rounded-md border border-[#3CCED7]/30 bg-[#3CCED7]/10 px-3 py-1.5 text-xs font-semibold text-[#1a9ba3] hover:border-blue-300"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Review
                </Link>
              ) : null}
              <Link
                href={buildUrl((getDecisionUrl ?? defaultGetDecisionUrl)(popover.node.slug ?? popover.node.id, projectId))}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white"
              >
                <FileText className="h-3.5 w-3.5" />
                Details
              </Link>
            </div>
          </div>
        </div>
      ) : null}

      {headerTooltip ? (
        <div
          className="fixed z-50 -translate-x-1/2 -translate-y-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs text-slate-700 shadow-[0_12px_28px_rgba(15,23,42,0.14)]"
          style={{ left: headerTooltip.x, top: headerTooltip.y }}
        >
          <div className="font-semibold text-slate-900">
            {formatDateTooltip(headerTooltip.dateKey)}
          </div>
          <div className="text-[11px] text-slate-600">
            {headerTooltip.count} decision{headerTooltip.count === 1 ? '' : 's'}
          </div>
        </div>
      ) : null}
    </div>
  );
});

export default DecisionTree;
