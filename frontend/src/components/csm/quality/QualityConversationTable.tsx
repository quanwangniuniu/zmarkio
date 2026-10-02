'use client';

import React from 'react';
import { QualityConversationRow } from '@/types/csmQuality';
import QualityRatingBadge from './QualityRatingBadge';
import { formatDateTime } from './formatDates';

interface QualityConversationTableProps {
  rows: QualityConversationRow[];
  loading: boolean;
  onSelect: (row: QualityConversationRow) => void;
}

/** tags[0] doubles as the conversation subject; the rest are real tags. */
function subjectOf(row: QualityConversationRow): string {
  const tags = Array.isArray(row.tags) ? row.tags : [];
  return tags[0] || `Conversation #${row.id}`;
}

export function QualityConversationTable({
  rows,
  loading,
  onSelect,
}: QualityConversationTableProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-3" aria-busy="true" aria-label="Loading conversations">
        {[0, 1, 2, 3, 4].map((key) => (
          <div key={key} className="h-10 animate-pulse rounded bg-slate-100" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="p-10 text-center">
        <p className="text-sm font-medium text-slate-700">No conversations match these filters</p>
        <p className="mt-1 text-sm text-slate-500">
          Widen the date range, or clear a filter to see more.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>
            <th scope="col" className="px-3 py-2">Conversation</th>
            <th scope="col" className="px-3 py-2">Agent</th>
            <th scope="col" className="px-3 py-2">Queue</th>
            <th scope="col" className="px-3 py-2">Channel</th>
            <th scope="col" className="px-3 py-2">Status</th>
            <th scope="col" className="px-3 py-2">Started</th>
            <th scope="col" className="px-3 py-2">My rating</th>
            <th scope="col" className="px-3 py-2 text-right">Reviews</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((row) => (
            <tr
              key={row.id}
              onClick={() => onSelect(row)}
              className="cursor-pointer hover:bg-slate-50"
            >
              <td className="px-3 py-2">
                <button
                  type="button"
                  className="text-left font-medium text-slate-800 hover:text-[#1a9ba3]"
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(row);
                  }}
                >
                  {subjectOf(row)}
                </button>
                <p className="text-xs text-slate-500">{row.customer_name || 'Unknown customer'}</p>
              </td>
              <td className="px-3 py-2 text-slate-600">{row.assigned_to_name || 'Unassigned'}</td>
              <td className="px-3 py-2 text-slate-600">{row.queue_name || '—'}</td>
              <td className="px-3 py-2 text-slate-600">{row.channel_display}</td>
              <td className="px-3 py-2 text-slate-600">{row.status_display}</td>
              <td className="px-3 py-2 text-slate-600">{formatDateTime(row.started_at)}</td>
              <td className="px-3 py-2">
                <QualityRatingBadge rating={row.my_review?.rating ?? null} />
              </td>
              <td className="px-3 py-2 text-right text-slate-600">{row.review_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default QualityConversationTable;
