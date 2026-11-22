'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import Link from 'next/link';
import { useBuildUrl } from '@/lib/buildUrl';
import {
  ChevronRight, Trash2, Share2, ArrowLeft, KanbanSquare,
  Calendar, ChevronsUp, ChevronUp, Minus, ChevronDown, ChevronsDown,
} from 'lucide-react';
import { TaskAPI } from '@/lib/api/taskApi';
import type { TaskData } from '@/types/task';
import StatusPill from './pills/StatusPill';
import TypeBadge from './pills/TypeBadge';
import FSMActionBar from './FSMActionBar';
import type { ProjectMemberData } from '@/lib/api/projectApi';
import { Skeleton } from '@/components/ui/skeleton';
import LinearPushTaskDialog from '@/components/linear/LinearPushTaskDialog';
import { UserInitialsAvatar } from './InlineSelect';

// ── Priority helpers ────────────────────────────────────────────────────────

const PRIORITY_TOKEN: Record<string, { bg: string; text: string; label: string; Icon: typeof Minus }> = {
  HIGHEST: { bg: 'bg-rose-50',   text: 'text-rose-700',   label: 'Highest', Icon: ChevronsUp },
  HIGH:    { bg: 'bg-orange-50', text: 'text-orange-700', label: 'High',    Icon: ChevronUp },
  MEDIUM:  { bg: 'bg-gray-100',  text: 'text-gray-700',   label: 'Medium',  Icon: Minus },
  LOW:     { bg: 'bg-sky-50',    text: 'text-sky-700',    label: 'Low',     Icon: ChevronDown },
  LOWEST:  { bg: 'bg-gray-100',  text: 'text-gray-500',   label: 'Lowest',  Icon: ChevronsDown },
};
const PRIORITY_VALUES = ['HIGHEST', 'HIGH', 'MEDIUM', 'LOW', 'LOWEST'] as const;

