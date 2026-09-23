'use client';

import React, { Suspense } from 'react';

import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import QualityInspectionView from '@/components/csm/quality/QualityInspectionView';

export default function ConversationQualityPage() {
  return (
    <ProtectedRoute requiredAuth={true} requireSupervisor={true}>
      {/* DashboardLayout, matching /csm, /csm/conversations and the rest of the
          CSM area. hideRightPanel as the workspace does: this page is wide. */}
      <DashboardLayout hideRightPanel>
        {/* The view reads filters from useSearchParams, which needs a Suspense
            boundary under the app router. */}
        <Suspense fallback={<div className="p-6 text-sm text-slate-500">Loading…</div>}>
          <QualityInspectionView />
        </Suspense>
      </DashboardLayout>
    </ProtectedRoute>
  );
}
