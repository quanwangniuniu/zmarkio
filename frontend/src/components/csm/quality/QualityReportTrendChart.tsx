'use client';

import React from 'react';
import { QualityBucket, QualityReportDateRow } from '@/types/csmQuality';

interface QualityReportTrendChartProps {
  rows: QualityReportDateRow[];
  bucket: QualityBucket;
}

const SEGMENTS: { key: 'good' | 'needs_improvement' | 'poor'; className: string; label: string }[] = [
  { key: 'good', className: 'bg-emerald-500', label: 'Good' },
  { key: 'needs_improvement', className: 'bg-amber-500', label: 'Needs Improvement' },
  { key: 'poor', className: 'bg-rose-500', label: 'Poor' },
];

/**
 * Stacked bars per bucket. Plain Tailwind rather than recharts: the series is
 * three fixed categories over a handful of buckets, so a chart library would
 * cost more than it explains.
 */
export function QualityReportTrendChart({ rows, bucket }: QualityReportTrendChartProps) {
  if (rows.length === 0) {
    return <p className="p-4 text-sm text-slate-500">No annotations in this period.</p>;
  }

  const max = Math.max(...rows.map((row) => row.total), 1);

  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap gap-3">
        {SEGMENTS.map((segment) => (
          <span key={segment.key} className="flex items-center gap-1.5 text-xs text-slate-600">
            <span className={`h-2.5 w-2.5 rounded-sm ${segment.className}`} />
            {segment.label}
          </span>
        ))}
        <span className="ml-auto text-xs text-slate-400">Grouped by {bucket}</span>
      </div>

      <ul className="space-y-1.5">
        {rows.map((row) => (
          <li key={row.bucket ?? 'unknown'} className="flex items-center gap-2">
            <span className="w-24 shrink-0 text-xs text-slate-500">{row.bucket ?? '—'}</span>
            <div className="flex h-5 flex-1 overflow-hidden rounded bg-slate-100">
              {SEGMENTS.map((segment) => {
                const value = row[segment.key];
                if (!value) return null;
                return (
                  <div
                    key={segment.key}
                    className={segment.className}
                    style={{ width: `${(value / max) * 100}%` }}
                    title={`${segment.label}: ${value}`}
                  />
                );
              })}
            </div>
            <span className="w-8 shrink-0 text-right text-xs text-slate-600">{row.total}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default QualityReportTrendChart;
