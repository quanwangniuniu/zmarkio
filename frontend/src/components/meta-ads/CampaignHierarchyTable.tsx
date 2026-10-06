'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import toast from 'react-hot-toast';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Image as ImageIcon,
  Play,
  Video,
} from 'lucide-react';

import { Skeleton } from '@/components/ui/skeleton';
import {
  facebookApi,
  type MetaAdPerformanceRow,
  type MetaAdSetPerformanceRow,
  type MetaCampaignPerformanceRow,
} from '@/lib/api/facebookApi';
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  formatRatio,
  hookRateBandClass,
  thumbnailOrFallback,
} from './metaAdsUtils';
import SelectAllHeader from './SelectAllHeader';
import VideoModal from './VideoModal';
import { useBuildUrl } from '@/lib/buildUrl';

const PAGE_SIZE_OPTIONS = [5, 10, 20] as const;

export interface CampaignHierarchyTableSelection {
  selectedIds: Set<number>;
  onToggle: (id: number) => void;
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case 'ACTIVE':
      return 'bg-[#A6E661]/20 text-[#3d6b00]';
    case 'PAUSED':
      return 'bg-gray-100 text-gray-600';
    case 'ARCHIVED':
    case 'DELETED':
      return 'bg-gray-50 text-gray-400';
    default:
      return 'bg-gray-100 text-gray-600';
  }
}

function roasBandClass(roasStr: string, idealRoas = 2.0): string {
  const roas = Number(roasStr);
  if (!Number.isFinite(roas) || roas <= 0) return 'bg-gray-100 text-gray-500';
  if (roas >= idealRoas) return 'bg-[#3CCED7]/15 text-[#1a9ba3]';
  if (roas >= idealRoas * 0.75) return 'bg-amber-100 text-amber-700';
  return 'bg-red-100 text-red-700';
}