function formatDueDate(dateStr?: string | null): string {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function isDueOverdue(dateStr?: string | null): boolean {
  if (!dateStr) return false;
  return new Date(dateStr + 'T00:00:00') < new Date(new Date().toDateString());
}

function buildIssueKey(projectName?: string, taskId?: number) {
  const prefix = (projectName || 'TASK')
    .replace(/[^a-zA-Z0-9]/g, '')
    .slice(0, 4)
    .toUpperCase();
  return `${prefix || 'TASK'}-${taskId ?? '?'}`;
}

// ── Component ───────────────────────────────────────────────────────────────

interface Props {
  task: TaskData;
  members: ProjectMemberData[];
  readOnly: boolean;
  onUpdated: () => void | Promise<void>;
  onMutated: () => void | Promise<void>;
  onDelete: () => void;
  loading?: boolean;
  isDrawer?: boolean;
}

export default function TaskDetailHeader({
  task,
  members,
  readOnly,
  onUpdated,
  onMutated,
  onDelete,
  loading = false,
  isDrawer = false,
}: Props) {
  const [value, setValue] = useState(task.summary || '');
  const [saving, setSaving] = useState(false);
  const [fieldSaving, setFieldSaving] = useState(false);
  const [linearPushOpen, setLinearPushOpen] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  // which pill dropdown is open: 'priority' | 'owner' | null
  const [openField, setOpenField] = useState<'priority' | 'owner' | null>(null);
  const [editingDueDate, setEditingDueDate] = useState(false);
  const lastSaved = useRef(task.summary || '');
  const titleTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const dueDateInputRef = useRef<HTMLInputElement | null>(null);

  const resizeTitle = useCallback(() => {
    const textarea = titleTextareaRef.current;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 92)}px`;
  }, []);

  useEffect(() => {
    setValue(task.summary || '');
    lastSaved.current = task.summary || '';
  }, [task.summary, task.id]);

  useEffect(() => {
    if (!editingTitle) return;
    const textarea = titleTextareaRef.current;
    if (!textarea) return;
    resizeTitle();
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  }, [editingTitle, resizeTitle]);

  // Popover outside-click is handled by the transparent overlay rendered below

  // Auto-focus the date input when editing starts
  useEffect(() => {
    if (editingDueDate) dueDateInputRef.current?.focus();
  }, [editingDueDate]);

  const commit = async () => {
    const trimmed = value.trim();
    if (!task.id || !trimmed || trimmed === lastSaved.current) {
      if (!trimmed) setValue(lastSaved.current);
      setEditingTitle(false);
      return;
    }
    setSaving(true);
    try {
      await TaskAPI.updateTask(task.slug ?? task.id, { summary: trimmed });
      lastSaved.current = trimmed;
      await onUpdated();
      setEditingTitle(false);
    } catch (e) {
      toast.error((e as any)?.response?.data?.detail || 'Save failed');
      setValue(lastSaved.current);
    } finally {
      setSaving(false);
    }
  };

  const patchField = async (data: Partial<TaskData>) => {
    if (!task.id) return;
    setFieldSaving(true);
    try {
      await TaskAPI.updateTask(task.slug ?? task.id, data);
      await onUpdated();
    } catch (e) {
      toast.error((e as any)?.response?.data?.detail || 'Update failed');
    } finally {
      setFieldSaving(false);
    }
  };

  const isSubmitted = task.status !== 'DRAFT';
  const projectName = task.project?.name || 'Project';
  const projectId = task.project?.id ?? task.project_id;
  const issueKey = buildIssueKey(projectName, task.id);
  const priority = (task as any).priority as string || 'MEDIUM';
  const priorityMeta = PRIORITY_TOKEN[priority] ?? PRIORITY_TOKEN.MEDIUM;
  const PriorityIcon = priorityMeta.Icon;
  const ownerName = task.owner?.username || task.owner?.email || 'Unassigned';
  const activeMembers = members.filter((m) => m.is_active);
  const overdue = isDueOverdue(task.due_date);

  const buildUrl = useBuildUrl();
  const tasksListHref = buildUrl('/tasks');

  return (
    <section className="rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
      {/* Top bar */}
      <div className={`flex items-center gap-2 border-b border-gray-100 px-4 py-2 sm:px-6 ${isDrawer ? 'justify-end' : 'justify-between'}`}>
        {!isDrawer && (
          <nav className="flex min-w-0 items-center gap-2 text-xs text-gray-500">
            <Link
              href={tasksListHref}
              data-testid="back-to-tasks"
              title="Back to Tasks"
              aria-label="Back to Tasks"
              className="inline-flex h-6 w-6 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-gray-900"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
            </Link>
            <div className="flex min-w-0 items-center gap-1.5">
              <Link href={tasksListHref} className="hidden hover:text-gray-900 sm:inline">Tasks</Link>
              <ChevronRight className="hidden h-3 w-3 text-gray-300 sm:block" />
              <Link href={tasksListHref} className="max-w-[9rem] truncate hover:text-gray-900 sm:max-w-[14rem]">
                {projectName}
              </Link>
              <ChevronRight className="h-3 w-3 shrink-0 text-gray-300" />
              <span data-testid="task-id-label" className="shrink-0 font-semibold text-gray-900">{issueKey}</span>
            </div>
          </nav>
        )}
        <div className="flex shrink-0 items-center gap-1">
          {task.id ? (
            <button
              type="button"
              onClick={() => setLinearPushOpen(true)}
              title="Sync to Linear"
              aria-label="Sync to Linear"
              disabled={readOnly}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-[#5E6AD2] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <KanbanSquare className="h-3.5 w-3.5" />
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => toast('Share to Chat — coming soon')}
            title="Share to Chat"
            aria-label="Share to Chat"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-50 hover:text-gray-900"
          >
            <Share2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={readOnly}
            title="Delete task"
            aria-label="Delete task"
            data-testid="delete-task-button"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition hover:bg-rose-50 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* Title */}
      <div className="px-4 py-4 sm:px-6 sm:py-5">
        {saving && !loading && (
          <div className="mb-1 text-[11px] text-gray-400">Saving…</div>
        )}
        {loading ? (
          <div className="space-y-4">
            <Skeleton className="h-10 w-72" />
            <div className="flex flex-wrap items-center gap-2">
              <Skeleton className="h-6 w-20 rounded-full" />
              <Skeleton className="h-6 w-20 rounded-full" />
              <Skeleton className="h-6 w-24 rounded-full" />
              <Skeleton className="h-4 w-32" />
            </div>
          </div>
        ) : (
          <div className="relative -mx-1 w-[calc(100%+0.5rem)]">
            {editingTitle ? (
              <textarea
                ref={titleTextareaRef}
                rows={1}
                className="block max-h-[92px] min-h-[40px] w-full resize-none overflow-y-auto rounded-md border-0 border-b-2 border-transparent bg-transparent px-1 py-1 text-xl font-semibold leading-tight text-gray-900 outline-none transition placeholder:text-gray-300 focus:border-[#3CCED7] disabled:opacity-70 sm:text-[22px]"
                value={value}
                onChange={(e) => {
                  setValue(e.target.value.replace(/[\r\n]+/g, ' '));
                  resizeTitle();
                }}
                onInput={resizeTitle}
                onBlur={commit}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); (e.target as HTMLTextAreaElement).blur(); }
                  if (e.key === 'Escape') { setValue(lastSaved.current); setEditingTitle(false); }
                }}
                disabled={readOnly}
                placeholder="Task title"
              />
            ) : (
              <button
                type="button"
                className="block min-h-[40px] w-full rounded-md px-1 py-1 text-left text-xl font-semibold leading-tight text-gray-900 outline-none transition hover:bg-gray-50 focus:bg-white focus:ring-2 focus:ring-[#3CCED7]/30 disabled:cursor-default disabled:hover:bg-transparent sm:text-[22px]"
                onClick={() => { if (!readOnly) setEditingTitle(true); }}
                disabled={readOnly}
                title={value || 'Task title'}
              >
                <span className="block overflow-hidden break-words [-webkit-box-orient:vertical] [-webkit-line-clamp:3]">
                  {value || 'Task title'}
                </span>
              </button>
            )}
          </div>
        )}

        {/* Pills row — status, priority (editable), type, owner (editable), due date (editable) */}
        {!loading && (
          <>
          {openField && (
            <div
              className="fixed inset-0 z-10"
              onClick={() => setOpenField(null)}
              aria-hidden="true"
            />
          )}
          <div className="relative mt-4 flex min-w-0 flex-wrap items-center gap-2">
            {/* Status — display only; FSMActionBar handles transitions */}
            <StatusPill status={task.status} />

            {/* Priority — click to change */}
            <div className="relative">
              <button
                type="button"
                data-testid="header-priority-trigger"
                disabled={readOnly || fieldSaving}
                onClick={() => setOpenField(openField === 'priority' ? null : 'priority')}
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium transition ${priorityMeta.bg} ${priorityMeta.text} disabled:cursor-not-allowed disabled:opacity-60 ${!readOnly ? 'hover:ring-1 hover:ring-gray-300' : ''}`}
                title="Change priority"
              >
                <PriorityIcon className="h-3 w-3" />
                {priorityMeta.label}
              </button>
              {openField === 'priority' && (
                <div className="absolute left-0 top-full z-20 mt-1 w-32 overflow-y-auto rounded-lg bg-white py-1 shadow-lg ring-1 ring-gray-100" style={{ maxHeight: '40vh' }}>
                  {PRIORITY_VALUES.map((p) => {
                    const meta = PRIORITY_TOKEN[p];
                    const { Icon } = meta;
                    return (
                      <button
                        key={p}
                        type="button"
                        onClick={() => {
                          setOpenField(null);
                          patchField({ priority: p } as any);
                        }}
                        className={`flex w-full items-center gap-2 px-3 py-1.5 text-[11px] font-medium transition hover:bg-gray-50 ${p === priority ? 'bg-gray-50' : ''}`}
                      >
                        <Icon className={`h-3 w-3 ${meta.text}`} />
                        <span className={meta.text}>{meta.label}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Type — display only */}
            <TypeBadge type={task.type} />

            {/* Owner — click to change */}
            <div className="relative">
              <button
                type="button"
                data-testid="header-owner-trigger"
                disabled={readOnly || fieldSaving || isSubmitted}
                onClick={() => setOpenField(openField === 'owner' ? null : 'owner')}
                className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] text-gray-500 transition disabled:cursor-not-allowed disabled:opacity-60 ${!readOnly && !isSubmitted ? 'hover:bg-gray-100 hover:text-gray-700' : ''}`}
                title="Change owner"
              >
                <UserInitialsAvatar name={ownerName} />
                {ownerName}
              </button>
              {openField === 'owner' && (
                <div className="absolute left-0 top-full z-20 mt-1 w-48 max-h-56 overflow-y-auto rounded-lg bg-white py-1 shadow-lg ring-1 ring-gray-100">
                  <button
                    type="button"
                    onClick={() => { setOpenField(null); patchField({ owner_id: null }); }}
                    className={`flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-gray-600 transition hover:bg-gray-50 ${!task.owner ? 'bg-gray-50 font-medium' : ''}`}
                  >
                    <span className="inline-block h-5 w-5 rounded-full bg-gray-100" />
                    Unassigned
                  </button>
                  {activeMembers.map((m) => {
                    const name = m.user.username || m.user.email || `User ${m.user.id}`;
                    const isSelected = task.owner?.id === m.user.id;
                    return (
                      <button
                        key={m.user.id}
                        type="button"
                        onClick={() => { setOpenField(null); patchField({ owner_id: m.user.id }); }}
                        className={`flex w-full items-center gap-2 px-3 py-1.5 text-[11px] text-gray-700 transition hover:bg-gray-50 ${isSelected ? 'bg-gray-50 font-medium' : ''}`}
                      >
                        <UserInitialsAvatar name={name} />
                        <span className="truncate">{name}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Due date — click to edit */}
            {editingDueDate ? (
              <input
                ref={dueDateInputRef}
                type="date"
                defaultValue={task.due_date || ''}
                className="rounded-full border border-[#3CCED7] px-2.5 py-0.5 text-[11px] text-gray-700 outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
                onBlur={(e) => {
                  setEditingDueDate(false);
                  const v = e.target.value;
                  if (v !== (task.due_date || '')) patchField({ due_date: v || undefined });
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') setEditingDueDate(false);
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                }}
              />
            ) : (
              <button
                type="button"
                data-testid="header-due-date-trigger"
                disabled={readOnly || fieldSaving}
                onClick={() => setEditingDueDate(true)}
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] transition disabled:cursor-not-allowed disabled:opacity-60 ${
                  task.due_date
                    ? overdue
                      ? 'bg-rose-50 text-rose-700 hover:ring-1 hover:ring-rose-200'
                      : 'bg-gray-100 text-gray-600 hover:ring-1 hover:ring-gray-300'
                    : 'text-gray-400 hover:bg-gray-100 hover:text-gray-600'
                }`}
                title={task.due_date ? 'Change due date' : 'Set due date'}
              >
                <Calendar className="h-3 w-3" />
                {task.due_date ? formatDueDate(task.due_date) : 'Set due date'}
              </button>
            )}
          </div>
          </>
        )}
      </div>

      {/* FSM action bar */}
      <div className="overflow-hidden rounded-b-xl border-t border-gray-100 bg-gray-50/40 px-4 py-3 sm:px-6">
        {loading ? (
          <div className="flex flex-wrap items-center gap-2">
            <Skeleton className="h-9 w-28 rounded-md" />
            <Skeleton className="h-9 w-24 rounded-md" />
            <Skeleton className="h-9 w-20 rounded-md" />
          </div>
        ) : (
          <FSMActionBar task={task} members={members} onMutated={onMutated} />
        )}
      </div>

      {task.id ? (
        <LinearPushTaskDialog
          isOpen={linearPushOpen}
          onClose={() => setLinearPushOpen(false)}
          taskId={task.id}
          linearIssueId={task.linear_issue_id}
          onSuccess={onUpdated}
        />
      ) : null}
    </section>
  );
}
