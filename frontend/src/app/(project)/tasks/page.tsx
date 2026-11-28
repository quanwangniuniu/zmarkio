'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Plus } from 'lucide-react';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import ChatFAB from '@/components/global-chat/ChatFAB';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import { useTaskData } from '@/hooks/useTaskData';
import TabNav, { type TasksTab } from '@/components/tasks/TabNav';
import SummaryView from '@/components/tasks/SummaryView';
import ListView from '@/components/tasks/ListView';
import BoardView from '@/components/tasks/BoardView';
import GanttView from '@/components/tasks/GanttView';
import InsightsView from '@/components/tasks/InsightsView';
import MyActionsView from '@/components/tasks/MyActionsView';
import PlanningView from '@/components/tasks/PlanningView';
import StatusReportsView from '@/components/tasks/StatusReportsView';
import { Skeleton } from '@/components/ui/skeleton';
import LinearImportModal from '@/components/linear/LinearImportModal';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';
import { useBuildUrl } from '@/lib/buildUrl';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { defaultTasksTab, visibleTasksTabs } from '@/lib/tasks/tasksModuleConfig';

const SLIM_SCROLLBAR_TABS = new Set<TasksTab>(['insights', 'my-actions', 'planning', 'status-reports']);
const FLAT_TASKS_BASE = '/tasks';

export default function TasksV2Page() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const searchParams = useSearchParams();
  const tabParam = searchParams?.get('tab') as TasksTab | null;
  const drawerTaskFromQuery = searchParams?.get('drawerTask')?.trim() || null;
  const { projectId, activeProject, projectContextLoading } = useActiveProjectForFlatRoute();

  useOptionConfigVersion();
  const defaultTab = defaultTasksTab();
  const tab: TasksTab = tabParam && visibleTasksTabs().includes(tabParam) ? tabParam : defaultTab;

  const { tasks, loading, error, fetchTasks } = useTaskData();
  const [linearImportOpen, setLinearImportOpen] = useState(false);
  const [hasLoadedTaskListOnce, setHasLoadedTaskListOnce] = useState(false);
  const [myActionsRefreshKey, setMyActionsRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    if (projectContextLoading) {
      setHasLoadedTaskListOnce(false);
      return () => {
        cancelled = true;
      };
    }

    if (!projectId) {
      setHasLoadedTaskListOnce(false);
      return () => {
        cancelled = true;
      };
    }

    setHasLoadedTaskListOnce(false);
    fetchTasks({ project_id: projectId, page: 1 })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) {
          setHasLoadedTaskListOnce(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [fetchTasks, projectContextLoading, projectId]);

  const taskListLoading =
    projectContextLoading || (Boolean(projectId) && (!hasLoadedTaskListOnce || loading));

  const setTab = useCallback(
    (next: TasksTab) => {
      const params = new URLSearchParams(searchParams?.toString() || '');
      params.delete('project_id');
      if (next === defaultTab) params.delete('tab');
      else params.set('tab', next);
      const qs = params.toString();
      router.replace(qs ? buildUrl(`${FLAT_TASKS_BASE}?${qs}`) : buildUrl(FLAT_TASKS_BASE));
    },
    [router, searchParams, buildUrl, defaultTab]
  );

  const refreshTasks = async () => {
    if (projectId) {
      await fetchTasks({ project_id: projectId, page: 1 });
    }
    setMyActionsRefreshKey((k) => k + 1);
  };

  const headerActions = (
    <button
      type="button"
      onClick={() => router.push(buildUrl(`${FLAT_TASKS_BASE}/new`))}
      className="inline-flex items-center gap-2 rounded-md bg-gradient-to-br from-[#3CCED7] to-[#A6E661] px-3.5 py-1.5 text-sm font-semibold text-white shadow-sm transition hover:opacity-90"
    >
      <Plus className="h-4 w-4" />
      Create task
    </button>
  );

  return (
    <ProtectedRoute renderChildrenWhileLoading>
      <DashboardLayout
        alerts={[]}
        upcomingMeetings={[]}
        mainClassName={SLIM_SCROLLBAR_TABS.has(tab) ? 'task-tab-scrollbar' : ''}
      >
        <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
          <header className="mb-5 flex items-end justify-between gap-4">
            <div className="min-w-0">
              <h1 className="text-2xl font-semibold text-gray-900">Tasks</h1>
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-500">
                {projectContextLoading ? (
                  <Skeleton className="h-4 w-40 max-w-full" />
                ) : (
                  <span className="min-w-0 truncate">
                    {activeProject?.name ?? (projectId ? '' : 'No project selected')}
                  </span>
                )}
                {projectId && !activeProject?.name && !projectContextLoading ? (
                  <Skeleton className="h-4 w-36 max-w-full" />
                ) : null}
                <span aria-hidden="true">·</span>
                <span className="text-gray-400">
                  {taskListLoading ? (
                    <Skeleton className="h-4 w-16" />
                  ) : (
                    `${tasks.length} item${tasks.length === 1 ? '' : 's'}`
                  )}
                </span>
              </div>
            </div>
          </header>

          <TabNav active={tab} onChange={setTab} trailing={headerActions} />

          {tab === 'summary' && (
            <SummaryView projectId={projectId} projectContextLoading={projectContextLoading} />
          )}
          {tab === 'tasks' && (
            <ListView
              tasks={tasks}
              loading={taskListLoading}
              error={error}
              projectId={projectId}
              listBasePath={buildUrl(FLAT_TASKS_BASE)}
              initialDrawerTaskSlug={drawerTaskFromQuery}
              onOpenLinearImport={() => setLinearImportOpen(true)}
              onLinearBulkSynced={refreshTasks}
              onRefresh={refreshTasks}
            />
          )}
          {tab === 'board' && (
            <BoardView tasks={tasks} loading={taskListLoading} error={error} projectId={projectId != null ? String(projectId) : undefined} />
          )}
          {tab === 'gantt' && (
            <GanttView projectId={projectId} projectContextLoading={projectContextLoading} />
          )}
          {tab === 'insights' && <InsightsView projectId={projectId} />}
          {tab === 'my-actions' && <MyActionsView projectId={projectId} refreshKey={myActionsRefreshKey} />}
          {tab === 'planning' && (
            <PlanningView
              projectId={projectId}
              tasks={tasks}
              loading={taskListLoading}
              error={error}
            />
          )}
          {tab === 'status-reports' && <StatusReportsView projectId={projectId} />}
        </div>
        <LinearImportModal
          isOpen={linearImportOpen}
          onClose={() => setLinearImportOpen(false)}
          projectId={projectId}
          onImported={refreshTasks}
        />
        <ChatFAB />
      </DashboardLayout>
    </ProtectedRoute>
  );
}
