import type { ReactNode } from 'react';
import WorkspaceDashboard from '@/components/projects/WorkspaceDashboard';
import CustomKPIPanel from '@/components/dashboard/kpi/CustomKPIPanel';
import MeetingsCard from '@/components/overview/MeetingsCard';
import RecentActivityCard from '@/components/overview/RecentActivityCard';
import AuditCard from '@/components/overview/AuditCard';
import TeamManagementSection from '@/components/overview/TeamManagementSection';
import type { OverviewMock } from '@/types/overview';

export interface DashboardWidgetContext {
  data: OverviewMock;
  projectId: number | string;
  projectName?: string | null;
  projectSlug?: string | null;
}

/** Add a renderer here and a catalogue entry on the API to make a card available. */
export const widgetRegistry: Record<string, (context: DashboardWidgetContext) => ReactNode> = {
  workspace: ({ projectId }) => <WorkspaceDashboard projectId={projectId} />,
  'custom-kpis': ({ projectSlug }) => <CustomKPIPanel projectSlug={projectSlug} />,
  meetings: ({ data }) => <MeetingsCard upcoming={data.upcomingMeetings} actions={data.actionItems} />,
  activity: ({ data }) => <RecentActivityCard activities={data.taskSummary.recent_activity ?? []} />,
  audit: ({ data }) => <AuditCard events={data.recentAuditEvents} />,
  'project-team': ({ projectId, projectName }) => <TeamManagementSection projectId={projectId} projectName={projectName} />,
};
