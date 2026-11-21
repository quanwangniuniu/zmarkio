'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react';
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronUp,
  Edit,
  Minus,
  Paperclip,
  Plus,
  Trash2,
} from 'lucide-react';
import toast from 'react-hot-toast';

import UserAvatar from '@/people/UserAvatar';
import BrandButton from '@/components/campaigns/BrandButton';
import BrandConfirmDialog from '@/components/campaigns/BrandConfirmDialog';
import { useCampaignSnapshots } from '@/hooks/campaigns/useCampaignSnapshots';
import type { PerformanceSnapshot } from '@/types/campaign';
import { SNAPSHOT_METRIC, SNAPSHOT_MILESTONE } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionLabel } from '@/lib/orgCustomization/optionConfig';

export interface SnapshotsSectionProps {
  campaignId: string;
  isArchived?: boolean;
  onCreate: () => void;
  onEdit: (snapshot: PerformanceSnapshot) => void;
}

export interface SnapshotsSectionHandle {
  refresh: () => Promise<void>;
}

type TrendPalette = {
  icon: typeof ArrowUp | typeof ArrowDown | typeof Minus;
  pillClass: string;
  iconClass: string;
  label: string;
};

function getTrendPalette(percentageChange: string | null): TrendPalette {
  if (percentageChange === null || percentageChange === undefined) {
    return {
      icon: Minus,
      pillClass: 'bg-gray-100 text-gray-600 border-gray-200',
      iconClass: 'text-gray-600',
      label: 'Stable',
    };
  }
  const change = parseFloat(percentageChange);
  if (change > 0) {
    return {
      icon: ArrowUp,
      pillClass: 'bg-green-100 text-green-600 border-green-200',
      iconClass: 'text-green-600',
      label: `+${change.toFixed(1)}%`,
    };
  }
  if (change < 0) {
    return {
      icon: ArrowDown,
      pillClass: 'bg-red-100 text-red-600 border-red-200',
      iconClass: 'text-red-600',
      label: `${change.toFixed(1)}%`,
    };
  }
  return {
    icon: Minus,
    pillClass: 'bg-gray-100 text-gray-600 border-gray-200',
    iconClass: 'text-gray-600',
    label: 'Stable',
  };
}

