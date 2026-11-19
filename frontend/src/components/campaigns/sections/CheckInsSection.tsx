'use client';

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react';
import { AlertCircle, CheckCircle, Edit, Minus, Plus, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';

import UserAvatar from '@/people/UserAvatar';
import BrandButton from '@/components/campaigns/BrandButton';
import BrandConfirmDialog from '@/components/campaigns/BrandConfirmDialog';
import { useCampaignCheckIns } from '@/hooks/campaigns/useCampaignCheckIns';
import type { CampaignCheckIn } from '@/types/campaign';
import { CHECKIN_SENTIMENT } from '@/lib/campaigns/campaignConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionColorStyle, optionLabel } from '@/lib/orgCustomization/optionConfig';

export interface CheckInsSectionProps {
  campaignId: string;
  isArchived?: boolean;
  onCreate: () => void;
  onEdit: (checkIn: CampaignCheckIn) => void;
}

export interface CheckInsSectionHandle {
  refresh: () => Promise<void>;
}

type SentimentPalette = {
  icon: typeof CheckCircle;
  pillClass: string;
  iconClass: string;
  defaultNote: string;
};

const SENTIMENT_PALETTE: Record<string, SentimentPalette> = {
  POSITIVE: {
    icon: CheckCircle,
    pillClass: 'bg-green-50 text-green-700 border-green-200',
    iconClass: 'text-green-600',
    defaultNote: 'Feeling positive about the campaign',
  },
  NEUTRAL: {
    icon: Minus,
    pillClass: 'bg-gray-50 text-gray-700 border-gray-200',
    iconClass: 'text-gray-600',
    defaultNote: 'Feeling neutral about the campaign',
  },
  NEGATIVE: {
    icon: AlertCircle,
    pillClass: 'bg-red-50 text-red-700 border-red-200',
    iconClass: 'text-red-600',
    defaultNote: 'Feeling concerned about the campaign',
  },
};

function getPalette(sentiment: string): SentimentPalette {
  return SENTIMENT_PALETTE[sentiment?.toUpperCase()] ?? SENTIMENT_PALETTE.NEUTRAL;
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

const CheckInsSection = forwardRef<CheckInsSectionHandle, CheckInsSectionProps>(
  function CheckInsSection({ campaignId, isArchived = false, onCreate, onEdit }, ref) {
    useOptionConfigVersion();
    const { items, loading, error, refresh, remove } = useCampaignCheckIns(campaignId);
    const [hoveredRow, setHoveredRow] = useState<string | null>(null);
    const [confirmDeleteTarget, setConfirmDeleteTarget] = useState<CampaignCheckIn | null>(null);
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

    const errorMessage = useMemo(() => {
      if (!error) return null;
      const anyErr = error as any;
      return anyErr?.response?.data?.error || anyErr?.message || 'Failed to load check-ins';
    }, [error]);

    const handleConfirmDelete = useCallback(async () => {
      if (!confirmDeleteTarget) return;
      const target = confirmDeleteTarget;
      setDeleting(true);
      try {
        await remove(target.id);
        toast.success('Check-in deleted');
        setConfirmDeleteTarget(null);
      } catch (err: any) {
        const msg = err?.response?.data?.error || err?.message || 'Failed to delete check-in';
        toast.error(msg);
      } finally {
        setDeleting(false);
      }
    }, [confirmDeleteTarget, remove]);

    const renderHeader = (count: number | null) => (
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Check-ins
          {count !== null && count > 0 && (
            <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">{count}</span>
          )}
        </h2>
        {!isArchived && (
          <BrandButton size="sm" onClick={onCreate} className="gap-1.5">
            <Plus className="h-4 w-4" />
            New Check-in
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
            <span className="ml-3 text-sm text-gray-600">Loading check-ins...</span>
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
            <p className="mb-2 text-base text-gray-700">No check-ins yet.</p>
            <p className="mb-4 text-sm text-gray-500">
              Log your first campaign health check-in.
            </p>
            {!isArchived && (
              <BrandButton size="sm" onClick={onCreate} className="gap-1.5">
                <Plus className="h-4 w-4" />
                Create Check-in
              </BrandButton>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {items.map((checkIn) => {
              const palette = getPalette(checkIn.sentiment);
              const Icon = palette.icon;
              const mainText = checkIn.note || palette.defaultNote;
              const userName =
                checkIn.checked_by?.username || checkIn.checked_by?.email || 'Unknown';
              const userForAvatar = checkIn.checked_by
                ? { name: userName, email: checkIn.checked_by.email }
                : null;
              const showActions = !isArchived && hoveredRow === checkIn.id;

              return (
                <div
                  key={checkIn.id}
                  className="relative rounded-md border border-gray-200 p-4 transition-colors hover:border-gray-300"
                  onMouseEnter={() => setHoveredRow(checkIn.id)}
                  onMouseLeave={() => setHoveredRow(null)}
                >
                  <div className="flex items-start gap-4">
                    <div className="flex-shrink-0">
                      <span
                        className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium ${palette.pillClass}`}
                        style={optionColorStyle(CHECKIN_SENTIMENT.colors[checkIn.sentiment])}
                      >
                        <Icon className={`h-4 w-4 ${palette.iconClass}`} />
                        {optionLabel(CHECKIN_SENTIMENT, checkIn.sentiment)}
                      </span>
                    </div>

                    <div className="min-w-0 flex-1">
                      <p className="mb-2 text-sm leading-relaxed text-gray-900">{mainText}</p>
                      {userForAvatar && (
                        <div className="flex items-center gap-2">
                          <UserAvatar user={userForAvatar} size="sm" />
                          <span className="text-xs text-gray-500">{userName}</span>
                        </div>
                      )}
                    </div>

                    <div className="flex flex-shrink-0 flex-col items-end gap-2">
                      <span className="text-xs text-gray-400">
                        {formatTimestamp(checkIn.created_at)}
                      </span>
                      {showActions && (
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => onEdit(checkIn)}
                            className="rounded p-1.5 text-gray-400 transition-colors hover:bg-[#3CCED7]/10 hover:text-[#0E8A96]"
                            title="Edit check-in"
                          >
                            <Edit className="h-4 w-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteTarget(checkIn)}
                            className="rounded p-1.5 text-gray-400 transition-colors hover:bg-rose-50 hover:text-rose-600"
                            title="Delete check-in"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
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
          title="Delete Check-in?"
          message="This check-in will be permanently deleted. This cannot be undone."
          confirmLabel="Delete"
          tone="danger"
          loading={deleting}
          onConfirm={handleConfirmDelete}
        />
      </section>
    );
  }
);

export default CheckInsSection;
