'use client';

import ChatFAB from '@/components/global-chat/ChatFAB';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import OverviewContent from '@/components/overview/OverviewContent';
import { useOverviewData } from '@/hooks/useOverviewData';
import { useOverviewSectionHashScroll } from '@/hooks/useOverviewSectionHashScroll';
import { useProjectStore } from '@/lib/projectStore';

function OverviewSkeleton() {
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-72 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100"
        >
          <div className="mb-4 h-4 w-32 animate-pulse rounded bg-gray-100" />
          <div className="space-y-2">
            <div className="h-3 w-full animate-pulse rounded bg-gray-100" />
            <div className="h-3 w-5/6 animate-pulse rounded bg-gray-100" />
            <div className="h-3 w-4/6 animate-pulse rounded bg-gray-100" />
            <div className="h-3 w-3/6 animate-pulse rounded bg-gray-100" />
          </div>
        </div>
      ))}
    </div>
  );
}

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
        <OverviewSkeleton />
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
