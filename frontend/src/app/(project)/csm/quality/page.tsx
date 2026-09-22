'use client';

import React, { Suspense } from 'react';

import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import Layout from '@/components/layout/Layout';
import QualityInspectionView from '@/components/csm/quality/QualityInspectionView';

export default function ConversationQualityPage() {
  return (
    <ProtectedRoute requiredAuth={true} requireSupervisor={true}>
      <Layout>
        {/* The view reads filters from useSearchParams, which needs a Suspense
            boundary under the app router. */}
        <Suspense fallback={<div className="p-6 text-sm text-slate-500">Loading…</div>}>
          <QualityInspectionView />
        </Suspense>
      </Layout>
    </ProtectedRoute>
  );
}
