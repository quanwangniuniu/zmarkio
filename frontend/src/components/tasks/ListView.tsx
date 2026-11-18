'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { ArrowDownToLine, ArrowUpToLine, Bookmark, ChevronDown, ChevronLeft, ChevronRight, Loader2, Pin, Plus, Save, Search, Trash2, X, ListTodo} from 'lucide-react';
import EmptyState from '@/components/state-feedback/EmptyState';
import toast from 'react-hot-toast';
import type { TaskBulkFailureItem, TaskData, TaskListFilters } from '@/types/task';
import { Id } from '@/types/common';
import { userDisplayName } from '@/types/task';
import { TASK_PRIORITY_BY_VALUE, TASK_PRIORITY_OPTIONS } from '@/lib/tasks/taskPriorities';
import { TASK_STATUS_BY_VALUE, TASK_STATUS_OPTIONS } from '@/lib/tasks/taskStatuses';
import { TASK_TYPE_DEFINITIONS, getTaskTypeShortLabel } from '@/lib/tasks/taskTypes';
import { formatTaskDateShort } from '@/lib/tasks/taskDates';
import {
  START_MUST_BE_ON_OR_BEFORE_DUE,
  violatesStartBeforeDue,
} from '@/lib/tasks/taskScheduleDates';
import { Skeleton } from '@/components/ui/skeleton';
import { TaskAPI } from '@/lib/api/taskApi';
import { useTaskStore } from '@/lib/taskStore';
import { useBuildUrl } from '@/lib/buildUrl';
import { ProjectAPI, type ProjectMemberData } from '@/lib/api/projectApi';
import BulkActionToolbar, { type BulkField } from './BulkActionToolbar';
import TaskListRowContextMenu, {
  type TaskListRowContextMenuState,
} from '@/components/tasks/TaskListRowContextMenu';
import LinearBulkOutputModal from '@/components/linear/LinearBulkOutputModal';
import { TaskFilterPanel } from './TaskFilterPanel';
import TaskDrawer from './TaskDrawer';
import QuickTaskCreate from './QuickTaskCreate';
import type { TaskTag } from '@/types/task';

function resolveTaskSlug(task: Pick<TaskData, 'slug'>): string | null {
  const slug = task.slug?.trim();
  return slug || null;
}

function pickTaskFields(
  task: Partial<TaskData>,
  fields: Array<keyof TaskData>,
): Partial<TaskData> {
  return Object.fromEntries(
    fields
      .filter((field) => field in task)
      .map((field) => [field, task[field]])
  ) as Partial<TaskData>;
}

interface ListViewProps {
  tasks: TaskData[];
  loading: boolean;
  error: string | null;
  projectId?: Id | null;
  /** Opens the shared Linear import modal owned by the tasks page. */
  onOpenLinearImport?: () => void;
  /** After a successful bulk push to Linear, refresh task list from parent. */
  onLinearBulkSynced?: () => void | Promise<void>;
  /** Refresh the task list from the parent (e.g. after a drawer mutation). */
  onRefresh?: () => void;
  /** When set, opens the quick-view drawer for this task slug (path or query, depending on list route). */
  initialDrawerTaskSlug?: string | null;
  /** List route base, e.g. `/tasks` or `/projects/<slug>/tasks`. Controls drawer URL shape. */
  listBasePath?: string;
}

const TABLE_COLUMN_WIDTHS = {
  icon: 'w-10',
  select: 'w-10',
  type: 'w-[76px]',
  status: 'w-[118px]',
  owner: 'w-[116px]',
  approver: 'w-[116px]',
  start: 'w-[104px]',
  due: 'w-[104px]',
} as const;

const APPROVER_QUICK_EDITABLE_STATUSES: TaskData['status'][] = ['DRAFT', 'SUBMITTED'];
const STATUS_DOT_CLASS: Record<string, string> = {
  DRAFT: 'bg-gray-400',
  SUBMITTED: 'bg-sky-500',
  UNDER_REVIEW: 'bg-amber-500',
  APPROVED: 'bg-emerald-500',
  REJECTED: 'bg-rose-500',
  LOCKED: 'bg-violet-500',
  CANCELLED: 'bg-gray-400',
};

const LIST_PAGE_SIZE = 10;

type SortKey = 'id_desc' | 'id_asc' | 'due_asc' | 'due_desc' | 'priority' | 'status' | 'owner_asc' | 'name_asc';
type GroupBy = 'none' | 'status' | 'priority' | 'type' | 'owner';

type SavedView = {
  id: string;
  name: string;
  filters: TaskListFilters;
  sortKey: SortKey;
  groupBy: GroupBy;
  search: string;
};

const savedViewsKey = (projectId: Id | null) => `tasks-saved-views-${projectId ?? 'all'}`;
const preViewKey = (projectId: Id | null) => `tasks-pre-view-state-${projectId ?? 'all'}`;

const backNavFlag = (projectId: Id | null) => `tasks-back-nav-${projectId ?? 'all'}`;
// Unique ID for this page load — resets on every reload, stays constant within a session.
// Stored alongside the back-nav flag so we can tell if the flag was written in this
// page load (client-side nav) or a previous one (stale after reload).
const PAGE_SESSION_ID = `${Date.now()}-${Math.random()}`;

const PRIORITY_SORT_ORDER: Record<string, number> = {
  HIGHEST: 0, HIGH: 1, MEDIUM: 2, LOW: 3, LOWEST: 4,
};
const STATUS_SORT_ORDER: Record<string, number> = {
  DRAFT: 0, SUBMITTED: 1, UNDER_REVIEW: 2, APPROVED: 3, REJECTED: 4, LOCKED: 5, CANCELLED: 6,
};

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'id_desc', label: 'Newest first' },
  { value: 'id_asc', label: 'Oldest first' },
  { value: 'due_asc', label: 'Due date ↑' },
  { value: 'due_desc', label: 'Due date ↓' },
  { value: 'priority', label: 'Priority' },
  { value: 'status', label: 'Status' },
  { value: 'owner_asc', label: 'Owner A→Z' },
  { value: 'name_asc',  label: 'Task name A→Z' },
];

function highlight(text: string, query: string): React.ReactNode {
  const str = text || '';
  if (!query.trim()) return str;
  const q = query.trim().toLowerCase();
  const idx = str.toLowerCase().indexOf(q);
  if (idx < 0) return str;
  return (
    <>
      {str.slice(0, idx)}
      <mark className="rounded-[2px] bg-yellow-100 px-0 text-yellow-900">{str.slice(idx, idx + q.length)}</mark>
      {str.slice(idx + q.length)}
    </>
  );
}

