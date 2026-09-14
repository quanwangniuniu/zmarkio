'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { useTaskTracking } from '@/lib/tracking/useTaskTracking';
import { useBuildUrl } from '@/lib/buildUrl';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ChatFAB from '@/components/global-chat/ChatFAB';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { TaskAPI } from '@/lib/api/taskApi';
import { ProjectAPI, type ProjectMemberData } from '@/lib/api/projectApi';
import type { TaskData } from '@/types/task';
import { normalizeTaskFromApi } from '@/lib/tasks/normalizeTaskFromApi';

import TaskDetailHeader from '@/components/tasks/detail/TaskDetailHeader';
import TaskDescriptionBlock from '@/components/tasks/detail/TaskDescriptionBlock';
import TaskTypeBlock from '@/components/tasks/detail/TaskTypeBlock';
import TaskSubtasksBlock from '@/components/tasks/detail/TaskSubtasksBlock';
import TaskRelationsBlock from '@/components/tasks/detail/TaskRelationsBlock';
import TaskAttachmentsBlock from '@/components/tasks/detail/TaskAttachmentsBlock';
import TaskFieldHistoryBlock from '@/components/tasks/detail/TaskFieldHistoryBlock';
import PropertiesPanel from '@/components/tasks/detail/PropertiesPanel';
import ApprovalTimelinePanel from '@/components/tasks/detail/ApprovalTimelinePanel';
import { useAuthStore } from '@/lib/authStore';
import { useTaskStore } from '@/lib/taskStore';
import EngagementPanel from '@/components/tasks/detail/EngagementPanel';
import CommentSection from '@/components/comments/CommentSection';

