'use client';

import DashboardBuilder from '@/components/dashboard/builder/DashboardBuilder';
import type { OverviewMock } from '@/types/overview';

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
    <div>
      {projectId ? (
        <DashboardBuilder key={String(projectId)} data={data} projectId={projectId} projectName={projectName} projectSlug={projectSlug} />
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-500">No active project selected.</div>
      )}
    </div>
  );
}