export default function CampaignHierarchyTable({
  campaigns,
  currency,
  adAccountId,
  days,
  selection,
}: {
  campaigns: MetaCampaignPerformanceRow[];
  currency: string;
  adAccountId: number;
  days: number;
  selection?: CampaignHierarchyTableSelection;
}) {
  const buildUrl = useBuildUrl();
  const [pageSize, setPageSize] = useState<number>(10);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [expandedCampaigns, setExpandedCampaigns] = useState<Set<number>>(new Set());

  useEffect(() => {
    setCurrentPage(1);
    setExpandedCampaigns(new Set());
  }, [adAccountId, days, pageSize, campaigns.length]);

  const total = campaigns.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const paged = useMemo(
    () => campaigns.slice((safePage - 1) * pageSize, safePage * pageSize),
    [campaigns, safePage, pageSize]
  );

  const toggleCampaign = (id: number) => {
    setExpandedCampaigns((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-gray-900">
          Campaign performance · {total} campaigns
        </h2>
        <p className="text-xs text-gray-500">
          Click ▶ to drill into ad sets and ads
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-[11px] uppercase text-gray-500">
            <tr>
              {selection && (
                <th className="w-10 px-3 py-2 font-medium">
                  <SelectAllHeader
                    selectedCount={
                      paged.filter((c) => selection.selectedIds.has(c.id))
                        .length
                    }
                    totalCount={paged.length}
                    onSelectAll={() => {
                      for (const c of paged) {
                        if (!selection.selectedIds.has(c.id)) {
                          selection.onToggle(c.id);
                        }
                      }
                    }}
                    onClear={() => {
                      for (const c of paged) {
                        if (selection.selectedIds.has(c.id)) {
                          selection.onToggle(c.id);
                        }
                      }
                    }}
                  />
                </th>
              )}
              <th className="w-10 px-2 py-2 font-medium"></th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Campaign</th>
              <th className="px-4 py-2 font-medium text-right">Spend</th>
              <th className="px-4 py-2 font-medium text-right">CTR</th>
              <th className="px-4 py-2 font-medium text-right">CPC</th>
              <th className="px-4 py-2 font-medium text-right">Leads</th>
              <th className="px-4 py-2 font-medium text-right">CPL</th>
              <th className="px-4 py-2 font-medium text-right">Purch.</th>
              <th className="px-4 py-2 font-medium text-right">Revenue</th>
              <th className="px-4 py-2 font-medium text-right">ROAS</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {paged.length === 0 ? (
              <tr>
                <td
                  colSpan={selection ? 12 : 11}
                  className="px-4 py-8 text-center text-xs text-gray-400"
                >
                  No campaign data. Try refreshing.
                </td>
              </tr>
            ) : (
              paged.map((c) => {
                const expanded = expandedCampaigns.has(c.id);
                const isSelected =
                  selection?.selectedIds.has(c.id) ?? false;
                return (
                  <>
                    <tr
                      key={c.id}
                      className={`hover:bg-gray-50/60 ${isSelected ? 'bg-[#3CCED7]/5' : ''}`}
                    >
                      {selection && (
                        <td className="px-3 py-2.5">
                          <input
                            type="checkbox"
                            aria-label={`Select campaign ${c.name || c.meta_campaign_id} for export`}
                            checked={isSelected}
                            onChange={() => selection.onToggle(c.id)}
                            className="h-4 w-4 rounded accent-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
                          />
                        </td>
                      )}
                      <td className="px-2 py-2.5 text-center">
                        <button
                          type="button"
                          onClick={() => toggleCampaign(c.id)}
                          aria-label={expanded ? 'Collapse campaign' : 'Expand campaign'}
                          className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-transparent text-gray-500 transition-colors hover:border-gray-200 hover:bg-gray-50 hover:text-[#1a9ba3]"
                        >
                          {expanded ? (
                            <ChevronDown className="h-3.5 w-3.5" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${statusBadgeClass(c.effective_status)}`}
                        >
                          {c.effective_status}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <Link
                          href={buildUrl(`/meta-ads/campaigns/${c.slug}`)}
                          className="block max-w-[420px] truncate text-xs font-medium text-gray-900 hover:text-[#1a9ba3]"
                        >
                          {c.name || c.meta_campaign_id}
                        </Link>
                        <div className="mt-0.5 text-[10px] text-gray-400">
                          {c.objective.replace('OUTCOME_', '')}
                        </div>
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-800">
                        {formatCurrency(c.spend, currency)}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-600">
                        {formatPercent(c.ctr)}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-600">
                        {formatCurrency(c.cpc, currency)}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-800">
                        {formatNumber(c.leads)}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-600">
                        {c.leads > 0 ? formatCurrency(c.cpl, currency) : '—'}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-800">
                        {formatNumber(c.purchases)}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono text-xs text-gray-800">
                        {Number(c.revenue) > 0 ? formatCurrency(c.revenue, currency) : '—'}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <span
                          className={`inline-flex rounded-md px-2 py-0.5 text-[11px] font-mono font-medium ${roasBandClass(c.roas)}`}
                        >
                          {Number(c.roas) > 0 ? `${formatRatio(c.roas)}x` : '—'}
                        </span>
                      </td>
                    </tr>
                    {expanded && (
                      <tr key={`${c.id}-expand`}>
                        <td
                          colSpan={selection ? 12 : 11}
                          className="bg-gradient-to-b from-[#3CCED7]/5 to-transparent px-4 py-3"
                        >
                          <AdSetInset
                            adAccountId={adAccountId}
                            campaignId={c.id}
                            campaignName={c.name || c.meta_campaign_id}
                            currency={currency}
                            days={days}
                          />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {total > 0 && (
        <PaginationBar
          pageSize={pageSize}
          onPageSizeChange={setPageSize}
          currentPage={safePage}
          totalPages={totalPages}
          total={total}
          onPageChange={setCurrentPage}
        />
      )}
    </section>
  );
}

function AdSetInset({
  adAccountId,
  campaignId,
  campaignName,
  currency,
  days,
}: {
  adAccountId: number;
  campaignId: number;
  campaignName: string;
  currency: string;
  days: number;
}) {
  const buildUrl = useBuildUrl();
  const [rows, setRows] = useState<MetaAdSetPerformanceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [expandedAdSets, setExpandedAdSets] = useState<Set<number>>(new Set());

  useEffect(() => {
    let active = true;
    setLoading(true);
    facebookApi
      .getMetaAdSetPerformance(adAccountId, days, campaignId)
      .then((d) => active && setRows(d.adsets))
      .catch(() => active && toast.error('Failed to load ad sets.'))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [adAccountId, campaignId, days]);

  const toggleAdSet = (id: number) => {
    setExpandedAdSets((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="rounded-lg border border-[#3CCED7]/30 bg-white">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-[#1a9ba3]">
          Ad sets under this campaign · {rows.length}
        </div>
        <div className="text-[10px] text-gray-400 truncate max-w-[280px]">
          {campaignName}
        </div>
      </div>
      {loading ? (
        <div className="space-y-2 p-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-8 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <div className="px-3 py-6 text-center text-xs text-gray-400">
          No ad sets with data in this window.
        </div>
      ) : (
        <table className="w-full text-xs">
          <thead className="bg-gray-50/80 text-left text-[10px] uppercase text-gray-500">
            <tr>
              <th className="w-8 px-2 py-1.5 font-medium"></th>
              <th className="px-3 py-1.5 font-medium">Ad set</th>
              <th className="px-3 py-1.5 font-medium text-right">Spend</th>
              <th className="px-3 py-1.5 font-medium text-right">Impr.</th>
              <th className="px-3 py-1.5 font-medium text-right">CTR</th>
              <th className="px-3 py-1.5 font-medium text-right">Leads</th>
              <th className="px-3 py-1.5 font-medium text-right">Purch.</th>
              <th className="px-3 py-1.5 font-medium text-right">Revenue</th>
              <th className="px-3 py-1.5 font-medium text-right">ROAS</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((a) => {
              const expanded = expandedAdSets.has(a.id);
              return (
                <>
                  <tr key={a.id} className="hover:bg-gray-50/60">
                    <td className="px-2 py-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => toggleAdSet(a.id)}
                        aria-label={expanded ? 'Collapse ad set' : 'Expand ad set'}
                        className="inline-flex h-5 w-5 items-center justify-center rounded-md border border-transparent text-gray-500 transition-colors hover:border-gray-200 hover:bg-gray-50 hover:text-[#1a9ba3]"
                      >
                        {expanded ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                      </button>
                    </td>
                    <td className="px-3 py-1.5">
                      <Link
                        href={buildUrl(`/meta-ads/adsets/${a.slug}`)}
                        className="block max-w-[420px] truncate text-xs text-gray-900 hover:text-[#1a9ba3]"
                      >
                        {a.name || a.meta_adset_id}
                      </Link>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-gray-400">
                        <span
                          className={`rounded-full px-1.5 py-0.5 font-medium ${statusBadgeClass(a.effective_status)}`}
                        >
                          {a.effective_status}
                        </span>
                        {a.optimization_goal && (
                          <span>goal · {a.optimization_goal}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-gray-800">
                      {formatCurrency(a.spend, currency)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-gray-600">
                      {formatNumber(a.impressions)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-gray-600">
                      {formatPercent(a.ctr)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-gray-800">
                      {formatNumber(a.leads)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-gray-800">
                      {formatNumber(a.purchases)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-xs text-gray-800">
                      {Number(a.revenue) > 0 ? formatCurrency(a.revenue, currency) : '—'}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <span
                        className={`inline-flex rounded-md px-1.5 py-0.5 text-[10px] font-mono font-medium ${roasBandClass(a.roas)}`}
                      >
                        {Number(a.roas) > 0 ? `${formatRatio(a.roas)}x` : '—'}
                      </span>
                    </td>
                  </tr>
                  {expanded && (
                    <tr key={`${a.id}-ads`}>
                      <td colSpan={9} className="bg-[#A6E661]/5 px-3 py-2">
                        <AdsInset
                          adAccountId={adAccountId}
                          adsetId={a.id}
                          adsetName={a.name || a.meta_adset_id}
                          currency={currency}
                          days={days}
                        />
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function AdsInset({
  adAccountId,
  adsetId,
  adsetName,
  currency,
  days,
}: {
  adAccountId: number;
  adsetId: number;
  adsetName: string;
  currency: string;
  days: number;
}) {
  const buildUrl = useBuildUrl();
  const [rows, setRows] = useState<MetaAdPerformanceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewCreativeId, setPreviewCreativeId] = useState<number | null>(null);
  const [previewTitle, setPreviewTitle] = useState<string>('');

  // Close any open preview when the ad account changes so a prior account's
  // modal / creative id cannot linger on the new account's UI.
  useEffect(() => {
    setPreviewCreativeId(null);
    setPreviewTitle('');
  }, [adAccountId]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    facebookApi
      .getMetaAdPerformance(adAccountId, days, { adsetId })
      .then((d) => active && setRows(d.ads))
      .catch(() => active && toast.error('Failed to load ads.'))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [adAccountId, adsetId, days]);

  return (
    <div className="rounded-lg border border-[#A6E661]/40 bg-white">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-1.5">
        <div className="text-[10px] font-semibold uppercase tracking-wide text-[#3d6b00]">
          Ads under this ad set · {rows.length}
        </div>
        <div className="text-[10px] text-gray-400 truncate max-w-[280px]">
          {adsetName}
        </div>
      </div>
      {loading ? (
        <div className="space-y-1.5 p-3">
          <Skeleton className="h-6 w-full" />
          <Skeleton className="h-6 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <div className="px-3 py-4 text-center text-[11px] text-gray-400">
          No ads with data in this window.
        </div>
      ) : (
        <table className="w-full text-[11px]">
          <thead className="bg-gray-50/70 text-left text-[10px] uppercase text-gray-500">
            <tr>
              <th className="w-10 px-2 py-1 font-medium"></th>
              <th className="px-3 py-1 font-medium">Ad</th>
              <th className="px-3 py-1 font-medium text-right">Spend</th>
              <th className="px-3 py-1 font-medium text-right">Impr.</th>
              <th className="px-3 py-1 font-medium text-right">Hook</th>
              <th className="px-3 py-1 font-medium text-right">Leads</th>
              <th className="px-3 py-1 font-medium text-right">Purch.</th>
              <th className="px-3 py-1 font-medium text-right">Revenue</th>
              <th className="px-3 py-1 font-medium text-right">ROAS</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((ad) => {
              const thumb = thumbnailOrFallback(ad.creative?.thumbnail_url ?? null);
              const isVideo =
                ad.creative?.object_type === 'VIDEO' || !!ad.creative?.video_id;
              return (
                <tr key={ad.id} className="align-top hover:bg-gray-50/60">
                  <td className="px-2 py-1.5">
                    {ad.creative ? (
                      <button
                        type="button"
                        onClick={() => {
                          setPreviewCreativeId(ad.creative!.id);
                          setPreviewTitle(ad.creative!.title || ad.name);
                        }}
                        aria-label="Play creative preview"
                        className="group relative h-7 w-7 overflow-hidden rounded-md border border-gray-200 bg-gray-50 transition-shadow hover:border-[#3CCED7]/60 hover:shadow"
                      >
                        {thumb ? (
                          <img
                            src={thumb}
                            alt=""
                            className="h-full w-full object-cover"
                            referrerPolicy="no-referrer"
                          />
                        ) : (
                          <ImageIcon className="m-auto h-3 w-3 text-gray-300" />
                        )}
                        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/30">
                          <Play className="h-3 w-3 fill-white text-white opacity-0 transition-opacity group-hover:opacity-100" />
                        </div>
                        {isVideo && (
                          <span className="pointer-events-none absolute bottom-0 right-0 rounded-tl-md bg-black/70 px-0.5">
                            <Video className="h-2 w-2 text-white" />
                          </span>
                        )}
                      </button>
                    ) : (
                      <div className="flex h-7 w-7 items-center justify-center rounded-md border border-dashed border-gray-200 bg-gray-50">
                        <ImageIcon className="h-3 w-3 text-gray-300" />
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-1.5">
                    <div className="max-w-[360px] truncate text-[11px] text-gray-900">
                      {ad.name || ad.meta_ad_id}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1 text-[10px] text-gray-400">
                      <span
                        className={`rounded-full px-1 py-0.5 font-medium ${statusBadgeClass(ad.effective_status)}`}
                      >
                        {ad.effective_status}
                      </span>
                      {ad.creative?.meta_creative_id && (
                        <Link
                          href={buildUrl(`/meta-ads/creatives/${ad.creative.slug}`)}
                          className="font-mono text-gray-400 hover:text-[#1a9ba3]"
                        >
                          creative {ad.creative.meta_creative_id}
                        </Link>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-[11px] text-gray-800">
                    {formatCurrency(ad.spend, currency)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-[11px] text-gray-600">
                    {formatNumber(ad.impressions)}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <span
                      className={`rounded px-1 py-0.5 font-mono text-[10px] ${hookRateBandClass(ad.hook_rate)}`}
                    >
                      {Number(ad.hook_rate) > 0 ? formatPercent(ad.hook_rate) : '—'}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-[11px] text-gray-800">
                    {formatNumber(ad.leads)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-[11px] text-gray-800">
                    {formatNumber(ad.purchases)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono text-[11px] text-gray-800">
                    {Number(ad.revenue) > 0 ? formatCurrency(ad.revenue, currency) : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <span
                      className={`inline-flex rounded-md px-1 py-0.5 font-mono text-[10px] ${roasBandClass(ad.roas)}`}
                    >
                      {Number(ad.roas) > 0 ? `${formatRatio(ad.roas)}x` : '—'}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <VideoModal
        creativeId={previewCreativeId}
        open={previewCreativeId !== null}
        title={previewTitle}
        onClose={() => setPreviewCreativeId(null)}
      />
    </div>
  );
}

function PaginationBar({
  pageSize,
  onPageSizeChange,
  currentPage,
  totalPages,
  total,
  onPageChange,
}: {
  pageSize: number;
  onPageSizeChange: (n: number) => void;
  currentPage: number;
  totalPages: number;
  total: number;
  onPageChange: (n: number) => void;
}) {
  const [pageInput, setPageInput] = useState<string>(String(currentPage));
  useEffect(() => setPageInput(String(currentPage)), [currentPage]);

  const goToPage = (n: number) => {
    const clamped = Math.max(1, Math.min(totalPages, Math.floor(n)));
    onPageChange(clamped);
    setPageInput(String(clamped));
  };

  const rangeStart = total === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const rangeEnd = Math.min(total, currentPage * pageSize);

  const btnBase =
    'inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40';

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 px-4 py-2.5 text-xs text-gray-600">
      <div className="flex items-center gap-2">
        <span className="text-gray-500">Rows per page</span>
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/40"
        >
          {PAGE_SIZE_OPTIONS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <span className="ml-2 text-gray-400">
          {rangeStart}–{rangeEnd} of {total}
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          className={btnBase}
          aria-label="First page"
          disabled={currentPage <= 1}
          onClick={() => goToPage(1)}
        >
          <ChevronsLeft className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className={btnBase}
          aria-label="Previous page"
          disabled={currentPage <= 1}
          onClick={() => goToPage(currentPage - 1)}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <div className="flex items-center gap-1 px-1 text-gray-500">
          <span>Page</span>
          <input
            type="number"
            min={1}
            max={totalPages}
            value={pageInput}
            onChange={(e) => setPageInput(e.target.value)}
            onBlur={() => {
              const n = Number(pageInput);
              if (Number.isFinite(n)) goToPage(n);
              else setPageInput(String(currentPage));
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const n = Number((e.target as HTMLInputElement).value);
                if (Number.isFinite(n)) goToPage(n);
              }
            }}
            className="h-7 w-14 rounded-md border border-gray-200 bg-white px-1.5 text-center font-mono text-xs focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/40"
          />
          <span>of {totalPages}</span>
        </div>
        <button
          type="button"
          className={btnBase}
          aria-label="Next page"
          disabled={currentPage >= totalPages}
          onClick={() => goToPage(currentPage + 1)}
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          className={btnBase}
          aria-label="Last page"
          disabled={currentPage >= totalPages}
          onClick={() => goToPage(totalPages)}
        >
          <ChevronsRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
