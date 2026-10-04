import type { ReactNode } from 'react';
import type { DashboardWidgetPosition } from '@/types/dashboardLayout';
import type { OverviewMock } from '@/types/overview';
import WorkspaceDashboardWidget, { type WorkspaceWidgetId } from '@/components/projects/WorkspaceDashboard';
import CustomKPIPanel from '@/components/dashboard/kpi/CustomKPIPanel';
import MeetingsCard from '@/components/overview/MeetingsCard';
import RecentActivityCard from '@/components/overview/RecentActivityCard';
import AuditCard from '@/components/overview/AuditCard';
import TeamManagementSection from '@/components/overview/TeamManagementSection';

export interface WidgetContext {
  data: OverviewMock;
  projectId: number | string;
  projectName?: string | null;
  projectSlug?: string | null;
}

export interface WidgetDefinition {
  id: string;
  title: string;
  defaultPosition: DashboardWidgetPosition;
  render: (context: WidgetContext) => ReactNode;
  legacy?: boolean;
  group?: string;
}

/** Add widget types here and to the API's allowed IDs; persistence uses stable IDs. */
export const widgetRegistry: WidgetDefinition[] = [
  ...([
    ['overall-progress', 'Overall Progress', 0, 0, 4, 4],
    ['tasks-completed', 'Tasks Completed', 4, 0, 4, 4],
    ['task-completion-rate', 'Task Completion Rate', 8, 0, 4, 4],
    ['overdue-tasks', 'Overdue Tasks', 0, 4, 6, 4],
    ['needs-attention', 'Needs Attention', 6, 4, 6, 4],
    ['decisions', 'Decisions', 0, 8, 4, 10],
    ['tasks', 'Tasks', 4, 8, 4, 10],
    ['operations', 'Operations', 8, 8, 4, 10],
    ['task-status', 'Task Status Breakdown', 0, 18, 6, 9],
    ['task-priority', 'Task Priority Distribution', 6, 18, 6, 9],
    ['task-trend', 'Tasks Created vs Completed', 0, 27, 12, 8],
  ] as const).map(([id, title, x, y, w, h]) => ({
    id, title, group: ['overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks', 'needs-attention'].includes(id) ? 'Project Overview' : ['decisions', 'tasks', 'operations'].includes(id) ? 'Module Summary' : 'Tasks',
    defaultPosition: { id, x, y, w, h },
    render: () => <WorkspaceDashboardWidget section={id as WorkspaceWidgetId} />,
  })),
  { id: 'custom-kpis', title: 'Custom KPIs', defaultPosition: { id: 'custom-kpis', x: 0, y: 35, w: 12, h: 7 }, render: ({ projectSlug }) => <CustomKPIPanel projectSlug={projectSlug} /> },
  { id: 'meetings', title: 'Meetings & action items', defaultPosition: { id: 'meetings', x: 0, y: 0, w: 6, h: 8 }, render: ({ data }) => <MeetingsCard upcoming={data.upcomingMeetings} actions={data.actionItems} /> },
  { id: 'activity', title: 'Recent activity', defaultPosition: { id: 'activity', x: 0, y: 0, w: 6, h: 8 }, render: ({ data }) => <RecentActivityCard activities={data.taskSummary.recent_activity} /> },
  { id: 'audit', title: 'Audit', defaultPosition: { id: 'audit', x: 0, y: 0, w: 6, h: 8 }, render: ({ data }) => <AuditCard events={data.recentAuditEvents} /> },
  { id: 'project-team', title: 'Project team', defaultPosition: { id: 'project-team', x: 0, y: 0, w: 6, h: 10 }, render: ({ projectId, projectName }) => <TeamManagementSection projectId={projectId} projectName={projectName} /> },
  // Old persisted layouts may still contain this ID until the API is restarted.
  // Render it rather than showing the unavailable-widget fallback during rollout.
  { id: 'workspace', title: 'Workspace', legacy: true, defaultPosition: { id: 'workspace', x: 0, y: 0, w: 12, h: 12 }, render: () => (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {(['overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks', 'needs-attention', 'decisions', 'tasks', 'operations', 'task-status', 'task-priority', 'task-trend'] as WorkspaceWidgetId[]).map((section) => <WorkspaceDashboardWidget key={section} section={section} />)}
    </div>
  ) },
];

export const widgetById = Object.fromEntries(widgetRegistry.map((widget) => [widget.id, widget])) as Record<string, WidgetDefinition>;
