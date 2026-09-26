'use client';

import React from 'react';
import { QualityReportAgentRow } from '@/types/csmQuality';

interface QualityReportAgentTableProps {
  rows: QualityReportAgentRow[];
}

export function QualityReportAgentTable({ rows }: QualityReportAgentTableProps) {
  if (rows.length === 0) {
    return <p className="p-4 text-sm text-slate-500">No annotations for these filters yet.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Annotation counts per agent</caption>
        <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="px-3 py-2">Agent</th>
            <th scope="col" className="px-3 py-2 text-right">Total</th>
            <th scope="col" className="px-3 py-2 text-right">Good</th>
            <th scope="col" className="px-3 py-2 text-right">Needs Improvement</th>
            <th scope="col" className="px-3 py-2 text-right">Poor</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr key={row.agent_user_id ?? 'unassigned'}>
              <th scope="row" className="px-3 py-2 text-left font-medium text-slate-700">
                {row.agent_name}
              </th>
              <td className="px-3 py-2 text-right text-slate-700">{row.total}</td>
              <td className="px-3 py-2 text-right text-emerald-700">{row.good}</td>
              <td className="px-3 py-2 text-right text-amber-700">{row.needs_improvement}</td>
              <td className="px-3 py-2 text-right text-rose-700">{row.poor}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default QualityReportAgentTable;
