'use client';

import ChatFAB from '@/components/global-chat/ChatFAB';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import DashboardBuilder from '@/components/dashboard/builder/DashboardBuilder';
import { useOverviewData } from '@/hooks/useOverviewData';
import { useProjectStore } from '@/lib/projectStore';

export default function DashboardPage() {
  const project = useProjectStore((state) => state.activeProject);
  const projectId = project?.id ?? null;
  const { data, alerts, loading, errors } = useOverviewData(projectId);

  return (
    <DashboardLayout alerts={alerts} upcomingMeetings={data.upcomingMeetings} mainClassName="dashboard-scrollbar">
      {projectId == null ? (
        <p className="rounded-lg border bg-white p-4 text-sm text-gray-600">Select a project to view its dashboard.</p>
      ) : (
        <>
          {Object.keys(errors).length > 0 && (
            <p role="status" className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              Some dashboard content could not be loaded.
            </p>
          )}
          <DashboardBuilder
            key={String(projectId)}
            data={data}
            projectId={projectId}
            projectName={project?.name}
            projectSlug={project?.slug}
            contentLoading={loading}
          />
        </>
      )}
      <ChatFAB />
    </DashboardLayout>
  );
}
