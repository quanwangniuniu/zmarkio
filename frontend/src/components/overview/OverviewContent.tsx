'use client';

import MeetingsCard from './MeetingsCard';
import RecentActivityCard from './RecentActivityCard';
import TeamManagementSection from './TeamManagementSection';
import WorkspaceDashboard from '@/components/projects/WorkspaceDashboard';
import CustomKPIPanel from '@/components/dashboard/kpi/CustomKPIPanel';
import type { OverviewMock } from '@/types/overview';
import AuditCard from './AuditCard';

interface OverviewContentProps {
  data: OverviewMock;
  projectId: number | string | null;
  projectName?: string | null;
  projectSlug?: string | null;
}

export default function OverviewContent({
  data,
  projectId,
  projectName,
  projectSlug,
}: OverviewContentProps) {
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div className="xl:col-span-2">
        {projectId ? (
          <WorkspaceDashboard projectId={projectId} />
        ) : (
          <div className="rounded-xl border-[0.5px] border-gray-200 bg-white px-4 py-3 text-sm text-gray-500">
            No active project selected.
          </div>
        )}
      </div>
      <div className="xl:col-span-2 scroll-mt-24" id="custom-kpis">
        <CustomKPIPanel projectSlug={projectSlug} />
      </div>
      <MeetingsCard upcoming={data.upcomingMeetings} actions={data.actionItems} />
      <RecentActivityCard activities={data.taskSummary.recent_activity} />
      <AuditCard events={data.recentAuditEvents} />
      <div className="xl:col-span-2 scroll-mt-24" id="project-team">
        <TeamManagementSection projectId={projectId} projectName={projectName} />
      </div>
    </div>
  );
}