function formatCurrency(value: string | number): string {
  const numValue = typeof value === 'string' ? parseFloat(value) : value;
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(numValue);
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

const SnapshotsSection = forwardRef<SnapshotsSectionHandle, SnapshotsSectionProps>(
  function SnapshotsSection({ campaignId, isArchived = false, onCreate, onEdit }, ref) {
    useOptionConfigVersion();
    const { items, loading, error, refresh, remove } = useCampaignSnapshots(campaignId);
    const [expandedSnapshots, setExpandedSnapshots] = useState<Set<string>>(new Set());
    const [hoveredRow, setHoveredRow] = useState<string | null>(null);
    const [confirmDeleteTarget, setConfirmDeleteTarget] = useState<PerformanceSnapshot | null>(null);
    const [deleting, setDeleting] = useState(false);

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

    const toggleExpand = useCallback((snapshotId: string) => {
      setExpandedSnapshots((prev) => {
        const next = new Set(prev);
        if (next.has(snapshotId)) next.delete(snapshotId);
        else next.add(snapshotId);
        return next;
      });
    }, []);

    const errorMessage = useMemo(() => {
      if (!error) return null;
      const anyErr = error as any;
      return anyErr?.response?.data?.error || anyErr?.message || 'Failed to load snapshots';
    }, [error]);

    const handleConfirmDelete = useCallback(async () => {
      if (!confirmDeleteTarget) return;
      const target = confirmDeleteTarget;
      setDeleting(true);
      try {
        await remove(target.id);
        toast.success('Snapshot deleted');
        setConfirmDeleteTarget(null);
      } catch (err: any) {
        const msg = err?.response?.data?.error || err?.message || 'Failed to delete snapshot';
        toast.error(msg);
      } finally {
        setDeleting(false);
      }
    }, [confirmDeleteTarget, remove]);

    const renderHeader = (count: number | null) => (
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Performance Snapshots
          {count !== null && count > 0 && (
            <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">{count}</span>
          )}
        </h2>
        {!isArchived && (
          <BrandButton size="sm" onClick={onCreate} className="gap-1.5">
            <Plus className="h-4 w-4" />
            New Snapshot
          </BrandButton>
        )}
      </div>
    );

    if (loading && items.length === 0) {
      return (
        <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          {renderHeader(null)}
          <div className="flex items-center justify-center py-8">
            <div className="h-6 w-6 animate-spin rounded-full border-b-2 border-[#3CCED7]" />
            <span className="ml-3 text-sm text-gray-600">Loading snapshots...</span>
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

    return (
      <section className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        {renderHeader(items.length)}

        {isArchived && (
          <div className="mb-3 rounded-md border border-yellow-200 bg-yellow-50 p-3 text-sm text-yellow-800">
            Archived campaigns cannot be edited.
          </div>
        )}

        {items.length === 0 ? (
          <div className="py-8 text-center">
            <p className="mb-2 text-base text-gray-700">No performance snapshots yet.</p>
            <p className="mb-4 text-sm text-gray-500">
              Document your first campaign milestone.
            </p>
            {!isArchived && (
              <BrandButton size="sm" onClick={onCreate} className="gap-1.5">
                <Plus className="h-4 w-4" />
                Create Snapshot
              </BrandButton>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {items.map((snapshot) => {
              const isExpanded = expandedSnapshots.has(snapshot.id);
              const trend = getTrendPalette(snapshot.percentage_change);
              const TrendIcon = trend.icon;
              const userName =
                snapshot.snapshot_by?.username ||
                snapshot.snapshot_by?.email ||
                'Unknown';
              const userForAvatar = snapshot.snapshot_by
                ? { name: userName, email: snapshot.snapshot_by.email }
                : null;
              const showHoverActions = !isArchived && !isExpanded && hoveredRow === snapshot.id;
              const hasAdditional =
                snapshot.additional_metrics &&
                Object.keys(snapshot.additional_metrics).length > 0;

              return (
                <div
                  key={snapshot.id}
                  className="relative rounded-md border border-gray-200 transition-colors hover:border-gray-300"
                  onMouseEnter={() => setHoveredRow(snapshot.id)}
                  onMouseLeave={() => setHoveredRow(null)}
                >
                  <div
                    className="cursor-pointer p-4"
                    onClick={() => toggleExpand(snapshot.id)}
                  >
                    <div className="flex items-start gap-4">
                      <div className="flex-shrink-0">
                        <span className="inline-flex items-center rounded-md border border-[#3CCED7]/30 bg-[#3CCED7]/10 px-2.5 py-1 text-xs font-medium text-[#1a9ba3]">
                          {optionLabel(SNAPSHOT_MILESTONE, snapshot.milestone_type)}
                        </span>
                      </div>

                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-4">
                          <span className="text-sm font-medium text-gray-900">
                            {formatCurrency(snapshot.spend)}
                          </span>

                          <div className="flex items-center gap-1">
                            <span className="text-sm text-gray-600">
                              {optionLabel(SNAPSHOT_METRIC, snapshot.metric_type)}:
                            </span>
                            <span className="text-sm font-semibold text-gray-900">
                              {parseFloat(snapshot.metric_value).toLocaleString()}
                            </span>
                          </div>

                          {snapshot.percentage_change !== null && (
                            <span
                              className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${trend.pillClass}`}
                            >
                              <TrendIcon className={`h-3 w-3 ${trend.iconClass}`} />
                              {trend.label}
                            </span>
                          )}

                          {snapshot.screenshot_url && (
                            <Paperclip className="h-4 w-4 text-gray-400" />
                          )}

                          <div className="ml-auto flex items-center gap-2">
                            {userForAvatar && <UserAvatar user={userForAvatar} size="sm" />}
                            <span className="text-xs text-gray-500">
                              {formatTimestamp(snapshot.created_at)}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="flex flex-shrink-0 items-center gap-2">
                        {showHoverActions && (
                          <>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onEdit(snapshot);
                              }}
                              className="rounded p-1.5 text-gray-600 transition-colors hover:bg-[#3CCED7]/10 hover:text-[#0E8A96]"
                              title="Edit snapshot"
                            >
                              <Edit className="h-4 w-4" />
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirmDeleteTarget(snapshot);
                              }}
                              className="rounded p-1.5 text-gray-600 transition-colors hover:bg-rose-50 hover:text-rose-600"
                              title="Delete snapshot"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </>
                        )}
                        {isExpanded ? (
                          <ChevronUp className="h-4 w-4 text-gray-400" />
                        ) : (
                          <ChevronDown className="h-4 w-4 text-gray-400" />
                        )}
                      </div>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="border-t border-gray-200 px-4 pb-4 pt-4">
                      <div className="space-y-4">
                        {snapshot.notes && (
                          <div>
                            <h4 className="mb-1 text-sm font-medium text-gray-900">Observations</h4>
                            <p className="whitespace-pre-wrap text-sm text-gray-600">
                              {snapshot.notes}
                            </p>
                          </div>
                        )}

                        {snapshot.screenshot_url && (
                          <div>
                            <h4 className="mb-2 text-sm font-medium text-gray-900">Screenshot</h4>
                            <div className="overflow-hidden rounded-md border border-gray-200">
                              <img
                                src={snapshot.screenshot_url}
                                alt="Performance snapshot"
                                className="h-auto max-h-96 w-full object-contain"
                                onError={(e) => {
                                  (e.target as HTMLImageElement).style.display = 'none';
                                }}
                              />
                            </div>
                          </div>
                        )}

                        {hasAdditional && (
                          <div>
                            <h4 className="mb-2 text-sm font-medium text-gray-900">
                              Additional Metrics
                            </h4>
                            <div className="grid grid-cols-2 gap-2">
                              {Object.entries(snapshot.additional_metrics).map(([key, value]) => (
                                <div key={key} className="text-sm">
                                  <span className="text-gray-600">{key}:</span>
                                  <span className="ml-2 font-medium text-gray-900">
                                    {String(value)}
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}

                        {!isArchived && (
                          <div className="flex items-center gap-2 border-t border-gray-200 pt-2">
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                onEdit(snapshot);
                              }}
                              className="flex items-center gap-1.5 rounded px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-[#3CCED7]/10 hover:text-[#0E8A96]"
                            >
                              <Edit className="h-4 w-4" />
                              Edit
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirmDeleteTarget(snapshot);
                              }}
                              className="flex items-center gap-1.5 rounded px-3 py-1.5 text-sm text-gray-600 transition-colors hover:bg-rose-50 hover:text-rose-600"
                            >
                              <Trash2 className="h-4 w-4" />
                              Delete
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <BrandConfirmDialog
          open={!!confirmDeleteTarget}
          onOpenChange={(next) => {
            if (!next) setConfirmDeleteTarget(null);
          }}
          title="Delete Snapshot?"
          message="This snapshot will be permanently deleted. This cannot be undone."
          confirmLabel="Delete"
          tone="danger"
          loading={deleting}
          onConfirm={handleConfirmDelete}
        />
      </section>
    );
  }
);

export default SnapshotsSection;
