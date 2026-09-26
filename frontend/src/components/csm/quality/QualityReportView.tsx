'use client';

import React from 'react';
import { QualityReport } from '@/types/csmQuality';
import { RATING_CLASSES } from './QualityRatingBadge';
import QualityReportAgentTable from './QualityReportAgentTable';
import QualityReportTrendChart from './QualityReportTrendChart';

interface QualityReportViewProps {
  report: QualityReport | null;
  loading: boolean;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <h3 className="border-b border-slate-200 px-4 py-2.5 text-sm font-semibold text-slate-700">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function QualityReportView({ report, loading }: QualityReportViewProps) {
  if (loading) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading report">
        {[0, 1, 2].map((key) => (
          <div key={key} className="h-32 animate-pulse rounded-lg bg-slate-100" />
        ))}
      </div>
    );
  }

  if (!report) return null;

  const { totals } = report;

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {report.by_rating.map((row) => (
          <div
            key={row.rating}
            className={`rounded-lg px-4 py-3 ring-1 ring-inset ${RATING_CLASSES[row.rating]}`}
          >
            <p className="text-xs font-medium">{row.rating_display}</p>
            <p className="mt-1 text-2xl font-semibold">{row.count}</p>
            <p className="text-xs opacity-80">{row.pct}% of annotations</p>
          </div>
        ))}
        <div className="rounded-lg bg-slate-50 px-4 py-3 ring-1 ring-inset ring-slate-500/20">
          <p className="text-xs font-medium text-slate-600">Coverage</p>
          <p className="mt-1 text-2xl font-semibold text-slate-800">{totals.coverage_pct}%</p>
          <p className="text-xs text-slate-500">
            {totals.conversations_reviewed} of {totals.conversations_in_scope} conversations
            reviewed
          </p>
        </div>
      </div>

      <Card title={`By agent — ${totals.reviews} annotation${totals.reviews === 1 ? '' : 's'}`}>
        <QualityReportAgentTable rows={report.by_agent} />
      </Card>

      <Card title="Over time">
        <QualityReportTrendChart rows={report.by_date} bucket={report.filters_echo.bucket} />
      </Card>
    </div>
  );
}

export default QualityReportView;