export default function TaskV2DetailPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const buildUrl = useBuildUrl();
  const taskId = params?.taskId ? String(params.taskId) : null;
  const routeProjectId = searchParams?.get('project_id') || null;

  const [task, setTask] = useState<TaskData | null>(null);
  const updateTaskInStore = useTaskStore((s) => s.updateTask);
  const projectId = task?.project?.id ?? task?.project_id ?? null;
  const { markInteraction } = useTaskTracking(taskId ?? 0, projectId);
  const [members, setMembers] = useState<ProjectMemberData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [activeTab, setActiveTab] = useState<'details' | 'history'>('details');

  const load = useCallback(
    async (options?: { internalRefetch?: boolean }) => {
      if (!taskId) return;
      setLoading(true);
      try {
        const resp = await TaskAPI.getTask(taskId, {
          ...options,
          projectId: routeProjectId,
        });
        const fresh = normalizeTaskFromApi(resp.data);
        setTask(fresh);
        if (fresh.id) updateTaskInStore(fresh.id, fresh);
        setError(null);
      } catch (e) {
        setError((e as any)?.response?.data?.detail || 'Failed to load task');
      } finally {
        setLoading(false);
      }
    },
    [taskId, routeProjectId, updateTaskInStore],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const pid = task?.project?.id ?? task?.project_id;
    if (!pid) return;
    let cancelled = false;
    ProjectAPI.getProjectMembers(pid)
      .then((rows) => {
        if (!cancelled) setMembers(rows);
      })
      .catch(() => {
        if (!cancelled) setMembers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [task?.project?.id, task?.project_id]);

  /** Refresh side panels (Focus insights, comments list, etc.) without reloading task. */
  const onMutated = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  /** Reload task shell after field/status edits; does not count as a page open. */
  const reloadTask = useCallback(async (updatedTask?: TaskData) => {
    if (updatedTask?.id) {
      const normalized = normalizeTaskFromApi(updatedTask);
      setTask(normalized);
      updateTaskInStore(normalized.id!, normalized);
    }
    setRefreshKey((k) => k + 1);
    await load({ internalRefetch: true });
  }, [load, updateTaskInStore]);

  const doDelete = async () => {
    if (!task?.id) return;
    try {
      await TaskAPI.deleteTask(task.slug ?? task.id);
      router.push(buildUrl('/tasks'));
    } catch (e) {
      toast.error((e as any)?.response?.data?.detail || 'Delete failed');
    }
  };

  const currentUser = useAuthStore((s) => s.user);
  const isOwner = currentUser?.id != null && task?.owner?.id != null &&
    Number(currentUser.id) === Number(task.owner.id);
  const isApprover = currentUser?.id != null && task?.current_approver?.id != null &&
    Number(currentUser.id) === Number(task.current_approver.id);
  const isCreator = currentUser?.id != null && task?.created_by?.id != null &&
    Number(currentUser.id) === Number(task.created_by.id);
  const creatorCanEditUnassignedDraft = Boolean(
    task?.status === 'DRAFT' && !task.owner && !task.current_approver && isCreator
  );
  const readOnly = task?.status === 'LOCKED' || (!isOwner && !isApprover && !creatorCanEditUnassignedDraft);
  const taskShell = (task ?? {
    id: taskId ?? undefined,
    summary: '',
    description: '',
    status: 'DRAFT',
    type: 'task',
    project: null,
    project_id: null,
    owner: null,
    current_approver: null,
    linked_object: null,
    start_date: null,
    due_date: null,
  }) as TaskData;

  return (
    <ProtectedRoute renderChildrenWhileLoading>
      <DashboardLayout alerts={[]} upcomingMeetings={[]}>
        <div className="bg-gray-50">
          {error && !loading && (
          <div data-testid="task-detail-error" className="px-6 py-12 text-center text-sm text-rose-600">{error}</div>
        )}
          {(!error && (task || loading)) && (
          <div className="mx-auto max-w-[1440px] px-0 py-3 sm:px-6 sm:py-4">
            <TaskDetailHeader
              task={taskShell}
              members={members}
              readOnly={Boolean(readOnly)}
              onUpdated={reloadTask}
              onMutated={reloadTask}
              onDelete={() => setConfirmDelete(true)}
              loading={loading}
            />

            {/* Tab bar */}
            <div className="mt-4 flex border-b border-gray-100">
              {(['details', 'history'] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setActiveTab(tab)}
                  className={`relative mr-4 py-2.5 text-xs font-medium transition-colors ${
                    activeTab === tab
                      ? 'text-gray-900 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:rounded-full after:bg-[#3CCED7]'
                      : 'text-gray-400 hover:text-gray-600'
                  }`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>

            <div className="mt-4 grid min-w-0 grid-cols-1 gap-4 sm:gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
              <div className="min-w-0 space-y-5">
                {activeTab === 'details' && (<>
                <TaskDescriptionBlock
                  task={taskShell}
                  readOnly={Boolean(readOnly)}
                  onUpdated={reloadTask}
                  loading={loading}
                />
                <TaskTypeBlock task={taskShell} loading={loading} readOnly={Boolean(readOnly)} onUpdated={reloadTask} />
                <TaskSubtasksBlock
                  task={taskShell}
                  readOnly={Boolean(readOnly)}
                  refreshKey={refreshKey}
                  loading={loading}
                  onMutated={onMutated}
                />
                <TaskRelationsBlock
                  task={taskShell}
                  readOnly={Boolean(readOnly)}
                  loading={loading}
                  onMutated={onMutated}
                />
                {(task?.id || loading) && (
                  <TaskAttachmentsBlock
                    taskId={task?.id ?? 0}
                    readOnly={Boolean(readOnly)}
                    loading={loading}
                    onMutated={onMutated}
                  />
                )}
                {(task?.id || loading) && (
                  task?.id ? (
                    <CommentSection
                      entityType="task"
                      entityId={task.id}
                      readOnlyComposer={Boolean(readOnly)}
                    />
                  ) : null
                )}
                </>)}
                {activeTab === 'history' && (task?.id || loading) && (
                  <TaskFieldHistoryBlock
                    taskId={task?.id ?? 0}
                    refreshKey={refreshKey}
                    loading={loading}
                  />
                )}
              </div>

              <aside className="min-w-0 space-y-5">
                <PropertiesPanel
                  task={taskShell}
                  members={members}
                  readOnly={Boolean(readOnly)}
                  onUpdated={reloadTask}
                  loading={loading}
                  onFirstInteraction={() => markInteraction('priority_select', 'change')}
                />
                {(task?.id || loading) && (
                  <ApprovalTimelinePanel
                    taskId={task?.id ?? 0}
                    refreshKey={refreshKey}
                    loading={loading}
                  />
                )}
                {(task?.id || loading) && (
                  <EngagementPanel
                    taskId={task?.slug ?? taskId ?? ''}
                    refreshKey={refreshKey}
                    loading={loading}
                  />
                )}
              </aside>
            </div>
          </div>
        )}

        {confirmDelete && task && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
            <div className="w-full max-w-sm overflow-hidden rounded-xl bg-white shadow-2xl ring-1 ring-gray-100">
              <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
              <div className="p-5">
                <h3 className="text-base font-semibold text-gray-900">Delete this task?</h3>
                <p className="mt-2 text-sm text-gray-600">
                  &quot;{task.summary}&quot; will be permanently removed. This cannot be undone.
                </p>
                <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(false)}
                    className="inline-flex h-9 items-center rounded-lg bg-white px-4 text-sm font-medium text-gray-700 ring-1 ring-gray-200 hover:ring-gray-300"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={doDelete}
                    className="inline-flex h-9 items-center rounded-lg bg-white px-4 text-sm font-medium text-rose-600 ring-1 ring-rose-200 hover:bg-rose-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
        </div>
        <ChatFAB />
      </DashboardLayout>
    </ProtectedRoute>
  );
}
