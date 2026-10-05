import type { ReactNode } from 'react';
import type { DashboardWidgetPosition } from '@/types/dashboardLayout';
import type { OverviewMock } from '@/types/overview';
import WorkspaceDashboardWidget, { type WorkspaceWidgetId } from '@/components/projects/WorkspaceDashboard';
import CustomKPIPanel from '@/components/dashboard/kpi/CustomKPIPanel';
import MeetingsCard from '@/components/overview/MeetingsCard';
import RecentActivityCard from '@/components/overview/RecentActivityCard';
import AuditCard from '@/components/overview/AuditCard';
import TeamManagementSection from '@/components/overview/TeamManagementSection';
import { isSectionTitle, SECTION_TITLE_ID } from './sectionTitle';

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
}

/** Add widget types here and to the API's allowed IDs; persistence uses stable IDs. */
export const widgetRegistry: WidgetDefinition[] = [
  { id: SECTION_TITLE_ID, title: 'Section title', defaultPosition: { id: SECTION_TITLE_ID, title: 'Section title', x: 0, y: 0, w: 12, h: 1 }, render: () => null },
  ...([
    ['overall-progress', 'Overall Progress', 0, 0, 4, 3],
    ['tasks-completed', 'Tasks Completed', 4, 0, 4, 3],
    ['task-completion-rate', 'Task Completion Rate', 8, 0, 4, 3],
    ['overdue-tasks', 'Overdue Tasks', 0, 4, 6, 3],
    ['needs-attention', 'Needs Attention', 6, 4, 6, 3],
    ['decisions', 'Decisions', 0, 8, 4, 7],
    ['tasks', 'Tasks', 4, 8, 4, 7],
    ['operations', 'Operations', 8, 8, 4, 7],
    ['task-status', 'Task Status Breakdown', 0, 18, 4, 5],
    ['task-priority', 'Task Priority Distribution', 4, 18, 4, 5],
    ['task-types', 'Type breakdown', 8, 18, 4, 6],
    ['task-trend', 'Tasks Created vs Completed', 0, 27, 12, 5],
  ] as const).map(([id, title, x, y, w, h]) => ({
    id, title,
    defaultPosition: { id, x, y, w, h },
    render: () => <WorkspaceDashboardWidget section={id as WorkspaceWidgetId} />,
  })),
  { id: 'custom-kpis', title: 'Custom KPIs', defaultPosition: { id: 'custom-kpis', x: 0, y: 35, w: 12, h: 4 }, render: ({ projectSlug }) => <CustomKPIPanel projectSlug={projectSlug} /> },
  { id: 'meetings', title: 'Meetings & action items', defaultPosition: { id: 'meetings', x: 0, y: 0, w: 6, h: 4 }, render: ({ data }) => <MeetingsCard upcoming={data.upcomingMeetings} actions={data.actionItems} /> },
  { id: 'activity', title: 'Recent activity', defaultPosition: { id: 'activity', x: 0, y: 0, w: 6, h: 4 }, render: ({ data }) => <RecentActivityCard activities={data.taskSummary.recent_activity} /> },
  { id: 'audit', title: 'Audit', defaultPosition: { id: 'audit', x: 0, y: 0, w: 6, h: 4 }, render: ({ data }) => <AuditCard events={data.recentAuditEvents} /> },
  { id: 'project-team', title: 'Project team', defaultPosition: { id: 'project-team', x: 0, y: 0, w: 6, h: 6 }, render: ({ projectId, projectName }) => <TeamManagementSection projectId={projectId} projectName={projectName} /> },
];

export const widgetById = Object.fromEntries(widgetRegistry.map((widget) => [widget.id, widget])) as Record<string, WidgetDefinition>;

export function getWidgetDefinition(id: string): WidgetDefinition | undefined {
  return widgetById[isSectionTitle(id) ? SECTION_TITLE_ID : id];
}

export function createWidget(definition: WidgetDefinition): DashboardWidgetPosition {
  return { ...definition.defaultPosition, id: definition.id === SECTION_TITLE_ID ? `${SECTION_TITLE_ID}-${crypto.randomUUID()}` : definition.id };
}
