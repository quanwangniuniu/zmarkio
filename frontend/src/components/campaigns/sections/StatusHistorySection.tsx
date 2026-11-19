'use client';

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
} from 'react';
import { ArrowRight } from 'lucide-react';

import { useCampaignStatusHistory } from '@/hooks/campaigns/useCampaignStatusHistory';
import { CAMPAIGN_STATUS } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionLabel } from '@/lib/orgCustomization/optionConfig';

export interface StatusHistorySectionProps {
  campaignId: string;
}

export interface StatusHistorySectionHandle {
  refresh: () => Promise<void>;
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });
}

function formatFullTimestamp(timestamp: string): string {
  const date = new Date(timestamp);
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

const StatusHistorySection = forwardRef<StatusHistorySectionHandle, StatusHistorySectionProps>(
  function StatusHistorySection({ campaignId }, ref) {
    useOptionConfigVersion();
    const { items, loading, error, refresh } = useCampaignStatusHistory(campaignId);

    useEffect(() => {
      if (!campaignId) return;
      void refresh();
    }, [campaignId, refresh]);

    useImperativeHandle(
      ref,
      () => ({
        refresh: async () => {
          await refresh();
        },
      }),
      [refresh]
    );

    const errorMessage = useMemo(() => {
      if (!error) return null;
      const anyErr = error as any;
      return anyErr?.response?.data?.error || anyErr?.message || 'Failed to load status history';
    }, [error]);

    const renderHeader = (count: number | null) => (
      <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-gray-900">
        Status History
        {count !== null && count > 0 && (
          <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">{count}</span>
        )}
      </h2>
    );

    if (loading && items.length === 0) {
      return (
        <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          {renderHeader(null)}
          <div className="flex items-center justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-b-2 border-[#3CCED7]" />
            <span className="ml-3 text-sm text-gray-600">Loading status history...</span>
          </div>
        </section>
      );
    }

    if (errorMessage && items.length === 0) {
      return (
        <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          {renderHeader(null)}
          <div className="rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
            {errorMessage}
          </div>
        </section>
      );
    }

    if (items.length === 0) {
      return (
        <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          {renderHeader(0)}
          <div className="py-8 text-center text-sm text-gray-500">
            No status changes recorded yet.
          </div>
        </section>
      );
    }

    return (
      <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        {renderHeader(items.length)}

        <div className="relative">
          <div className="absolute bottom-0 left-4 top-0 w-0.5 bg-gray-300" />
          <div className="space-y-3">
            {items.map((item) => (
              <div key={item.id} className="relative flex gap-4 pl-2">
                <div className="relative z-10">
                  <div className="mt-2 h-2 w-2 flex-shrink-0 rounded-full bg-gray-400" />
                </div>
                <div className="flex-1 pb-3">
                  <div className="flex items-center gap-3">
                    <div className="flex flex-1 items-center gap-2">
                      <div className="min-w-[100px]">
                        <span className="text-sm font-normal text-gray-600">
                          {optionLabel(CAMPAIGN_STATUS, item.from_status)}
                        </span>
                      </div>
                      <ArrowRight className="h-4 w-4 flex-shrink-0 text-gray-400" />
                      <div className="min-w-[100px]">
                        <span className="text-sm font-semibold text-gray-900">
                          {optionLabel(CAMPAIGN_STATUS, item.to_status)}
                        </span>
                      </div>
                    </div>
                    <div className="flex-shrink-0 text-right">
                      <div
                        className="text-xs text-gray-500"
                        title={formatFullTimestamp(item.created_at)}
                      >
                        {formatTimestamp(item.created_at)}
                      </div>
                    </div>
                  </div>
                  {item.note && (
                    <p className="mt-2 text-sm italic text-gray-600">{item.note}</p>
                  )}
                  {item.changed_by && (
                    <p className="mt-1 text-xs text-gray-400">
                      by {item.changed_by.username || item.changed_by.email}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    );
  }
);

export default StatusHistorySection;
