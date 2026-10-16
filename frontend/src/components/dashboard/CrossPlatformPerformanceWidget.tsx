'use client';

import { useEffect, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import ReportAPI from '@/lib/api/reportApi';
import type {
  CrossPlatformKPI,
  CrossPlatformReport,
  CrossPlatformReportRow,
} from '@/types/report';

const KPI_OPTIONS: { key: CrossPlatformKPI; label: string }[] = [
  { key: 'cpm', label: 'CPM' },
  { key: 'cpc', label: 'CPC' },
  { key: 'ctr', label: 'CTR' },
  { key: 'roas', label: 'ROAS' },
];

const PLATFORM_LABELS: Record<string, string> = {
  meta: 'Meta',
  google: 'Google',
  tiktok: 'TikTok',
};

function chartRows(rows: CrossPlatformReportRow[], metric: CrossPlatformKPI) {
  return rows.map((row) => ({
    platform: PLATFORM_LABELS[row.platform] ?? row.platform,
    value: row[metric] === null ? null : Number(row[metric]),
    status: row.status,
  }));
}

export default function CrossPlatformPerformanceWidget({
  projectSlug,
}: {
  projectSlug?: string | null;
}) {
  const [metric, setMetric] = useState<CrossPlatformKPI>('cpm');
  const [report, setReport] = useState<CrossPlatformReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    let active = true;
    if (!projectSlug) {
      setReport(null);
      return;
    }
    setLoading(true);
    setError(false);
    ReportAPI.getCrossPlatformReport(projectSlug)
      .then((response) => {
        if (active) setReport(response.data);
      })
      .catch(() => {
        if (active) setError(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectSlug]);

  const data = chartRows(report?.rows ?? [], metric);

  return (
    <section
      className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100"
      data-testid="cross-platform-performance"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">Cross-platform performance</h2>
          {report && (
            <p className="text-xs text-gray-500">
              {report.start_date} to {report.end_date}
            </p>
          )}
        </div>
        <div className="flex rounded-lg bg-gray-100 p-1" aria-label="Performance metric">
          {KPI_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={metric === option.key}
              className={`rounded-md px-3 py-1 text-xs font-medium ${
                metric === option.key
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500'
              }`}
              onClick={() => setMetric(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex h-56 items-center justify-center text-sm text-gray-500">Loading report…</div>
      ) : error ? (
        <div className="flex h-56 items-center justify-center text-sm text-red-600">Report could not be loaded.</div>
      ) : !projectSlug ? (
        <div className="flex h-56 items-center justify-center text-sm text-gray-500">Select a project to view performance.</div>
      ) : (
        <>
          <div className="h-56" data-testid="cross-platform-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="platform" />
                <YAxis />
                <Tooltip />
                <Bar dataKey="value" name={metric.toUpperCase()} fill="#3CCED7" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
            {data.map((row) => (
              <div key={row.platform} data-testid={`platform-${row.platform.toLowerCase()}`}>
                <div className="font-medium text-gray-700">{row.platform}</div>
                <div className="text-gray-500">
                  {row.value === null ? 'No data' : row.value.toLocaleString()}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