export default function ListView({
  tasks,
  loading,
  error,
  projectId = null,
  onOpenLinearImport,
  onLinearBulkSynced,
  onRefresh,
  initialDrawerTaskSlug = null,
  listBasePath = '/tasks',
}: ListViewProps) {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const searchParams = useSearchParams();
  const projectKey = projectId != null && projectId !== '' ? String(projectId) : null;
  const drawerUsesNestedPath = listBasePath.startsWith('/projects/');
  const removeTask = useTaskStore((s) => s.removeTask);
  const updateTask = useTaskStore((s) => s.updateTask);
  const sessionKey = `tasks-list-state-${projectId ?? 'all'}`;
  const [search, setSearch] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [bulkMode, setBulkMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [linearBulkOpen, setLinearBulkOpen] = useState(false);
  const [members, setMembers] = useState<ProjectMemberData[]>([]);
  const [tagCatalog, setTagCatalog] = useState<TaskTag[]>([]);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [editingSummaryId, setEditingSummaryId] = useState<number | null>(null);
  const [summaryDraft, setSummaryDraft] = useState('');
  const [openDuePickerTaskId, setOpenDuePickerTaskId] = useState<number | null>(null);
  const [dueDraftByTaskId, setDueDraftByTaskId] = useState<Record<number, string>>({});
  const [openStartPickerTaskId, setOpenStartPickerTaskId] = useState<number | null>(null);
  const [startDraftByTaskId, setStartDraftByTaskId] = useState<Record<number, string>>({});
  const [openPriorityTaskId, setOpenPriorityTaskId] = useState<number | null>(null);
  const [openStatusTaskId, setOpenStatusTaskId] = useState<number | null>(null);
  const [openOwnerTaskId, setOpenOwnerTaskId] = useState<number | null>(null);
  const [openApproverTaskId, setOpenApproverTaskId] = useState<number | null>(null);
  const [savingIds, setSavingIds] = useState<number[]>([]);
  const savingCountByTaskId = useRef(
    new Map<number, number>()
  );
  const [pinBusyIds, setPinBusyIds] = useState<number[]>([]);
  const [bulkFailures, setBulkFailures] = useState<TaskBulkFailureItem[]>([]);
  const [recentlyUpdatedIds, setRecentlyUpdatedIds] = useState<number[]>([]);
  const [truncatedSummaryIds, setTruncatedSummaryIds] = useState<number[]>([]);
  const updateTaskInStore = useTaskStore((s) => s.updateTask);
  const beginTaskOperation = useTaskStore(
    (s) => s.beginTaskOperation
  );
  const resolveTaskFromServer = useTaskStore(
    (s) => s.resolveTaskFromServer
  );
  const rollbackTaskOperation = useTaskStore(
    (s) => s.rollbackTaskOperation
  );
  const updateTasksBulkInStore = useTaskStore((s) => s.updateTasksBulk);

  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [subtasksMap, setSubtasksMap] = useState<Map<number, TaskData[]>>(new Map());
  const [loadingSubtaskIds, setLoadingSubtaskIds] = useState<Set<number>>(new Set());

  const toggleSubtasks = async (e: MouseEvent, taskId: number) => {
    e.stopPropagation();
    if (expandedIds.has(taskId)) {
      setExpandedIds((prev) => { const next = new Set(prev); next.delete(taskId); return next; });
      return;
    }
    if (!subtasksMap.has(taskId)) {
      setLoadingSubtaskIds((prev) => new Set(prev).add(taskId));
      try {
        const subs = await TaskAPI.getSubtasks(taskId);
        setSubtasksMap((prev) => new Map(prev).set(taskId, subs));
      } catch {
        setSubtasksMap((prev) => new Map(prev).set(taskId, []));
      } finally {
        setLoadingSubtaskIds((prev) => { const next = new Set(prev); next.delete(taskId); return next; });
      }
    }
    setExpandedIds((prev) => new Set(prev).add(taskId));
  };

  const [filters, setFilters] = useState<TaskListFilters>({});
  const [sortKey, setSortKey] = useState<SortKey>('id_desc');
  const [groupBy, setGroupBy] = useState<GroupBy>('none');

  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [viewsOpen, setViewsOpen] = useState(false);
  const [activeViewId, setActiveViewId] = useState<string | null>(null);
  const [savingViewName, setSavingViewName] = useState('');
  const [showSaveInput, setShowSaveInput] = useState(false);
  const viewsRef = useRef<HTMLDivElement>(null);

  // Load per-project saved views when projectId is known.
  // Note: we do NOT reset activeViewId here. The restore effect sets it from sessionStorage
  // on back-navigation, and the deactivate-on-drift effect clears it if the view no longer
  // exists in the newly loaded views (e.g. after switching projects).
  useEffect(() => {
    if (projectId === null) return;
    try {
      const raw = localStorage.getItem(savedViewsKey(projectId));
      setSavedViews(raw ? (JSON.parse(raw) as SavedView[]) : []);
    } catch { /* ignore */ }
  }, [projectId]);

  // Fix #1 — sync saved views across tabs via storage event.
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === savedViewsKey(projectId)) {
        try {
          const views = e.newValue ? (JSON.parse(e.newValue) as SavedView[]) : [];
          setSavedViews(views);
        } catch { /* ignore */ }
      }
    };
    window.addEventListener('storage', handler);
    return () => window.removeEventListener('storage', handler);
  }, [projectId]);

  // Close views dropdown on outside click.
  useEffect(() => {
    const handler = (e: Event) => {
      if (viewsRef.current && !viewsRef.current.contains(e.target as Node)) {
        setViewsOpen(false);
        setShowSaveInput(false);
        setSavingViewName('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Fix #2 — deactivate when state drifts; also deactivate if the view was deleted in another tab.
  useEffect(() => {
    if (!activeViewId) return;
    const view = savedViews.find((v) => v.id === activeViewId);
    if (!view) {
      // View was deleted (e.g. from another tab) — deactivate silently.
      setActiveViewId(null);
      return;
    }
    const matches =
      JSON.stringify(view.filters) === JSON.stringify(filters) &&
      view.sortKey === sortKey &&
      view.groupBy === groupBy &&
      view.search === search;
    if (!matches) setActiveViewId(null);
  }, [filters, sortKey, groupBy, search, savedViews]); // eslint-disable-line react-hooks/exhaustive-deps

  const persistViews = (views: SavedView[]) => {
    setSavedViews(views);
    try { localStorage.setItem(savedViewsKey(projectId), JSON.stringify(views)); } catch { /* ignore */ }
  };

  const savePreViewState = () => {
    try {
      sessionStorage.setItem(
        preViewKey(projectId),
        JSON.stringify({ filters, sortKey, groupBy, search }),
      );
    } catch { /* ignore */ }
  };

  const restorePreViewState = () => {
    try {
      const raw = sessionStorage.getItem(preViewKey(projectId));
      if (!raw) return;
      const s = JSON.parse(raw) as { filters: TaskListFilters; sortKey: SortKey; groupBy: GroupBy; search: string };
      setFilters(s.filters ?? {});
      setSortKey(s.sortKey ?? 'id_desc');
      setGroupBy(s.groupBy ?? 'none');
      setSearch(s.search ?? '');
    } catch { /* ignore */ }
  };

  const confirmSaveView = () => {
    const name = savingViewName.trim();
    if (!name) return;
    const view: SavedView = {
      id: `${Date.now()}`,
      name,
      filters,
      sortKey,
      groupBy,
      search,
    };
    persistViews([...savedViews, view]);
    setActiveViewId(view.id);
    setSavingViewName('');
    setShowSaveInput(false);
    setViewsOpen(false);
    toast.success(`View "${name}" saved`);
  };

  const applyView = (view: SavedView) => {
    if (!activeViewId) savePreViewState();
    setFilters(view.filters);
    setSortKey(view.sortKey);
    setGroupBy(view.groupBy);
    setSearch(view.search);
    setActiveViewId(view.id);
    setViewsOpen(false);
    setCurrentPage(1);
  };

  // Fix #4 — update an existing saved view with the current state.
  const updateView = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const updated = savedViews.map((v) =>
      v.id === id ? { ...v, filters, sortKey, groupBy, search } : v,
    );
    persistViews(updated);
    setActiveViewId(id);
    toast.success('View updated');
  };

  const deleteView = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    persistViews(savedViews.filter((v) => v.id !== id));
    if (activeViewId === id) {
      setActiveViewId(null);
      restorePreViewState();
      sessionStorage.removeItem(preViewKey(projectId));
    }
  };

  // Restore filter/sort state from sessionStorage once projectId is known.
  // Using a ref so this only runs once per project (not on every projectId re-render).
  // On unmount (navigating away), set a back-nav flag so the next mount knows to restore.
  // This flag lives in sessionStorage so it survives the component re-creating, but is
  // absent on a true page reload (restore would not happen).
  useEffect(() => {
    if (!projectId) return;
    return () => {
      try { sessionStorage.setItem(backNavFlag(projectId), PAGE_SESSION_ID); } catch { /* ignore */ }
    };
  }, [projectId]);

  const restoredForProject = useRef<Id | null | 'all'>(undefined as any);
  useEffect(() => {
    const key = projectId ?? 'all';
    if (restoredForProject.current === key) return;
    restoredForProject.current = key;

    const flagValue = sessionStorage.getItem(backNavFlag(projectId));
    // Always clear the flag — whether we restore or not.
    sessionStorage.removeItem(backNavFlag(projectId));

    // Only restore if the flag was written in THIS page load (same PAGE_SESSION_ID).
    // A stale flag from a previous session (after a reload) won't match.
    if (flagValue !== PAGE_SESSION_ID) {
      // No flag → fresh page load. Clear any stale state and use defaults.
      sessionStorage.removeItem(`tasks-list-state-${key}`);
      sessionStorage.removeItem(preViewKey(projectId ?? null));
      return;
    }

    try {
      const saved = sessionStorage.getItem(`tasks-list-state-${key}`);
      if (!saved) return;
      const parsed = JSON.parse(saved) as { filters?: TaskListFilters; sortKey?: SortKey; groupBy?: GroupBy; search?: string; activeViewId?: string };
      if (parsed.filters) setFilters(parsed.filters);
      if (parsed.sortKey) setSortKey(parsed.sortKey);
      if (parsed.groupBy) setGroupBy(parsed.groupBy);
      if (parsed.search !== undefined) setSearch(parsed.search);
      if (parsed.activeViewId) {
        setActiveViewId(parsed.activeViewId);
        // Do NOT remove preViewKey here — it must survive back-navigation so that
        // "Clear active view" can restore the state the user had before applying the view.
      }
    } catch { /* ignore */ }
  }, [projectId]);

  useEffect(() => {
    try {
      sessionStorage.setItem(sessionKey, JSON.stringify({ filters, sortKey, groupBy, search, activeViewId }));
    } catch { /* ignore quota errors */ }
  }, [sessionKey, filters, sortKey, groupBy, search, activeViewId]);

  const parseApiError = (err: unknown, fallback: string) => {
    const data = (err as any)?.response?.data;
    if (typeof data?.detail === 'string') return data.detail;
    if (typeof data?.error === 'string') return data.error;
    if (Array.isArray(data?.non_field_errors) && data.non_field_errors[0]) {
      return String(data.non_field_errors[0]);
    }
    return fallback;
  };

  useEffect(() => {
    setSelectedIds((prev) => prev.filter((id) => tasks.some((t) => t.id === id)));
  }, [tasks]);

  useEffect(() => {
    if (!bulkMode) {
      setSelectedIds([]);
    }
  }, [bulkMode]);

  useEffect(() => {
    const closeMenus = () => {
      setOpenDuePickerTaskId(null);
      setOpenStartPickerTaskId(null);
      setOpenPriorityTaskId(null);
      setOpenStatusTaskId(null);
      setOpenOwnerTaskId(null);
      setOpenApproverTaskId(null);
    };
    window.addEventListener('click', closeMenus);
    return () => window.removeEventListener('click', closeMenus);
  }, []);

  useEffect(() => {
    let mounted = true;
    if (!projectId) {
      setMembers([]);
      return;
    }
    ProjectAPI.getAllProjectMembers(projectId)
      .then((rows) => {
        if (!mounted) return;
        setMembers(rows.filter((m) => m.is_active));
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, [projectId]);

  useEffect(() => {
    let mounted = true;
    if (!projectId) {
      setTagCatalog([]);
      return;
    }
    TaskAPI.getTagCatalog(projectId)
      .then((rows) => {
        if (mounted) setTagCatalog(rows);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, [projectId]);
  const [drawerRefreshKey, setDrawerRefreshKey] = useState(0);
  const [drawerTaskSlug, setDrawerTaskSlug] = useState<string | null>(() => {
    const fromPath = initialDrawerTaskSlug?.trim();
    return fromPath || null;
  });
  const openDrawer = useCallback((slug: string | null) => {
    const normalized = slug?.trim() || null;
    setDrawerTaskSlug(normalized);
    const params = new URLSearchParams(searchParams?.toString() ?? '');
    params.delete('drawerTaskId');
    params.delete('project_id');

    if (drawerUsesNestedPath) {
      if (!projectKey) return;
      params.delete('drawerTask');
      const qs = params.toString();
      const path = normalized
        ? `${listBasePath}/${encodeURIComponent(normalized)}`
        : listBasePath;
      router.replace(qs ? `${path}?${qs}` : path, { scroll: false });
      return;
    }

    if (normalized) params.set('drawerTask', normalized);
    else params.delete('drawerTask');
    const qs = params.toString();
    router.replace(qs ? `${listBasePath}?${qs}` : listBasePath, { scroll: false });
  }, [drawerUsesNestedPath, listBasePath, projectKey, router, searchParams]);
  const legacyDrawerResolvedRef = useRef(false);

  useEffect(() => {
    const fromPath = initialDrawerTaskSlug?.trim() || null;
    setDrawerTaskSlug(fromPath);
  }, [initialDrawerTaskSlug]);

  useEffect(() => {
    if (!drawerUsesNestedPath || !projectKey || initialDrawerTaskSlug?.trim()) return;
    const legacyQuerySlug = searchParams?.get('drawerTask')?.trim();
    if (legacyQuerySlug) {
      openDrawer(legacyQuerySlug);
    }
  }, [drawerUsesNestedPath, initialDrawerTaskSlug, openDrawer, projectKey, searchParams]);

  const [quickCreateOpen, setQuickCreateOpen] = useState(false);
  const [rowMenu, setRowMenu] = useState<TaskListRowContextMenuState>(null);
  const [menuMembers, setMenuMembers] = useState<ProjectMemberData[]>([]);
  const [menuMembersLoading, setMenuMembersLoading] = useState(false);
  const membersByProjectIdRef = useRef<Map<number | string, ProjectMemberData[]>>(new Map());
  const pendingMemberFetchesRef = useRef<Map<number | string, Promise<ProjectMemberData[]>>>(new Map());

  useEffect(() => {
    if (!rowMenu) {
      setMenuMembers([]);
      setMenuMembersLoading(false);
      return;
    }
    const pid = rowMenu.task.project?.slug ?? rowMenu.task.project_id ?? rowMenu.task.project?.id ?? null;
    if (pid == null) {
      setMenuMembers([]);
      setMenuMembersLoading(false);
      return;
    }

    const cached = membersByProjectIdRef.current.get(pid);
    if (cached) {
      setMenuMembers(cached);
      setMenuMembersLoading(false);
      return;
    }

    let cancelled = false;
    setMenuMembers([]);
    setMenuMembersLoading(true);

    const existing = pendingMemberFetchesRef.current.get(pid);
    const promise =
      existing ??
      ProjectAPI.getProjectMembers(pid)
        .then((rows) => {
          membersByProjectIdRef.current.set(pid, rows);
          pendingMemberFetchesRef.current.delete(pid);
          return rows;
        })
        .catch(() => {
          pendingMemberFetchesRef.current.delete(pid);
          return [] as ProjectMemberData[];
        });
    if (!existing) {
      pendingMemberFetchesRef.current.set(pid, promise);
    }

    void promise.then((rows) => {
      if (!cancelled) {
        setMenuMembers(rows);
        setMenuMembersLoading(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [rowMenu]);

  const openRowMenu = useCallback((e: MouseEvent, task: TaskData) => {
    e.preventDefault();
    e.stopPropagation();
    if (task.id == null) return;
    let x = e.clientX;
    let y = e.clientY;
    setRowMenu({ task, x, y });
  }, []);

  const closeRowMenu = useCallback(() => setRowMenu(null), []);

  const handleOpenDetail = useCallback(
    (taskKey: number | string) => {
      router.push(buildUrl(`/tasks/${taskKey}`));
    },
    [router, buildUrl]
  );

  const handleTaskDeleted = useCallback(
    (taskId: number) => {
      removeTask(taskId);
    },
    [removeTask]
  );

  const handleTaskPatched = useCallback(
    (taskId: number, data: Partial<TaskData>) => {
      updateTask(taskId, data);
    },
    [updateTask]
  );

  const visible = useMemo(() => {
    let result = tasks;
    const q = search.trim().toLowerCase();
    if (q) {
      result = result.filter((t) =>
        `${t.summary ?? ''} ${(t.tags ?? []).map((tag) => tag.name).join(' ')} ${t.type ?? ''} ${t.owner?.username ?? ''} ${t.current_approver?.username ?? ''}`
          .toLowerCase()
          .includes(q)
      );
    }
    // Panel filters
    if (filters.status) {
      const vals = Array.isArray(filters.status) ? filters.status : [filters.status];
      result = result.filter((t) => t.status && vals.includes(t.status));
    }
    if (filters.priority) {
      const vals = Array.isArray(filters.priority) ? filters.priority : [filters.priority];
      result = result.filter((t) => t.priority && vals.includes(t.priority));
    }
    if (filters.type) {
      const vals = Array.isArray(filters.type) ? filters.type : [filters.type];
      result = result.filter((t) => t.type && vals.includes(t.type));
    }
    if (filters.owner_id) {
      const ids = (Array.isArray(filters.owner_id) ? filters.owner_id : [filters.owner_id]) as number[];
      result = result.filter((t) => t.owner?.id != null && ids.includes(t.owner.id));
    }
    if (filters.current_approver_id) {
      const ids = (Array.isArray(filters.current_approver_id) ? filters.current_approver_id : [filters.current_approver_id]) as number[];
      result = result.filter((t) => t.current_approver?.id != null && ids.includes(t.current_approver.id));
    }
    if (filters.due_date_after) {
      result = result.filter((t) => t.due_date && t.due_date >= filters.due_date_after!);
    }
    if (filters.due_date_before) {
      result = result.filter((t) => t.due_date && t.due_date <= filters.due_date_before!);
    }
    if (filters.has_subtasks === true) {
      result = result.filter((t) => (t.subtask_count ?? 0) > 0);
    } else if (filters.has_subtasks === false) {
      result = result.filter((t) => (t.subtask_count ?? 0) === 0);
    }
    if (filters.tag_names?.length) {
      const required = new Set(filters.tag_names);
      result = result.filter((t) => (t.tags ?? []).some((tag) => required.has(tag.name)));
    }
    return result;
  }, [search, tasks, filters]);

  const sorted = useMemo(() => {
    const arr = [...visible];
    arr.sort((a, b) => {
      const pinDelta = Number(Boolean(b.is_pinned)) - Number(Boolean(a.is_pinned));
      if (pinDelta !== 0) return pinDelta;
      switch (sortKey) {
        case 'id_asc':  return (a.id ?? 0) - (b.id ?? 0);
        case 'id_desc': return (b.id ?? 0) - (a.id ?? 0);
        case 'due_asc':  return ((a.due_date ?? '9999') < (b.due_date ?? '9999') ? -1 : 1);
        case 'due_desc': return ((a.due_date ?? '9999') > (b.due_date ?? '9999') ? -1 : 1);
        case 'priority': return (PRIORITY_SORT_ORDER[a.priority ?? 'MEDIUM'] ?? 2) - (PRIORITY_SORT_ORDER[b.priority ?? 'MEDIUM'] ?? 2);
        case 'status':   return (STATUS_SORT_ORDER[a.status ?? 'DRAFT'] ?? 0) - (STATUS_SORT_ORDER[b.status ?? 'DRAFT'] ?? 0);
        case 'owner_asc': return (a.owner?.username ?? '').localeCompare(b.owner?.username ?? '');
        case 'name_asc':  return (a.summary ?? '').localeCompare(b.summary ?? '');
        default: return 0;
      }
    });
    return arr;
  }, [visible, sortKey]);

  const isGrouped = groupBy !== 'none';
  const totalPages = Math.max(1, Math.ceil(sorted.length / LIST_PAGE_SIZE));
  const safeCurrentPage = Math.min(Math.max(currentPage, 1), totalPages);
  const paginatedVisible = useMemo(() => {
    const start = (safeCurrentPage - 1) * LIST_PAGE_SIZE;
    return sorted.slice(start, start + LIST_PAGE_SIZE);
  }, [safeCurrentPage, sorted]);
  // Migrate legacy ?drawerTaskId=<numeric> once tasks are loaded (slug-only deep links).
  useEffect(() => {
    if (drawerTaskSlug || legacyDrawerResolvedRef.current || initialDrawerTaskSlug?.trim()) return;
    const legacyId = searchParams?.get('drawerTaskId');
    if (!legacyId) return;
    const n = Number(legacyId);
    if (!Number.isFinite(n) || n <= 0) {
      legacyDrawerResolvedRef.current = true;
      openDrawer(null);
      return;
    }
    if (loading) return;
    legacyDrawerResolvedRef.current = true;
    const match = paginatedVisible.find((t) => t.id === n);
    const slug = match?.slug?.trim();
    if (slug) openDrawer(slug);
    else openDrawer(null);
  }, [drawerTaskSlug, initialDrawerTaskSlug, loading, openDrawer, paginatedVisible, searchParams]);
  const pageStart = sorted.length === 0 ? 0 : (safeCurrentPage - 1) * LIST_PAGE_SIZE + 1;
  const pageEnd = Math.min(sorted.length, safeCurrentPage * LIST_PAGE_SIZE);

  // Build display rows with optional group headers
  type DisplayRow =
    | { kind: 'header'; key: string; label: string }
    | { kind: 'task'; task: TaskData };

  const displayRows = useMemo((): DisplayRow[] => {
    if (!isGrouped) return paginatedVisible.map((task) => ({ kind: 'task' as const, task }));
    const groups = new Map<string, { label: string; tasks: TaskData[] }>();
    paginatedVisible.forEach((task) => {
      let key = '';
      let label = '';
      if (groupBy === 'status') {
        key = `status-${task.status ?? 'DRAFT'}`;
        label = TASK_STATUS_BY_VALUE[task.status ?? 'DRAFT']?.label ?? (task.status ?? 'Draft');
      } else if (groupBy === 'priority') {
        key = `priority-${task.priority ?? 'MEDIUM'}`;
        label = TASK_PRIORITY_BY_VALUE[task.priority ?? 'MEDIUM']?.label ?? (task.priority ?? 'Medium');
      } else if (groupBy === 'type') {
        key = `type-${task.type ?? 'none'}`;
        label = TASK_TYPE_DEFINITIONS.find((t) => t.value === task.type)?.label ?? task.type ?? 'No type';
      } else if (groupBy === 'owner') {
        const ownerName = task.owner ? userDisplayName(task.owner) : null;
        key = `owner-${ownerName ?? 'unassigned'}`;
        label = ownerName ?? 'Unassigned';
      }
      if (!groups.has(key)) groups.set(key, { label, tasks: [] });
      groups.get(key)!.tasks.push(task);
    });
    const rows: DisplayRow[] = [];
    groups.forEach(({ label, tasks: groupTasks }, key) => {
      rows.push({ kind: 'header', key, label: `${label} (${groupTasks.length})` });
      groupTasks.forEach((task) => rows.push({ kind: 'task', task }));
    });
    return rows;
  }, [isGrouped, groupBy, paginatedVisible]);

  useEffect(() => {
    setCurrentPage(1);
  }, [search]);

  useEffect(() => {
    setCurrentPage(1);
  }, [filters, sortKey, groupBy]);

  useEffect(() => {
    setCurrentPage((page) => Math.min(Math.max(page, 1), totalPages));
  }, [totalPages]);

  useEffect(() => {
    if (loading) {
      setTruncatedSummaryIds([]);
      return;
    }

    const measureTruncation = () => {
      const nodes = document.querySelectorAll<HTMLElement>('[data-summary-id]');
      const next: number[] = [];
      nodes.forEach((node) => {
        const id = Number(node.dataset.summaryId);
        if (!Number.isFinite(id)) return;
        const isTruncated =
          node.scrollHeight > node.clientHeight + 1 || node.scrollWidth > node.clientWidth + 1;
        if (isTruncated) next.push(id);
      });
      setTruncatedSummaryIds(next);
    };

    const frame = window.requestAnimationFrame(measureTruncation);
    window.addEventListener('resize', measureTruncation);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', measureTruncation);
    };
  }, [loading, paginatedVisible]);

  const memberOptions = useMemo(() => {
    if (members.length > 0) {
      return members.map((m) => ({
        id: m.user.id,
        label:
          m.user.name ||
          (m.user.username && m.user.username.length > 1 ? m.user.username : undefined) ||
          m.user.email ||
          m.user.username ||
          `User #${m.user.id}`,
      }));
    }
    const unique = new Map<number, string>();
    tasks.forEach((task) => {
      if (task.owner?.id) unique.set(task.owner.id, userDisplayName(task.owner));
      if (task.current_approver?.id) unique.set(task.current_approver.id, userDisplayName(task.current_approver));
    });
    return Array.from(unique.entries()).map(([id, label]) => ({ id, label }));
  }, [members, tasks]);

  const filterMemberOptions = useMemo(
    () => memberOptions.map((m) => ({ id: m.id, name: m.label })),
    [memberOptions]
  );

  const tagOptions = useMemo(() => {
    const seen = new Map<string, string>(); // name -> color
    for (const tag of tagCatalog) {
      if (tag.name) seen.set(tag.name, tag.color);
    }
    // Tasks loaded into the list may have tags created since the catalog was fetched;
    // surface them too so the filter stays in sync without an extra refetch.
    for (const t of tasks) {
      for (const tag of t.tags ?? []) {
        if (tag.name && !seen.has(tag.name)) seen.set(tag.name, tag.color);
      }
    }
    return [...seen.entries()].map(([name, color]) => ({ name, color })).sort((a, b) => a.name.localeCompare(b.name));
  }, [tasks, tagCatalog]);

  const toggleSelection = (taskId: number, checked: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (checked) next.add(taskId);
      else next.delete(taskId);
      return Array.from(next);
    });
  };

  const openLinearOutput = () => {
    if (selectedIds.length === 0) {
      toast('Select one or more tasks with the checkboxes first.', { duration: 5000 });
      return;
    }
    setLinearBulkOpen(true);
  };

  const toggleSelectAll = (checked: boolean) => {
    if (!checked) {
      const pageIds = new Set(paginatedVisible.map((t) => t.id).filter(Boolean) as number[]);
      setSelectedIds((prev) => prev.filter((id) => !pageIds.has(id)));
      return;
    }
    setSelectedIds((prev) => {
      const next = new Set(prev);
      paginatedVisible.forEach((task) => {
        if (task.id) next.add(task.id);
      });
      return Array.from(next);
    });
  };

  const setTaskSaving = (
    taskId: number,
    saving: boolean,
  ) => {
    const currentCount =
      savingCountByTaskId.current.get(taskId) ?? 0;

    const nextCount = saving
      ? currentCount + 1
      : Math.max(0, currentCount - 1);

    if (nextCount === 0) {
      savingCountByTaskId.current.delete(taskId);
    } else {
      savingCountByTaskId.current.set(
        taskId,
        nextCount,
      );
    }

    setSavingIds((previousIds) => {
      const nextIds = new Set(previousIds);

      if (nextCount > 0) {
        nextIds.add(taskId);
      } else {
        nextIds.delete(taskId);
      }

      return Array.from(nextIds);
    });
  };

  const markRecentlyUpdated = (taskIds: number[]) => {
    setRecentlyUpdatedIds((prev) => {
      const next = new Set(prev);
      taskIds.forEach((id) => next.add(id));
      return Array.from(next);
    });
    window.setTimeout(() => {
      setRecentlyUpdatedIds((prev) => prev.filter((id) => !taskIds.includes(id)));
    }, 900);
  };

  const setTaskPinBusy = (taskId: number, busy: boolean) => {
    setPinBusyIds((prev) => {
      const next = new Set(prev);
      if (busy) next.add(taskId);
      else next.delete(taskId);
      return Array.from(next);
    });
  };

  const toggleTaskPin = async (task: TaskData) => {
    if (!task.id || pinBusyIds.includes(task.id)) return;
    const nextPinned = !task.is_pinned;
    const previous = task.is_pinned;
    updateTaskInStore(task.id, { is_pinned: nextPinned });
    setTaskPinBusy(task.id, true);
    try {
      const res = nextPinned
        ? await TaskAPI.pinTask(task.slug ?? task.id)
        : await TaskAPI.unpinTask(task.slug ?? task.id);
      const data = res.data as TaskData;
      updateTaskInStore(task.id, { is_pinned: data.is_pinned ?? nextPinned });
      toast.success(nextPinned ? 'Task pinned' : 'Task unpinned');
    } catch (err) {
      updateTaskInStore(task.id, { is_pinned: previous });
      toast.error(parseApiError(err, nextPinned ? 'Failed to pin task' : 'Failed to unpin task'));
    } finally {
      setTaskPinBusy(task.id, false);
    }
  };

  const updateSingleTask = async (
    task: TaskData,
    patch: Partial<TaskData>,
    requestData: Record<string, unknown>,
    fallbackError: string
  ): Promise<boolean> => {
    if (!task.id) return false;

    const patchFields = Object.keys(
      patch
    ) as Array<keyof TaskData>;

    const operationScope = patchFields
      .map(String)
      .sort()
      .join(',');

    const operationId =
      globalThis.crypto.randomUUID();

    const currentStoreTask =
      useTaskStore
        .getState()
        .tasks
        .find((candidate) => candidate.id === task.id)
      ?? task;

    const previousPatch = pickTaskFields(
      currentStoreTask,
      patchFields,
    );

    beginTaskOperation(
      task.id,
      operationScope,
      operationId,
    );

    updateTaskInStore(task.id, patch);
    setTaskSaving(task.id, true);

    try {
      const response = await TaskAPI.updateTask(
        task.slug ?? task.id,
        requestData as Partial<TaskData>,
        operationId,
      );

      const serverTask = response.data as TaskData & {
        operation_id?: string;
      };

      if (serverTask.operation_id !== operationId) {
        throw new Error(
          'Task update response did not return the matching operation ID.'
        );
      }

      const serverPatch = pickTaskFields(
        serverTask,
        patchFields,
      );

      const applied = resolveTaskFromServer(
        task.id,
        serverPatch,
        operationScope,
        operationId,
      );

      if (applied) {
        markRecentlyUpdated([task.id]);

        if (
          resolveTaskSlug(task) === drawerTaskSlug
        ) {
          setDrawerRefreshKey((key) => key + 1);
        }
      }

      return applied;
    } catch (error) {
      const rollbackApplied = rollbackTaskOperation(
        task.id,
        previousPatch,
        operationScope,
        operationId,
      );

      if (rollbackApplied) {
        toast.error(
          parseApiError(error, fallbackError)
        );
      }

      return false;
    } finally {
      setTaskSaving(task.id, false);
    }
  };

  const commitSummaryEdit = async (task: TaskData) => {
    const next = summaryDraft.trim();
    setEditingSummaryId(null);
    if (!task.id || !next || next === task.summary) return;
    const ok = await updateSingleTask(
      task,
      { summary: next },
      { summary: next },
      'Failed to update summary'
    );
    if (ok) toast.success('Summary updated');
  };

  const updateTaskStatus = async (task: TaskData, nextStatus: string) => {
    if (!task.id || task.status === nextStatus) return;
    const previous = task.status;
    updateTaskInStore(task.id, { status: nextStatus as TaskData['status'] });
    setTaskSaving(task.id, true);
    try {
      await TaskAPI.bulkAction({
        task_ids: [task.id],
        status: nextStatus as TaskData['status'],
      });
      toast.success('Status updated');
    } catch (err) {
      updateTaskInStore(task.id, { status: previous });
      toast.error(parseApiError(err, 'Failed to update status'));
    } finally {
      setTaskSaving(task.id, false);
    }
  };

  const applyBulkField = async (field: BulkField, value: string) => {
    if (selectedIds.length === 0) return;
    setBulkBusy(true);
    setBulkFailures([]);
    const payload: Record<string, unknown> = { task_ids: selectedIds };

    if (field === 'owner_id' || field === 'current_approver_id') {
      payload[field] = Number(value);
    } else {
      payload[field] = value;
    }

    try {
      const response = await TaskAPI.bulkAction(payload as any);
      const patched: Partial<TaskData> = {};
      if (field === 'status') patched.status = value as TaskData['status'];
      if (field === 'due_date') patched.due_date = value;
      if (field === 'owner_id') {
        const owner = memberOptions.find((m) => m.id === Number(value));
        patched.owner_id = Number(value);
        patched.owner = owner
          ? { id: owner.id, username: owner.label, email: '' }
          : undefined;
      }
      if (field === 'current_approver_id') {
        const approver = memberOptions.find((m) => m.id === Number(value));
        patched.current_approver_id = Number(value);
        patched.current_approver = approver
          ? { id: approver.id, username: approver.label, email: '' }
          : undefined;
      }
      if (field === 'priority') patched.priority = value;
      if (field === 'start_date') patched.start_date = value;
      if (field === 'planned_start_date') patched.planned_start_date = value;
      updateTasksBulkInStore(selectedIds, patched);
      markRecentlyUpdated(selectedIds);
      setSelectedIds([]);
      toast.success(response.detail || `Updated ${response.result.updated_count} task(s)`);
    } catch (err) {
      const data = (err as any)?.response?.data;
      const failed = data?.result?.failed;
      if (Array.isArray(failed) && failed.length > 0) {
        setBulkFailures(failed as TaskBulkFailureItem[]);
        toast.error(`${failed.length} task(s) failed. ${failed[0].reason}`);
      } else {
        toast.error(parseApiError(err, 'Bulk update failed'));
      }
    } finally {
      setBulkBusy(false);
    }
  };

  const getAllowedStatusOptions = (status: TaskData['status'] | undefined): string[] => {
    const current = status ?? 'DRAFT';
    const map: Record<string, string[]> = {
      DRAFT: ['SUBMITTED'],
      SUBMITTED: ['UNDER_REVIEW', 'CANCELLED'],
      UNDER_REVIEW: ['APPROVED', 'REJECTED', 'CANCELLED'],
      APPROVED: ['LOCKED', 'CANCELLED'],
      LOCKED: ['APPROVED'],
      REJECTED: ['DRAFT'],
      CANCELLED: ['DRAFT'],
    };
    return [current, ...(map[current] ?? [])];
  };

  const getApproverDisabledReason = (status: TaskData['status'] | undefined) => {
    const current = status ?? 'DRAFT';
    if (APPROVER_QUICK_EDITABLE_STATUSES.includes(current)) return null;
    return 'Approver can only be changed in DRAFT or SUBMITTED status.';
  };

  const bulkAllowedStatusOptions = useMemo(() => {
    if (!bulkMode || selectedIds.length === 0) return TASK_STATUS_OPTIONS;
    const selectedTasks = tasks.filter((task) => task.id && selectedIds.includes(task.id));
    if (selectedTasks.length === 0) return [];

    const optionSets = selectedTasks.map((task) => new Set(getAllowedStatusOptions(task.status)));
    const [first, ...rest] = optionSets;
    const intersection = Array.from(first).filter((status) =>
      rest.every((set) => set.has(status))
    );
    return intersection;
  }, [bulkMode, selectedIds, tasks]);

  const bulkStatusDisabledReason = useMemo(() => {
    if (!bulkMode || selectedIds.length === 0) return null;
    if (bulkAllowedStatusOptions.length > 0) return null;
    return 'Selected tasks do not share a valid status transition. Refine your selection.';
  }, [bulkAllowedStatusOptions.length, bulkMode, selectedIds.length]);

  const openDuePicker = (task: TaskData) => {
    if (!task.id) return;
    setOpenPriorityTaskId(null);
    setOpenStartPickerTaskId(null);
    setOpenDuePickerTaskId(task.id);
    setDueDraftByTaskId((prev) => ({
      ...prev,
      [task.id as number]: task.due_date ?? '',
    }));
  };

  const openStartPicker = (task: TaskData) => {
    if (!task.id) return;
    setOpenPriorityTaskId(null);
    setOpenDuePickerTaskId(null);
    setOpenStartPickerTaskId(task.id);
    setStartDraftByTaskId((prev) => ({
      ...prev,
      [task.id as number]: task.start_date ?? '',
    }));
  };

  const commitDueDate = async (task: TaskData, nextValue: string | null) => {
    const nextDue = nextValue || undefined;
    if (violatesStartBeforeDue(task.start_date ?? null, nextDue ?? null)) {
      toast.error(START_MUST_BE_ON_OR_BEFORE_DUE);
      return;
    }
    const ok = await updateSingleTask(
      task,
      { due_date: nextDue },
      { due_date: nextValue },
      'Failed to update due date'
    );
    if (ok) {
      toast.success(nextValue ? 'Due date updated' : 'Due date cleared');
      setOpenDuePickerTaskId(null);
    }
  };

  const commitStartDate = async (task: TaskData, nextValue: string | null) => {
    const nextStart = nextValue || null;
    if (violatesStartBeforeDue(nextStart, task.due_date ?? null)) {
      toast.error(START_MUST_BE_ON_OR_BEFORE_DUE);
      return;
    }
    const ok = await updateSingleTask(
      task,
      { start_date: nextStart },
      { start_date: nextValue },
      'Failed to update start date'
    );
    if (ok) {
      toast.success(nextValue ? 'Start date updated' : 'Start date cleared');
      setOpenStartPickerTaskId(null);
    }
  };

  const updatePriority = async (task: TaskData, next: string) => {
    if (!task.id || task.priority === next) {
      setOpenPriorityTaskId(null);
      return;
    }
    const ok = await updateSingleTask(
      task,
      { priority: next },
      { priority: next },
      'Failed to update priority'
    );
    if (ok) {
      toast.success('Priority updated');
      setOpenPriorityTaskId(null);
    }
  };

  const getDueDateTone = (dueDate?: string | null) => {
    if (!dueDate) return 'text-gray-700';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const due = new Date(`${dueDate}T00:00:00`);
    const msPerDay = 24 * 60 * 60 * 1000;
    const diffDays = Math.floor((due.getTime() - today.getTime()) / msPerDay);
    if (diffDays < 0) return 'text-rose-600';
    if (diffDays <= 3) return 'text-amber-600';
    return 'text-gray-700';
  };

  const goToPage = (page: number) => {
    setCurrentPage(Math.min(Math.max(page, 1), totalPages));
  };

  const currentPageIds = useMemo(
    () => paginatedVisible.map((task) => task.id).filter(Boolean) as number[],
    [paginatedVisible]
  );
  const isCurrentPageSelected =
    currentPageIds.length > 0 && currentPageIds.every((id) => selectedIds.includes(id));

  return (
    <div className="min-w-0">
      <TaskListRowContextMenu
        state={rowMenu}
        menuMembers={menuMembers}
        menuMembersLoading={menuMembersLoading}
        onRequestClose={closeRowMenu}
        onOpenDetail={handleOpenDetail}
        onTaskDeleted={handleTaskDeleted}
        onTaskPatched={handleTaskPatched}
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {/* Search + clear */}
        <div className="relative min-w-[12rem] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search summary, tags, type or owner…"
            className="h-9 w-full rounded-lg border border-gray-200 bg-white pl-9 pr-8 text-sm outline-none transition placeholder:text-gray-400 focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-600"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Filter panel */}
        <TaskFilterPanel
          filters={filters}
          onChange={setFilters}
          onClearAll={() => setFilters({})}
          ownerOptions={filterMemberOptions}
          approverOptions={filterMemberOptions}
          typeOptions={TASK_TYPE_DEFINITIONS.map((t) => ({ value: t.value, label: t.label }))}
          tagOptions={tagOptions}
        />

        {/* Saved Views */}
        <div className="relative shrink-0" ref={viewsRef}>
          <button
            type="button"
            onClick={() => setViewsOpen((v) => !v)}
            aria-label="Saved views"
            data-testid="saved-views-button"
            className={`inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-xs font-medium transition ${
              activeViewId
                ? 'border-[#3CCED7] bg-[#3CCED7]/10 text-[#2ab5be]'
                : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
            }`}
          >
            <Bookmark className="h-3.5 w-3.5 shrink-0" />
            {activeViewId ? (savedViews.find((v) => v.id === activeViewId)?.name ?? 'View') : 'Views'}
            {savedViews.length > 0 && !activeViewId && (
              <span className="ml-0.5 rounded-full bg-gray-100 px-1.5 py-px text-[10px] font-semibold text-gray-500">
                {savedViews.length}
              </span>
            )}
          </button>

          {viewsOpen && (
            <div
              className="absolute right-0 top-full z-30 mt-1.5 w-56 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-lg"
              data-testid="saved-views-dropdown"
            >
              {activeViewId && (
                <div className="border-b border-gray-100 p-1.5">
                  <button
                    type="button"
                    onClick={() => {
                      setActiveViewId(null);
                      restorePreViewState();
                      sessionStorage.removeItem(preViewKey(projectId));
                      setCurrentPage(1);
                      setViewsOpen(false);
                    }}
                    className="w-full rounded-lg px-3 py-2 text-left text-xs font-medium text-gray-500 transition hover:bg-gray-50"
                    data-testid="clear-view-button"
                  >
                    ✕ Clear active view
                  </button>
                </div>
              )}
              {savedViews.length === 0 ? (
                <p className="px-3 py-3 text-xs text-gray-400">No saved views yet.</p>
              ) : (
                <ul className="max-h-60 overflow-y-auto py-1">
                  {savedViews.map((v) => (
                    <li key={v.id}>
                      <button
                        type="button"
                        onClick={() => applyView(v)}
                        className={`group flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition hover:bg-gray-50 ${
                          activeViewId === v.id ? 'font-semibold text-[#2ab5be]' : 'text-gray-700'
                        }`}
                        data-testid="saved-view-item"
                      >
                        <span className="truncate">{v.name}</span>
                        <div className="flex shrink-0 items-center gap-0.5">
                          <button
                            type="button"
                            aria-label={`Update view ${v.name}`}
                            data-testid="update-view-button"
                            onClick={(e) => updateView(v.id, e)}
                            className="rounded p-0.5 text-gray-300 transition hover:text-[#2ab5be] group-hover:text-gray-400"
                            title="Update view with current filters"
                          >
                            <Save className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            aria-label={`Delete view ${v.name}`}
                            data-testid="delete-view-button"
                            onClick={(e) => deleteView(v.id, e)}
                            className="rounded p-0.5 text-gray-300 transition hover:text-rose-500 group-hover:text-gray-400"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <div className="border-t border-gray-100 p-1.5">
                {showSaveInput ? (
                  <div className="flex items-center gap-1">
                    <input
                      autoFocus
                      type="text"
                      value={savingViewName}
                      onChange={(e) => setSavingViewName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') confirmSaveView();
                        if (e.key === 'Escape') { setShowSaveInput(false); setSavingViewName(''); }
                      }}
                      placeholder="View name…"
                      className="h-7 flex-1 rounded border border-gray-200 px-2 text-xs outline-none focus:border-[#3CCED7]"
                      data-testid="save-view-name-input"
                    />
                    <button
                      type="button"
                      onClick={confirmSaveView}
                      disabled={!savingViewName.trim()}
                      className="h-7 rounded bg-[#3CCED7] px-2 text-xs font-semibold text-white disabled:opacity-40 hover:bg-[#2ab5be]"
                      data-testid="save-view-confirm-button"
                    >
                      Save
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setShowSaveInput(true)}
                    className="w-full rounded-lg px-3 py-2 text-left text-xs font-medium text-[#2ab5be] transition hover:bg-[#3CCED7]/10"
                    data-testid="save-view-button"
                  >
                    + Save current view
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Sort */}
        <div className="relative shrink-0">
          <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="h-9 appearance-none rounded-lg border border-gray-200 bg-white pl-3 pr-8 text-xs font-medium text-gray-700 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
            aria-label="Sort tasks"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>

        {/* Group by */}
        <div className="relative shrink-0">
          <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
          <select
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            className="h-9 appearance-none rounded-lg border border-gray-200 bg-white pl-3 pr-8 text-xs font-medium text-gray-700 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
            aria-label="Group tasks"
          >
            <option value="none">No grouping</option>
            <option value="status">By status</option>
            <option value="priority">By priority</option>
            <option value="type">By type</option>
            <option value="owner">By owner</option>
          </select>
        </div>

        <button
          type="button"
          onClick={() => setBulkMode((v) => !v)}
          className={`shrink-0 inline-flex h-9 items-center rounded-xl border px-3.5 text-xs font-semibold transition ${bulkMode
              ? 'border-[#2fc6d6] bg-white text-[#2fc6d6] shadow-sm hover:bg-[#2fc6d6]/5'
              : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
            }`}
        >
          {bulkMode ? 'Exit Bulk edit' : 'Bulk edit'}
        </button>
      </div>

      {bulkMode && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 shadow-[0_2px_8px_rgba(15,23,42,0.04)]">
          <div className="min-w-0 text-xs text-gray-500">
            Bulk edit is active. Select tasks to apply bulk actions or send them to Linear.
          </div>
          <div
            className="grid w-full min-w-0 grid-cols-1 overflow-hidden rounded-lg border border-gray-200 bg-white sm:w-auto sm:grid-cols-2"
            role="group"
            aria-label="Linear bulk actions"
          >
            <button
              type="button"
              disabled={!projectId}
              title={
                !projectId
                  ? 'Select a project in the sidebar first'
                  : 'Import Linear issues as tasks in this project'
              }
              onClick={onOpenLinearImport}
              className="inline-flex min-h-9 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap border-b border-gray-200 px-2.5 py-2 text-xs font-semibold leading-none text-gray-800 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 sm:border-b-0 sm:border-r"
            >
              <ArrowDownToLine className="h-3.5 w-3.5 shrink-0 text-[#5E6AD2]" aria-hidden />
              Import from Linear
            </button>
            <button
              type="button"
              disabled={!projectId || selectedIds.length === 0}
              title={
                !projectId
                  ? 'Select a project in the sidebar first'
                  : selectedIds.length === 0
                    ? 'Select one or more tasks first'
                    : 'Push selected tasks to Linear'
              }
              onClick={openLinearOutput}
              className="inline-flex min-h-9 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap bg-[#5E6AD2]/10 px-2.5 py-2 text-xs font-semibold leading-none text-[#4a55b8] transition hover:bg-[#5E6AD2]/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <ArrowUpToLine className="h-3.5 w-3.5 shrink-0 text-[#5E6AD2]" aria-hidden />
              Output to Linear
            </button>
          </div>
        </div>
      )}

      {bulkMode && selectedIds.length > 0 && (
        <BulkActionToolbar
          selectedCount={selectedIds.length}
          memberOptions={memberOptions}
          statusOptions={bulkAllowedStatusOptions}
          statusDisabledReason={bulkStatusDisabledReason}
          loading={bulkBusy}
          onApply={applyBulkField}
          onClearSelection={() => setSelectedIds([])}
        />
      )}
      {bulkFailures.length > 0 && (
        <div className="mb-3 rounded-lg border border-rose-200 bg-rose-50/70 p-3">
          <div className="mb-1 text-xs font-semibold text-rose-700">
            Bulk update failed ({bulkFailures.length})
          </div>
          <div className="space-y-1 text-xs text-rose-700">
            {bulkFailures.slice(0, 4).map((item, index) => (
              <div key={`${item.task_id ?? 'na'}-${index}`}>
                Task {item.task_id ?? 'N/A'}: {item.reason}
              </div>
            ))}
            {bulkFailures.length > 4 && (
              <div className="text-rose-600">+{bulkFailures.length - 4} more</div>
            )}
          </div>
          <button
            type="button"
            onClick={() => setBulkFailures([])}
            className="mt-2 text-xs font-medium text-rose-700 underline underline-offset-2"
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        {error ? (
          <div className="px-6 py-12 text-center text-sm text-rose-600">{error}</div>
        ) : !loading && sorted.length === 0 ? (

          <EmptyState
            icon={<ListTodo className="h-5 w-5" />}
            title="No tasks yet"
            description="Create your first task to start tracking work in this project."
            actionLabel="Add the first task"
            onAction={() => setQuickCreateOpen(true)}
            actionIcon={<Plus className="h-3.5 w-3.5" />}
          />

        ) : (
          <div className="overflow-x-auto">
          <table data-testid="task-list" className="w-full min-w-[980px] table-fixed text-xs">
            <colgroup>
              <col className={TABLE_COLUMN_WIDTHS.icon} />
              {bulkMode ? <col className={TABLE_COLUMN_WIDTHS.select} /> : null}
              <col />
              <col className={TABLE_COLUMN_WIDTHS.type} />
              <col className={TABLE_COLUMN_WIDTHS.status} />
              <col className={TABLE_COLUMN_WIDTHS.owner} />
              <col className={TABLE_COLUMN_WIDTHS.approver} />
              <col className={TABLE_COLUMN_WIDTHS.start} />
              <col className={TABLE_COLUMN_WIDTHS.due} />
            </colgroup>
            <thead className="border-b border-gray-100 bg-gray-50/60 text-[11px] font-medium uppercase tracking-wide text-gray-400">
              <tr>
                <th className={`${TABLE_COLUMN_WIDTHS.icon} px-4 py-2.5 text-left`}></th>
                {bulkMode ? (
                  <th className={`${TABLE_COLUMN_WIDTHS.select} px-2 py-2.5 text-left`}>
                    <input
                      type="checkbox"
                      checked={isCurrentPageSelected}
                      onChange={(e) => toggleSelectAll(e.target.checked)}
                      className="h-4 w-4 rounded border-gray-300 accent-gray-900 focus:ring-gray-300"
                    />
                  </th>
                ) : null}
                <th className="px-4 py-2.5 text-left">Summary</th>
                <th className={`${TABLE_COLUMN_WIDTHS.type} px-4 py-2.5 text-left`}>Type</th>
                <th className={`${TABLE_COLUMN_WIDTHS.status} px-4 py-2.5 text-left`}>Status</th>
                <th className={`${TABLE_COLUMN_WIDTHS.owner} px-4 py-2.5 text-left`}>Owner</th>
                <th className={`${TABLE_COLUMN_WIDTHS.approver} px-4 py-2.5 text-left`}>Approver</th>
                <th className={`${TABLE_COLUMN_WIDTHS.start} px-4 py-2.5 text-left`}>Start</th>
                <th className={`${TABLE_COLUMN_WIDTHS.due} px-5 py-2.5 text-left`}>Due</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 text-gray-700">
              {loading ? Array.from({ length: 6 }, (_, index) => (
                <tr key={`tasks-list-skeleton-${index}`}>
                  <td className={`${TABLE_COLUMN_WIDTHS.icon} px-4 py-3`}>
                    <Skeleton className="h-2 w-2 rounded-full" />
                  </td>
                  {bulkMode ? (
                    <td className={`${TABLE_COLUMN_WIDTHS.select} px-2 py-3`}>
                      <Skeleton className="h-4 w-4 rounded-sm" />
                    </td>
                  ) : null}
                  <td className="px-4 py-3">
                    <div className="min-w-0 max-w-[32rem] space-y-2">
                      <Skeleton className="h-4 w-full max-w-[22rem]" />
                      <Skeleton className="h-3 w-full max-w-[32rem]" />
                    </div>
                  </td>
                  <td className={`${TABLE_COLUMN_WIDTHS.type} px-4 py-3`}><Skeleton className="h-3 w-16" /></td>
                  <td className={`${TABLE_COLUMN_WIDTHS.status} px-4 py-3`}><Skeleton className="h-5 w-16 rounded-full" /></td>
                  <td className={`${TABLE_COLUMN_WIDTHS.owner} px-4 py-3`}><Skeleton className="h-3 w-16" /></td>
                  <td className={`${TABLE_COLUMN_WIDTHS.approver} px-4 py-3`}><Skeleton className="h-3 w-16" /></td>
                  <td className={`${TABLE_COLUMN_WIDTHS.due} px-5 py-3`}><div className="flex justify-start"><Skeleton className="h-3 w-14" /></div></td>
                </tr>
              )) : displayRows.map((row, index) => {
                if (row.kind === 'header') {
                  const colspan = bulkMode ? 8 : 7;
                  return (
                    <tr key={row.key} className="bg-gray-50/80">
                      <td colSpan={colspan} className="px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-gray-500">
                        {row.label}
                      </td>
                    </tr>
                  );
                }
                const { task } = row;

                const priority = task.priority ?? 'MEDIUM';
                const status = task.status ?? 'DRAFT';
                const statusMeta = TASK_STATUS_BY_VALUE[status] ?? TASK_STATUS_BY_VALUE.DRAFT;
                const isSaving = !!task.id && savingIds.includes(task.id);
                const isSelected = !!task.id && selectedIds.includes(task.id);
                const openPriorityUpward = index >= Math.max(paginatedVisible.length - 3, 0);
                const openOverlayUpward = index >= Math.max(paginatedVisible.length - 3, 0);
                const colSpan = bulkMode ? 8 : 7;
                const subtasks = task.id ? (subtasksMap.get(task.id) ?? []) : [];
                const isExpanded = !!task.id && expandedIds.has(task.id);
                return (
                  <React.Fragment key={task.id}>
                  <tr
                    key={task.id}
                    data-testid="task-row"
                    className={`cursor-pointer transition-colors duration-150 hover:bg-gray-50/50 ${task.id && recentlyUpdatedIds.includes(task.id)
                        ? 'bg-emerald-50/45'
                        : bulkMode && isSelected
                          ? 'bg-cyan-50/40'
                          : ''
                      }`}
                    onClick={() => {
                      if (!task.id) return;
                      if (bulkMode) {
                        toggleSelection(task.id, !isSelected);
                        return;
                      }
                      const drawerSlug = resolveTaskSlug(task);
                      if (drawerSlug) openDrawer(drawerSlug);
                    }}
                    onContextMenu={(e) => openRowMenu(e, task)}
                  >
                    <td className={`${TABLE_COLUMN_WIDTHS.icon} align-middle px-4 py-1.5`}>
                      <div className="relative" onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenDuePickerTaskId(null);
                            setOpenPriorityTaskId((prev) => (prev === task.id ? null : task.id ?? null));
                          }}
                          className={`inline-block h-2.5 w-2.5 rounded-full ${TASK_PRIORITY_BY_VALUE[priority]?.dot ?? 'bg-gray-300'} ring-2 ring-white transition hover:scale-110`}
                          title={`Priority: ${TASK_PRIORITY_BY_VALUE[priority]?.label ?? priority}`}
                        />
                        {openPriorityTaskId === task.id ? (
                          <div
                            className={`absolute left-0 z-20 min-w-[136px] rounded-lg border border-gray-200 bg-white p-1.5 shadow-lg ${openPriorityUpward ? 'bottom-5' : 'top-5'
                              }`}
                          >
                            {TASK_PRIORITY_OPTIONS.map((opt) => (
                              <button
                                key={opt}
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  void updatePriority(task, opt);
                                }}
                                className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition ${opt === priority ? 'bg-gray-100 text-gray-900' : 'text-gray-600 hover:bg-gray-50'
                                  }`}
                              >
                                <span className={`h-2 w-2 rounded-full ${TASK_PRIORITY_BY_VALUE[opt]?.dot ?? 'bg-gray-300'}`} />
                                <span>{TASK_PRIORITY_BY_VALUE[opt]?.label ?? opt}</span>
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    {bulkMode ? (
                      <td
                        className={`${TABLE_COLUMN_WIDTHS.select} align-middle px-2 py-1.5`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <input
                          type="checkbox"
                          checked={!!task.id && selectedIds.includes(task.id)}
                          onChange={(e) => task.id && toggleSelection(task.id, e.target.checked)}
                          className="h-4 w-4 rounded border-gray-300 accent-gray-900 focus:ring-gray-300"
                        />
                      </td>
                    ) : null}
                    <td className="align-middle px-4 py-1.5">
                      <div className="min-w-0">
                        {editingSummaryId === task.id ? (
                          <input
                            autoFocus
                            value={summaryDraft}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) => setSummaryDraft(e.target.value)}
                            onBlur={() => {
                              void commitSummaryEdit(task);
                            }}
                            onKeyDown={async (e) => {
                              if (e.key === 'Escape') {
                                setEditingSummaryId(null);
                                return;
                              }
                              if (e.key !== 'Enter') return;
                              await commitSummaryEdit(task);
                            }}
                            className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm font-medium text-gray-900 outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
                          />
                        ) : (
                          <div
                            data-testid="task-row-open"
                            data-summary-id={task.id}
                            className="group relative flex w-full items-center gap-2 text-left"
                            onClick={(e) => {
                              e.stopPropagation();
                              if (bulkMode) { task.id && toggleSelection(task.id, !isSelected); return; }
                              const drawerSlug = resolveTaskSlug(task);
                              if (drawerSlug) openDrawer(drawerSlug);
                            }}
                          >
                            <span className="block min-w-0 flex-1 leading-5">
                              <span className="block truncate text-sm font-medium text-gray-900">
                                {highlight(task.summary || `Task #${task.id}`, search)}
                              </span>
                              {task.tags && task.tags.length > 0 ? (
                                <span className="mt-1 flex min-w-0 flex-wrap gap-1">
                                  {task.tags.slice(0, 3).map((tag) => (
                                    <span
                                      key={`${tag.name.toLowerCase()}:${tag.color}`}
                                      className="inline-flex max-w-[8rem] items-center gap-1 rounded-full bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-600 ring-1 ring-gray-200/80"
                                    >
                                      <span
                                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                                        style={{ backgroundColor: tag.color }}
                                        aria-hidden
                                      />
                                      <span className="truncate">{tag.name}</span>
                                    </span>
                                  ))}
                                  {task.tags.length > 3 ? (
                                    <span className="rounded-full bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium text-gray-400">
                                      +{task.tags.length - 3}
                                    </span>
                                  ) : null}
                                </span>
                              ) : null}
                            </span>
                            <button
                              type="button"
                              disabled={!task.id || pinBusyIds.includes(task.id)}
                              aria-label={task.is_pinned ? 'Unpin task' : 'Pin task'}
                              title={task.is_pinned ? 'Unpin task' : 'Pin task'}
                              onClick={(e) => {
                                e.stopPropagation();
                                void toggleTaskPin(task);
                              }}
                              className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-md transition ${
                                task.is_pinned
                                  ? 'text-[#2ab5be] hover:bg-[#2fc6d6]/10'
                                  : 'text-gray-300 opacity-0 hover:bg-gray-100 hover:text-gray-500 group-hover:opacity-100'
                              } disabled:cursor-not-allowed disabled:opacity-50`}
                            >
                              <Pin
                                className={`h-3 w-3 ${task.is_pinned ? 'fill-current' : ''}`}
                                aria-hidden
                              />
                            </button>
                            {(task.subtask_count ?? 0) > 0 && (
                              <button
                                data-toggle-subtasks
                                type="button"
                                onClick={(e) => task.id && toggleSubtasks(e as unknown as MouseEvent, task.id)}
                                className="flex-shrink-0 text-gray-300 hover:text-gray-500"
                                title="Toggle subtasks"
                              >
                                {task.id && loadingSubtaskIds.has(task.id) ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : task.id && expandedIds.has(task.id) ? (
                                  <ChevronDown className="h-3.5 w-3.5" />
                                ) : (
                                  <ChevronRight className="h-3.5 w-3.5" />
                                )}
                              </button>
                            )}
                            {task.id && truncatedSummaryIds.includes(task.id) ? (
                              <div className="pointer-events-none absolute bottom-full left-0 z-20 mb-1 hidden w-[24rem] rounded-lg border border-gray-200 bg-white p-2 text-xs text-gray-700 shadow-lg group-hover:block">
                                <div className="mb-1 font-semibold text-gray-900">{task.summary || `Task #${task.id}`}</div>
                                {task.description ? (
                                  <div className="line-clamp-3 text-gray-600">{task.description}</div>
                                ) : (
                                  <div className="text-gray-500">No description</div>
                                )}
                                <div className="mt-1 text-[10px] uppercase tracking-wide text-gray-400">
                                  {getTaskTypeShortLabel(task.type) ?? task.type ?? 'Task'}
                                </div>
                              </div>
                            ) : null}
                            {isSaving ? (
                              <span className="text-[10px] text-gray-400">Saving…</span>
                            ) : (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setEditingSummaryId(task.id ?? null);
                                  setSummaryDraft(task.summary || '');
                                }}
                                className="hidden h-6 items-center px-1 text-[10px] font-medium text-gray-400 transition-colors hover:text-gray-600 group-hover:inline-flex"
                              >
                                Edit
                              </button>
                            )}
                          </div>
                        )}
                        {task.description?.trim() ? (
                          <div className="mt-0.5 line-clamp-2 text-xs font-medium text-gray-600">
                            {task.description.trim()}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td className={`${TABLE_COLUMN_WIDTHS.type} align-middle px-4 py-1.5 text-xs font-medium uppercase tracking-wide text-gray-500`}>
                      {getTaskTypeShortLabel(task.type) ?? task.type ?? '—'}
                    </td>
                    <td className={`${TABLE_COLUMN_WIDTHS.status} align-middle px-4 py-1.5`}>
                      <div className="relative" onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenOwnerTaskId(null);
                            setOpenApproverTaskId(null);
                            setOpenDuePickerTaskId(null);
                            setOpenStartPickerTaskId(null);
                            setOpenStatusTaskId((prev) => (prev === task.id ? null : task.id ?? null));
                          }}
                          className={`inline-flex h-8 w-full items-center rounded-[6px] border border-transparent px-3 text-xs font-medium ${statusMeta.classes} outline-none transition hover:ring-1 hover:ring-gray-200`}
                        >
                          <span className="truncate">{TASK_STATUS_BY_VALUE[status]?.label ?? status}</span>
                        </button>
                        {openStatusTaskId === task.id ? (
                          <div
                            className={`absolute left-0 z-20 min-w-[148px] rounded-lg border border-gray-200 bg-white p-1.5 shadow-lg ${openOverlayUpward ? 'bottom-10' : 'top-10'
                              }`}
                          >
                            {getAllowedStatusOptions(task.status).map((opt) => (
                              <button
                                key={opt}
                                type="button"
                                onClick={() => {
                                  setOpenStatusTaskId(null);
                                  void updateTaskStatus(task, opt);
                                }}
                                className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                              >
                                <span className="inline-flex items-center gap-2">
                                  <span className={`h-2 w-2 rounded-full ${STATUS_DOT_CLASS[opt] ?? 'bg-gray-300'}`} />
                                  <span>{TASK_STATUS_BY_VALUE[opt]?.label ?? opt}</span>
                                </span>
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td
                      className={`${TABLE_COLUMN_WIDTHS.owner} align-middle px-4 py-1.5 text-xs text-gray-600`}
                    >
                      <div className="relative">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setOpenStatusTaskId(null);
                            setOpenApproverTaskId(null);
                            setOpenDuePickerTaskId(null);
                            setOpenStartPickerTaskId(null);
                            setOpenOwnerTaskId((prev) => (prev === task.id ? null : task.id ?? null));
                          }}
                          className="inline-flex h-8 w-full items-center justify-start truncate rounded-md border border-transparent px-1 text-left text-xs text-gray-700 transition hover:border-[#2fc6d6]/70 hover:bg-[#2fc6d6]/5 hover:px-3"
                        >
                          {task.owner ? userDisplayName(task.owner) : '—'}
                        </button>
                        {openOwnerTaskId === task.id ? (
                          <div
                            className={`absolute left-0 z-20 min-w-[168px] max-h-48 overflow-y-auto rounded-lg border border-gray-200 bg-white p-1.5 shadow-lg ${openOverlayUpward ? 'bottom-10' : 'top-10'
                              }`}
                          >
                            {memberOptions.map((member) => (
                              <button
                                key={member.id}
                                type="button"
                                onClick={() => {
                                  if (!task.id || task.owner?.id === member.id) {
                                    setOpenOwnerTaskId(null);
                                    return;
                                  }
                                  setOpenOwnerTaskId(null);
                                  void (async () => {
                                    const ok = await updateSingleTask(
                                      task,
                                      {
                                        owner_id: member.id,
                                        owner: { id: member.id, username: member.label, email: '' },
                                      },
                                      { owner_id: member.id },
                                      'Failed to update owner'
                                    );
                                    if (ok) toast.success('Owner updated');
                                  })();
                                }}
                                className="flex w-full items-center rounded-md px-2 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                              >
                                {member.label}
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td
                      className={`${TABLE_COLUMN_WIDTHS.approver} align-middle px-4 py-1.5 text-xs text-gray-600`}
                    >
                      {(() => {
                        const disabledReason = getApproverDisabledReason(task.status);
                        const isDisabled = Boolean(disabledReason);
                        return (
                          <div className="group relative">
                            <button
                              type="button"
                              disabled={isDisabled}
                              onClick={(e) => {
                                e.stopPropagation();
                                setOpenStatusTaskId(null);
                                setOpenOwnerTaskId(null);
                                setOpenDuePickerTaskId(null);
                                setOpenStartPickerTaskId(null);
                                setOpenApproverTaskId((prev) => (prev === task.id ? null : task.id ?? null));
                              }}
                              className={`inline-flex h-8 w-full items-center justify-start truncate rounded-md border border-transparent px-1 text-left text-xs transition ${isDisabled
                                  ? 'cursor-default text-gray-500'
                                  : 'text-gray-700 hover:border-[#2fc6d6]/70 hover:bg-[#2fc6d6]/5 hover:px-3'
                                }`}
                            >
                              {task.current_approver ? userDisplayName(task.current_approver) : '—'}
                            </button>
                            {!isDisabled && openApproverTaskId === task.id ? (
                              <div
                                className={`absolute left-0 z-20 min-w-[168px] max-h-48 overflow-y-auto rounded-lg border border-gray-200 bg-white p-1.5 shadow-lg ${openOverlayUpward ? 'bottom-10' : 'top-10'
                                  }`}
                              >
                                <button
                                  type="button"
                                  onClick={() => {
                                    setOpenApproverTaskId(null);
                                    if (!task.id || !task.current_approver?.id) return;
                                    void (async () => {
                                      const ok = await updateSingleTask(
                                        task,
                                        { current_approver_id: undefined, current_approver: undefined },
                                        { current_approver_id: null },
                                        'Failed to update approver'
                                      );
                                      if (ok) toast.success('Approver updated');
                                    })();
                                  }}
                                  className="flex w-full items-center rounded-md px-2 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                                >
                                  —
                                </button>
                                {memberOptions.map((member) => (
                                  <button
                                    key={member.id}
                                    type="button"
                                    onClick={() => {
                                      setOpenApproverTaskId(null);
                                      if (!task.id || task.current_approver?.id === member.id) return;
                                      void (async () => {
                                        const ok = await updateSingleTask(
                                          task,
                                          {
                                            current_approver_id: member.id,
                                            current_approver: { id: member.id, username: member.label, email: '' },
                                          },
                                          { current_approver_id: member.id },
                                          'Failed to update approver'
                                        );
                                        if (ok) toast.success('Approver updated');
                                      })();
                                    }}
                                    className="flex w-full items-center rounded-md px-2 py-1.5 text-left text-xs text-gray-700 hover:bg-gray-50"
                                  >
                                    {member.label}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                            {isDisabled && disabledReason ? (
                              <div
                                className={`pointer-events-none absolute left-0 z-20 hidden w-56 rounded-lg border border-gray-200 bg-white p-2 text-[11px] text-gray-700 shadow-lg group-hover:block ${openOverlayUpward ? 'bottom-9' : 'top-9'
                                  }`}
                              >
                                {disabledReason}
                              </div>
                            ) : null}
                          </div>
                        );
                      })()}
                    </td>
                    <td
                      className={`${TABLE_COLUMN_WIDTHS.start} relative align-middle px-3 py-1.5 text-left text-xs tabular-nums text-gray-500`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenStatusTaskId(null);
                          setOpenOwnerTaskId(null);
                          setOpenApproverTaskId(null);
                          openStartPicker(task);
                        }}
                        className="inline-flex h-7 w-full items-center justify-start rounded-md border border-transparent px-2 text-left text-xs tabular-nums text-gray-700 transition hover:border-[#2fc6d6]/70 hover:bg-[#2fc6d6]/5"
                      >
                        {task.start_date ? formatTaskDateShort(task.start_date) : '—'}
                      </button>
                      {openStartPickerTaskId === task.id ? (
                        <div
                          className={`absolute right-0 z-20 w-52 rounded-lg border border-gray-200 bg-white p-2 shadow-lg ${index >= Math.max(paginatedVisible.length - 3, 0) ? 'bottom-11' : 'top-11'}`}
                        >
                          <input
                            type="date"
                            lang="en-GB"
                            value={startDraftByTaskId[task.id ?? -1] ?? ''}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) =>
                              setStartDraftByTaskId((prev) => ({
                                ...prev,
                                [task.id as number]: e.target.value,
                              }))
                            }
                            className="h-9 w-full rounded-md border border-gray-200 bg-white px-2 text-xs text-gray-700 outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
                          />
                          <div className="mt-2 flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                const val = startDraftByTaskId[task.id ?? -1] || null;
                                void commitStartDate(task, val);
                              }}
                              className="h-8 min-w-0 flex-1 rounded-lg bg-gradient-to-r from-[#7ee3e8] to-[#b9ee98] px-2 text-[11px] font-medium text-white shadow-sm transition hover:brightness-95"
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => void commitStartDate(task, null)}
                              className="h-8 min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2 text-[11px] font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-50"
                            >
                              Clear
                            </button>
                            <button
                              type="button"
                              onClick={() => setOpenStartPickerTaskId(null)}
                              className="h-8 min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2 text-[11px] font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-50"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </td>
                    <td
                      className={`${TABLE_COLUMN_WIDTHS.due} relative align-middle px-3 py-1.5 text-left text-xs tabular-nums text-gray-500`}
                    >
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenStatusTaskId(null);
                          setOpenOwnerTaskId(null);
                          setOpenApproverTaskId(null);
                          openDuePicker(task);
                        }}
                        className={`inline-flex h-7 w-full items-center justify-start rounded-md border border-transparent px-2 text-left text-xs tabular-nums transition hover:border-[#2fc6d6]/70 hover:bg-[#2fc6d6]/5 ${getDueDateTone(task.due_date)}`}
                      >
                        {task.due_date ? formatTaskDateShort(task.due_date) : '—'}
                      </button>
                      {openDuePickerTaskId === task.id ? (
                        <div
                          className={`absolute right-0 z-20 w-52 rounded-lg border border-gray-200 bg-white p-2 shadow-lg ${index >= Math.max(paginatedVisible.length - 3, 0) ? 'bottom-11' : 'top-11'
                            }`}
                        >
                          <input
                            type="date"
                            lang="en-GB"
                            value={dueDraftByTaskId[task.id ?? -1] ?? ''}
                            onClick={(e) => e.stopPropagation()}
                            onChange={(e) =>
                              setDueDraftByTaskId((prev) => ({
                                ...prev,
                                [task.id as number]: e.target.value,
                              }))
                            }
                            className="h-9 w-full rounded-md border border-gray-200 bg-white px-2 text-xs text-gray-700 outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/20"
                          />
                          <div className="mt-2 flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                const val = dueDraftByTaskId[task.id ?? -1] || null;
                                void commitDueDate(task, val);
                              }}
                              className="h-8 min-w-0 flex-1 rounded-lg bg-gradient-to-r from-[#7ee3e8] to-[#b9ee98] px-2 text-[11px] font-medium text-white shadow-sm transition hover:brightness-95"
                            >
                              Save
                            </button>
                            <button
                              type="button"
                              onClick={() => void commitDueDate(task, null)}
                              className="h-8 min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2 text-[11px] font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-50"
                            >
                              Clear
                            </button>
                            <button
                              type="button"
                              onClick={() => setOpenDuePickerTaskId(null)}
                              className="h-8 min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2 text-[11px] font-medium text-gray-600 transition hover:border-gray-300 hover:bg-gray-50"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                  {isExpanded && subtasks.length === 0 && (
                    <tr key={`${task.id}-empty`}>
                      <td colSpan={colSpan} className="py-1.5 pl-14 text-xs text-gray-400">
                        No subtasks.
                      </td>
                    </tr>
                  )}
                  {isExpanded && subtasks.map((sub) => (
                    <tr
                      key={`sub-${sub.id}`}
                      className="cursor-pointer bg-gray-50/60 hover:bg-gray-100/60"
                      onClick={() => {
                        const drawerSlug = resolveTaskSlug(sub);
                        if (drawerSlug) openDrawer(drawerSlug);
                      }}
                    >
                      <td colSpan={colSpan} className="py-1.5 pl-14 pr-4">
                        <div className="flex items-center gap-3 text-sm text-gray-700">
                          <span className="truncate font-medium">{sub.summary || `Task #${sub.id}`}</span>
                          <span className="flex-shrink-0 text-xs text-gray-400">{sub.type}</span>
                          <span className="flex-shrink-0 text-xs text-gray-400">{sub.owner ? userDisplayName(sub.owner) : 'Unassigned'}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                  </React.Fragment>
                );
              })}
              {!loading && !error && projectId && (
                <tr data-testid="inline-add-task-row">
                  <td colSpan={bulkMode ? 8 : 7} className="border-t border-dashed border-gray-100">
                    <button
                      type="button"
                      onClick={() => setQuickCreateOpen(true)}
                      className="flex w-full items-center gap-2 px-4 py-2.5 text-xs text-gray-400 transition hover:bg-gray-50/60 hover:text-gray-600"
                    >
                      <Plus className="h-3.5 w-3.5 shrink-0" />
                      Add a task
                    </button>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          </div>
        )}
        {!loading && !error && sorted.length > LIST_PAGE_SIZE ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-4 py-2.5 text-xs text-gray-600">
            <span className="text-gray-400">
              {pageStart}–{pageEnd} of {visible.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => goToPage(safeCurrentPage - 1)}
                disabled={safeCurrentPage <= 1}
                className="inline-flex h-8 items-center gap-1 rounded-md px-2.5 text-xs font-medium text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ChevronLeft className="h-3 w-3" aria-hidden="true" />
                Prev
              </button>
              <span className="text-gray-500">
                Page {safeCurrentPage} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => goToPage(safeCurrentPage + 1)}
                disabled={safeCurrentPage >= totalPages}
                className="inline-flex h-8 items-center gap-1 rounded-md px-2.5 text-xs font-medium text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next
                <ChevronRight className="h-3 w-3" aria-hidden="true" />
              </button>
            </div>
          </div>
        ) : null}
      </div>
      <LinearBulkOutputModal
        isOpen={linearBulkOpen}
        onClose={() => setLinearBulkOpen(false)}
        taskIds={selectedIds}
        tasks={tasks}
        onComplete={() => {
          void onLinearBulkSynced?.();
        }}
      />
      {projectId && (
        <QuickTaskCreate
          projectId={projectId}
          open={quickCreateOpen}
          onClose={() => setQuickCreateOpen(false)}
          onCreated={(task) => {
            setCurrentPage(1);
            const q = search.trim().toLowerCase();
            const hiddenBySearch = !!q && !`${task.summary ?? ''} ${(task.tags ?? []).map((tag) => tag.name).join(' ')} ${task.type ?? ''} ${task.owner?.username ?? ''}`.toLowerCase().includes(q);
            const hiddenByStatus = !!filters.status && !(Array.isArray(filters.status) ? filters.status : [filters.status]).includes(task.status ?? '');
            const hiddenByPriority = !!filters.priority && !(Array.isArray(filters.priority) ? filters.priority : [filters.priority]).includes(task.priority ?? '');
            const hiddenByType = !!filters.type && !(Array.isArray(filters.type) ? filters.type : [filters.type]).includes(task.type ?? '');
            const hiddenByTags = !!filters.tag_names?.length && !(task.tags ?? []).some((tag) => filters.tag_names?.includes(tag.name));
            if (hiddenBySearch || hiddenByStatus || hiddenByPriority || hiddenByType || hiddenByTags) {
              toast('Task created but hidden by your active filters — clear them to see it.', { icon: '⚠️', duration: 5000 });
            }
            const drawerSlug = resolveTaskSlug(task);
            if (drawerSlug) openDrawer(drawerSlug);
          }}
        />
      )}
      <TaskDrawer
        taskSlug={drawerTaskSlug}
        onClose={() => openDrawer(null)}
        onTaskUpdate={async () => { await onRefresh?.(); }}
        externalRefreshKey={drawerRefreshKey}
        taskSlugs={paginatedVisible.map((t) => resolveTaskSlug(t)).filter(Boolean) as string[]}
        onNavigate={(dir) => {
          if (!drawerTaskSlug) return;
          const slugs = paginatedVisible.map((t) => resolveTaskSlug(t)).filter(Boolean) as string[];
          const idx = slugs.indexOf(drawerTaskSlug);
          if (idx === -1) return;
          const next = dir === 'next' ? slugs[idx + 1] : slugs[idx - 1];
          if (next) openDrawer(next);
        }}
      />
    </div>
  );
}
