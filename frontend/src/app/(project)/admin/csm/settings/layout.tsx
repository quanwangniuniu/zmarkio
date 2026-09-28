'use client';

import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import CsmSectionTabs from '@/components/csm/CsmSectionTabs';

export default function CsmSettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute requiredAuth fallback="/unauthorized">
      {/* No sub-sidebar here: the Customer Service section nav sits on top, so
          settings sits alongside Organisations, Queues and the rest. */}
      <DashboardLayout alerts={[]} upcomingMeetings={[]} mainClassName="!p-0 !space-y-0" hideRightPanel>
        <div className="flex min-h-[calc(100vh-3rem)] flex-1 flex-col bg-white">
          <div className="px-8 pt-6 max-sm:px-4">
            <CsmSectionTabs activeSection="settings" showSettings />
          </div>
          <div className="min-w-0 flex-1 bg-white">{children}</div>
        </div>
      </DashboardLayout>
    </ProtectedRoute>
  );
}
