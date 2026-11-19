'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
} from 'react';
import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  CheckCircle,
  Image as ImageIcon,
  Minus,
} from 'lucide-react';

import UserAvatar from '@/people/UserAvatar';
import CampaignStatusPill from '@/components/campaigns/pills/CampaignStatusPill';
import { Badge } from '@/components/ui/badge';
import { useCampaignActivity } from '@/hooks/campaigns/useCampaignActivity';
import type { CampaignActivityTimelineItem, CampaignStatus } from '@/types/campaign';
import { CHECKIN_SENTIMENT, SNAPSHOT_METRIC, SNAPSHOT_MILESTONE } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle, optionLabel } from '@/lib/orgCustomization/optionConfig';

export interface TimelineSectionProps {
  campaignId: string;
}

export interface TimelineSectionHandle {
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

function renderEventIcon(item: CampaignActivityTimelineItem) {
  if (item.type === 'status_change') {
    return (
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-[#3CCED7]/15">
        <ArrowRight className="h-4 w-4 text-[#0E8A96]" />
      </div>
    );
  }
  if (item.type === 'check_in') {
    const sentiment = item.details.sentiment?.toUpperCase();
    if (sentiment === 'POSITIVE') {
      return (
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-green-100">
          <CheckCircle className="h-4 w-4 text-green-600" />
        </div>
      );
    }
    if (sentiment === 'NEGATIVE') {
      return (
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-red-100">
          <AlertCircle className="h-4 w-4 text-red-600" />
        </div>
      );
    }
    return (
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gray-100">
        <Minus className="h-4 w-4 text-gray-600" />
      </div>
    );
  }
  if (item.type === 'performance_snapshot') {
    return (
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-purple-100">
        <BarChart3 className="h-4 w-4 text-purple-600" />
      </div>
    );
  }
  return null;
}

function renderEventContent(item: CampaignActivityTimelineItem) {
  const userName = item.user?.username || item.user?.email || 'Unknown';

  if (item.type === 'status_change') {
    return (
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-gray-900">
            <span className="font-medium">{userName}</span> changed status
          </span>
          {item.details.from_status && (
            <CampaignStatusPill status={item.details.from_status as CampaignStatus} />
          )}
          <ArrowRight className="h-4 w-4 text-gray-400" />
          {item.details.to_status && (
            <CampaignStatusPill status={item.details.to_status as CampaignStatus} />
          )}
        </div>
        {item.details.note && (
          <p className="mt-1 text-sm text-gray-600">{item.details.note}</p>
        )}
      </div>
    );
  }

  if (item.type === 'check_in') {
    const sentiment = item.details.sentiment?.toUpperCase();
    const sentimentColor =
      sentiment === 'POSITIVE'
        ? 'bg-green-100 text-green-800'
        : sentiment === 'NEGATIVE'
        ? 'bg-red-100 text-red-800'
        : 'bg-gray-100 text-gray-800';

    return (
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-gray-900">
            <span className="font-medium">{userName}</span> checked in
          </span>
          <Badge
            variant="outline"
            className={`text-xs ${sentimentColor}`}
            style={sentiment ? optionColorStyle(CHECKIN_SENTIMENT.colors[sentiment]) : undefined}
          >
            {optionLabel(CHECKIN_SENTIMENT, sentiment) || 'Neutral'}
          </Badge>
        </div>
        {item.details.note && (
          <p className="mt-1 text-sm text-gray-600">{item.details.note}</p>
        )}
      </div>
    );
  }

  if (item.type === 'performance_snapshot') {
    const pct = item.details.percentage_change;
    return (
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-sm text-gray-900">
            <span className="font-medium">{userName}</span> recorded performance snapshot
          </span>
          {item.details.milestone_type && (
            <Badge variant="outline" className="text-xs">
              {optionLabel(SNAPSHOT_MILESTONE, item.details.milestone_type)}
            </Badge>
          )}
        </div>
        <div className="mt-2 flex flex-wrap gap-4 text-sm text-gray-600">
          {item.details.spend && (
            <span>
              <span className="font-medium">Spend:</span> ${item.details.spend}
            </span>
          )}
          {item.details.metric_type && item.details.metric_value && (
            <span>
              <span className="font-medium">{optionLabel(SNAPSHOT_METRIC, item.details.metric_type)}:</span>{' '}
              {item.details.metric_value}
            </span>
          )}
          {pct && (
            <span className={pct.startsWith('-') ? 'text-red-600' : 'text-green-600'}>
              <span className="font-medium">Change:</span> {pct}%
            </span>
          )}
        </div>
        {item.details.notes && (
          <p className="mt-2 text-sm text-gray-600">{item.details.notes}</p>
        )}
        {item.details.screenshot_url && (
          <div className="mt-2">
            <a
              href={item.details.screenshot_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm text-[#0E8A96] hover:text-[#0A6F78]"
            >
              <ImageIcon className="h-4 w-4" />
              View screenshot
            </a>
          </div>
        )}
      </div>
    );
  }

  return null;
}

const TimelineSection = forwardRef<TimelineSectionHandle, TimelineSectionProps>(
  function TimelineSection({ campaignId }, ref) {
    useOptionConfigVersion();
    const {
      items,
      page,
      pageSize,
      totalCount,
      loading,
      error,
      load,
    } = useCampaignActivity(campaignId);

    const totalPages = useMemo(
      () => (totalCount > 0 ? Math.ceil(totalCount / pageSize) : 0),
      [totalCount, pageSize]
    );

    useEffect(() => {
      if (!campaignId) return;
      void load({ page: 1 });
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [campaignId]);

    useImperativeHandle(
      ref,
      () => ({
        refresh: async () => {
          await load({ page });
        },
      }),
      [load, page]
    );

    const handlePageChange = useCallback(
      async (newPage: number) => {
        if (newPage < 1 || newPage > totalPages) return;
        await load({ page: newPage });
        window.scrollTo({ top: 0, behavior: 'smooth' });
      },
      [load, totalPages]
    );

    const errorMessage = useMemo(() => {
      if (!error) return null;
      const anyErr = error as any;
      return anyErr?.response?.data?.error || anyErr?.message || 'Failed to load activity timeline';
    }, [error]);

    const renderHeader = (count: number | null) => (
      <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-gray-900">
        Activity Timeline
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
            <span className="ml-3 text-sm text-gray-600">Loading timeline...</span>
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
          <div className="py-8 text-center text-sm text-gray-500">No activity recorded yet.</div>
        </section>
      );
    }

    const startIndex = (page - 1) * pageSize + 1;
    const endIndex = Math.min(page * pageSize, totalCount);

    return (
      <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        {renderHeader(totalCount)}

        <div className="relative">
          <div className="absolute bottom-0 left-4 top-0 w-0.5 bg-gray-200" />
          <div className="space-y-4">
            {items.map((item) => {
              const userName = item.user?.username || item.user?.email || 'Unknown';
              const userForAvatar = item.user
                ? { name: userName, email: item.user.email }
                : null;
              return (
                <div key={item.id} className="relative flex gap-4 pl-2">
                  <div className="relative z-10">{renderEventIcon(item)}</div>
                  <div className="flex-1 pb-4">
                    <div className="flex items-start gap-3">
                      {userForAvatar && <UserAvatar user={userForAvatar} size="sm" />}
                      {renderEventContent(item)}
                      <div
                        className="flex-shrink-0 text-right text-xs text-gray-500"
                        title={formatFullTimestamp(item.timestamp)}
                      >
                        {formatTimestamp(item.timestamp)}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {totalPages > 1 && (
          <div className="mt-6 border-t border-gray-200 pt-4">
            <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
              <div className="text-sm text-gray-700">
                Showing <span className="font-medium">{startIndex}</span> to{' '}
                <span className="font-medium">{endIndex}</span> of{' '}
                <span className="font-medium">{totalCount}</span> activities
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handlePageChange(page - 1)}
                  disabled={page === 1 || loading}
                  className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white"
                >
                  Previous
                </button>

                <div className="flex items-center gap-1">
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    let pageNum: number;
                    if (totalPages <= 5) {
                      pageNum = i + 1;
                    } else if (page <= 3) {
                      pageNum = i + 1;
                    } else if (page >= totalPages - 2) {
                      pageNum = totalPages - 4 + i;
                    } else {
                      pageNum = page - 2 + i;
                    }

                    const isCurrent = pageNum === page;
                    return (
                      <button
                        key={pageNum}
                        type="button"
                        onClick={() => handlePageChange(pageNum)}
                        className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                          isCurrent
                            ? 'bg-[#3CCED7] text-white'
                            : 'border border-gray-300 bg-white text-gray-700 hover:bg-gray-50'
                        }`}
                      >
                        {pageNum}
                      </button>
                    );
                  })}
                </div>

                <button
                  type="button"
                  onClick={() => handlePageChange(page + 1)}
                  disabled={page === totalPages || loading}
                  className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white"
                >
                  Next
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    );
  }
);

export default TimelineSection;
