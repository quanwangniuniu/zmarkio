'use client';

import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';

export default function CsmSettingsLayout({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute requiredAuth fallback="/unauthorized">
      {/* The main dashboard sidebar is the only navigation here: the settings
          pages are reached from the hub's cards, and the right-hand panel is
          noise on a settings screen. */}
      <DashboardLayout alerts={[]} upcomingMeetings={[]} mainClassName="!p-0 !space-y-0" hideRightPanel>
        <div className="flex min-h-[calc(100vh-3rem)] flex-1 bg-white">
          <div className="min-w-0 flex-1 bg-white">{children}</div>
        </div>
      </DashboardLayout>
    </ProtectedRoute>
  );
}
