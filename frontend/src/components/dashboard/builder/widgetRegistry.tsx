import type { ReactNode } from 'react';
import type { DashboardLayoutConfiguration, DashboardWidgetPosition } from '@/types/dashboardLayout';
import type { OverviewMock } from '@/types/overview';
import WorkspaceDashboardWidget, { isWorkspaceWidgetId, type WorkspaceWidgetId } from '@/components/projects/WorkspaceDashboard';
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
}

/** Renderers stay in React; presets, allowed types and limits come from the API. */
export function createWidgetRegistry(configuration: DashboardLayoutConfiguration) {
  const renderers: Record<string, WidgetDefinition['render']> = {
    'custom-kpis': ({ projectSlug }) => <CustomKPIPanel projectSlug={projectSlug} />,
    meetings: ({ data }) => <MeetingsCard upcoming={data.upcomingMeetings} actions={data.actionItems} />,
    activity: ({ data }) => <RecentActivityCard activities={data.taskSummary.recent_activity} />,
    audit: ({ data }) => <AuditCard events={data.recentAuditEvents} />,
    'project-team': ({ projectId, projectName }) => <TeamManagementSection projectId={projectId} projectName={projectName} />,
  };
  const widgets: WidgetDefinition[] = configuration.widgets
      .filter((preset) => isWorkspaceWidgetId(preset.id) || Boolean(renderers[preset.id]))
      .map(({ id, label, x, y, w, h, add_x }) => ({
        id, title: label, defaultPosition: { id, x: add_x ?? x, y, w, h },
        render: renderers[id] ?? (() => <WorkspaceDashboardWidget section={id as WorkspaceWidgetId} />),
      }));
  const byId = Object.fromEntries(widgets.map((widget) => [widget.id, widget]));
  const getWidgetDefinition = (id: string): WidgetDefinition | undefined =>
    byId[id];
  const createWidget = (definition: WidgetDefinition): DashboardWidgetPosition => ({
    ...definition.defaultPosition,
  });
  return { widgets, getWidgetDefinition, createWidget };
}
