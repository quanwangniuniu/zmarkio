'use client';

import ChatFAB from '@/components/global-chat/ChatFAB';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import OverviewContent from '@/components/overview/OverviewContent';
import { WorkspaceDashboardSkeleton } from '@/components/projects/WorkspaceDashboard';
import { useOverviewData } from '@/hooks/useOverviewData';
import { useOverviewSectionHashScroll } from '@/hooks/useOverviewSectionHashScroll';
import { useProjectStore } from '@/lib/projectStore';

function ErrorBanner({ errors }: { errors: Record<string, string> }) {
  const fields = Object.keys(errors);
  if (fields.length === 0) return null;
  return (
    <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
      Some sections could not be loaded: {fields.join(', ')}. Other sections are shown normally.
    </div>
  );
}

export default function OverviewPage() {
  const activeProject = useProjectStore((s) => s.activeProject);
  const projectId = activeProject?.id ?? null;
  const { data, alerts, loading, errors } = useOverviewData(projectId);
  useOverviewSectionHashScroll(!loading && projectId != null);

  return (
    <DashboardLayout
      alerts={alerts}
      upcomingMeetings={data.upcomingMeetings}
      mainClassName="dashboard-scrollbar"
    >
      <ErrorBanner errors={errors} />
      {loading ? (
        <WorkspaceDashboardSkeleton />
      ) : (
        <OverviewContent
          data={data}
          projectId={projectId}
          projectName={activeProject?.name}
          projectSlug={activeProject?.slug}
        />
      )}
      <ChatFAB />
    </DashboardLayout>
  );
}
