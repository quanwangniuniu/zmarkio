'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import toast from 'react-hot-toast';
import {
  ArrowUpRight,
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
  type MetaCreativePerformanceRow,
} from '@/lib/api/facebookApi';
import {
  formatCurrency,
  formatNumber,
  formatPercent,
  formatRatio,
  hookRateBandClass,
  holdRateBandClass,
  thumbnailOrFallback,
} from './metaAdsUtils';
import ExportActionMenu from './ExportActionMenu';
import SelectAllHeader from './SelectAllHeader';
import VideoModal from './VideoModal';
import { useBuildUrl } from '@/lib/buildUrl';

const PAGE_SIZE_OPTIONS = [5, 10, 20] as const;

type SortKey = 'spend' | 'hook_rate' | 'hold_rate' | 'roas' | 'revenue';

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'spend', label: 'Spend' },
  { key: 'hook_rate', label: 'Hook rate' },
  { key: 'hold_rate', label: 'Hold rate' },
  { key: 'roas', label: 'ROAS' },
  { key: 'revenue', label: 'Revenue' },
];

export default function CreativesPanel({
  adAccountId,
  days,
  currency,
}: {
  adAccountId: number;
  days: number;
  currency: string;
}) {
  const [rows, setRows] = useState<MetaCreativePerformanceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [pageSize, setPageSize] = useState<number>(10);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [sortKey, setSortKey] = useState<SortKey>('spend');
  const [hidePerfEmpty, setHidePerfEmpty] = useState(true);
  const [previewCreativeId, setPreviewCreativeId] = useState<number | null>(null);
  const [previewTitle, setPreviewTitle] = useState<string>('');
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const router = useRouter();
  const buildUrl = useBuildUrl();

  const toggleSelected = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

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
      .getMetaCreativePerformance(adAccountId, days)
      .then((d) => {
        if (!active) return;
        setRows(d.creatives);
      })
      .catch(() => {
        if (!active) return;
        toast.error('Failed to load creative performance.');
      })
      .finally(() => setLoading(false));
    return () => {
      active = false;
    };
  }, [adAccountId, days]);

  const filtered = useMemo(() => {
    const base = hidePerfEmpty ? rows.filter((r) => Number(r.spend) > 0) : rows;
    return [...base].sort(
      (a, b) => Number((b as any)[sortKey]) - Number((a as any)[sortKey])
    );
  }, [rows, sortKey, hidePerfEmpty]);

  useEffect(() => {
    setCurrentPage(1);
  }, [pageSize, sortKey, hidePerfEmpty, days, adAccountId, rows.length]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const paged = useMemo(
    () => filtered.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filtered, safePage, pageSize]
  );
  const selectedCreativeCount = selectedIds.size;
  const disableGenerateVariations = selectedCreativeCount > 1;

  if (loading) {
    return (
      <div className="space-y-3">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-20 w-full" />
        ))}
      </div>
    );
  }

  return (
    <section className="rounded-xl border border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">
            Creative performance · {filtered.length} creatives
            {selectedIds.size > 0 && (
              <span className="ml-2 text-[11px] font-medium normal-case text-[#1a9ba3]">
                · {selectedIds.size} selected
              </span>
            )}
          </h2>
          <p className="mt-0.5 text-[11px] text-gray-500">
            Hook rate = video watched to 25% / impressions · Hold rate = reached 75% / reached 25%
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-[11px] text-gray-600">
            <input
              type="checkbox"
              checked={hidePerfEmpty}
              onChange={(e) => setHidePerfEmpty(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-[#3CCED7] focus:ring-[#3CCED7]/40"
            />
            Only show creatives with spend
          </label>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/40"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.key} value={o.key}>
                Sort by {o.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={disableGenerateVariations}
            title={
              disableGenerateVariations
                ? 'Generate variations supports one selected creative at a time.'
                : undefined
            }
            onClick={() => {
              if (disableGenerateVariations) return;
              if (selectedCreativeCount === 1) {
                const onlyId = Array.from(selectedIds)[0];
                const onlySlug = rows.find((r) => r.id === onlyId)?.slug;
                router.push(buildUrl(`/variations-studio?creative=${onlySlug ?? onlyId}`));
              } else {
                router.push(buildUrl('/variations-studio'));
              }
            }}
            className="inline-flex h-9 items-center gap-1 rounded-lg bg-white px-3 text-sm font-medium text-gray-700 ring-1 ring-gray-200 transition hover:text-[#1a9ba3] hover:ring-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:cursor-not-allowed disabled:text-gray-400 disabled:ring-gray-200 disabled:hover:text-gray-400 disabled:hover:ring-gray-200"
          >
            Generate variations
          </button>
          <ExportActionMenu
            unit="creative"
            selectedIds={Array.from(selectedIds)}
            adAccountId={adAccountId}
            days={days}
          />
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-[11px] uppercase text-gray-500">
            <tr>
              <th className="w-10 px-3 py-2 font-medium">
                <SelectAllHeader
                  selectedCount={
                    paged.filter((c) => selectedIds.has(c.id)).length
                  }
                  totalCount={paged.length}
                  onSelectAll={() => {
                    const next = new Set(selectedIds);
                    for (const c of paged) next.add(c.id);
                    setSelectedIds(next);
                  }}
                  onClear={() => {
                    const next = new Set(selectedIds);
                    for (const c of paged) next.delete(c.id);
                    setSelectedIds(next);
                  }}
                />
              </th>
              <th className="px-4 py-2 font-medium">Creative</th>
              <th className="px-4 py-2 font-medium text-right">Spend</th>
              <th className="px-4 py-2 font-medium text-right">Impr.</th>
              <th className="px-4 py-2 font-medium text-right">Hook</th>
              <th className="px-4 py-2 font-medium text-right">Hold</th>
              <th className="px-4 py-2 font-medium text-right">Leads</th>
              <th className="px-4 py-2 font-medium text-right">Purch.</th>
              <th className="px-4 py-2 font-medium text-right">Revenue</th>
              <th className="px-4 py-2 font-medium text-right">ROAS</th>
              <th className="px-2 py-2 font-medium" aria-label="View details"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {paged.length === 0 ? (
              <tr>
                <td
                  colSpan={11}
                  className="px-4 py-8 text-center text-xs text-gray-400"
                >
                  {rows.length === 0
                    ? 'No creative data available.'
                    : 'No creatives match the current filter.'}
                </td>
              </tr>
            ) : (
              paged.map((c) => (
                <CreativeRow
                  key={c.id}
                  c={c}
                  currency={currency}
                  selected={selectedIds.has(c.id)}
                  onToggle={() => toggleSelected(c.id)}
                  onPlay={() => {
                    setPreviewTitle(c.title || c.name || c.meta_creative_id);
                    setPreviewCreativeId(c.id);
                  }}
                />
              ))
            )}
          </tbody>
        </table>
      </div>

      <VideoModal
        creativeId={previewCreativeId}
        open={previewCreativeId !== null}
        title={previewTitle}
        onClose={() => setPreviewCreativeId(null)}
      />

      {filtered.length > 0 && (
        <PaginationBar
          pageSize={pageSize}
          onPageSizeChange={setPageSize}
          currentPage={safePage}
          totalPages={totalPages}
          total={filtered.length}
          onPageChange={setCurrentPage}
        />
      )}
    </section>
  );
}

function CreativeRow({
  c,
  currency,
  onPlay,
  selected,
  onToggle,
}: {
  c: MetaCreativePerformanceRow;
  currency: string;
  onPlay: () => void;
  selected: boolean;
  onToggle: () => void;
}) {
  const buildUrl = useBuildUrl();
  const thumb = thumbnailOrFallback(c.thumbnail_url);
  const isVideo = c.object_type === 'VIDEO' || !!c.video_id;
  const detailHref = buildUrl(`/meta-ads/creatives/${c.slug}`);
  const display = c.title || c.name || c.meta_creative_id;
  return (
    <tr
      className={`align-top hover:bg-gray-50/60 ${selected ? 'bg-[#3CCED7]/5' : ''}`}
    >
      <td className="px-3 py-3">
        <input
          type="checkbox"
          aria-label={`Select creative ${display} for export`}
          checked={selected}
          onChange={onToggle}
          className="h-4 w-4 rounded accent-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
        />
      </td>
      <td className="px-4 py-3">
        <div className="flex items-start gap-3">
          <button
            type="button"
            onClick={onPlay}
            aria-label={isVideo ? 'Play video preview' : 'Preview creative'}
            className="group relative h-14 w-14 shrink-0 overflow-hidden rounded-md border border-gray-200 bg-gray-50 transition-shadow hover:border-[#3CCED7]/60 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7]/50"
          >
            {thumb ? (
              <img
                src={thumb}
                alt=""
                className="h-full w-full object-cover"
                loading="lazy"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-gray-300">
                <ImageIcon className="h-5 w-5" />
              </div>
            )}
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/35">
              <span className="flex h-7 w-7 translate-y-0 scale-90 items-center justify-center rounded-full bg-white/0 text-white shadow-lg opacity-0 transition-all group-hover:scale-100 group-hover:bg-white group-hover:text-[#1a9ba3] group-hover:opacity-100">
                <Play className="h-3.5 w-3.5 fill-current" />
              </span>
            </div>
            {isVideo && (
              <span className="pointer-events-none absolute bottom-0 right-0 rounded-tl-md bg-black/70 px-1 py-0.5 text-[9px] font-medium text-white">
                <Video className="-mt-px inline h-2.5 w-2.5" />
              </span>
            )}
          </button>
          <Link
            href={detailHref}
            className="group/body min-w-0 flex-1 rounded-md px-1 -ml-1 py-0.5 -my-0.5 transition-colors hover:bg-gray-100/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7]/40"
          >
            <div className="max-w-[360px] truncate text-xs font-medium text-gray-900 group-hover/body:text-[#1a9ba3]">
              {c.title || c.name || c.meta_creative_id}
            </div>
            {c.body && (
              <div className="mt-0.5 line-clamp-2 max-w-[420px] text-[11px] text-gray-500">
                {c.body}
              </div>
            )}
            <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-gray-400">
              <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono">
                {c.meta_creative_id}
              </span>
              {c.call_to_action_type && (
                <span className="rounded bg-gray-100 px-1.5 py-0.5">
                  {c.call_to_action_type}
                </span>
              )}
              {c.object_type && (
                <span className="rounded bg-gray-100 px-1.5 py-0.5">
                  {c.object_type}
                </span>
              )}
            </div>
          </Link>
        </div>
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-gray-800">
        {formatCurrency(c.spend, currency)}
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-gray-600">
        {formatNumber(c.impressions)}
      </td>
      <td className="px-4 py-3 text-right">
        <HookRateCell value={c.hook_rate} />
      </td>
      <td className="px-4 py-3 text-right">
        <HoldRateCell value={c.hold_rate} />
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-gray-800">
        {formatNumber(c.leads)}
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-gray-800">
        {formatNumber(c.purchases)}
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-gray-800">
        {Number(c.revenue) > 0 ? formatCurrency(c.revenue, currency) : '—'}
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs text-gray-800">
        {Number(c.roas) > 0 ? `${formatRatio(c.roas)}x` : '—'}
      </td>
      <td className="px-2 py-3 text-right">
        <Link
          href={detailHref}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-gray-400 transition-colors hover:border-gray-200 hover:bg-gray-50 hover:text-[#1a9ba3]"
          aria-label="Open creative detail"
        >
          <ArrowUpRight className="h-3.5 w-3.5" />
        </Link>
      </td>
    </tr>
  );
}

function HookRateCell({ value }: { value: string }) {
  const n = Number(value);
  const width = Math.min(100, Math.max(0, n));
  return (
    <div className="flex flex-col items-end gap-1">
      <span
        className={`inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-mono font-medium ${hookRateBandClass(value)}`}
      >
        {Number.isFinite(n) && n > 0 ? formatPercent(value) : '—'}
      </span>
      {Number.isFinite(n) && n > 0 && (
        <div className="h-1 w-16 overflow-hidden rounded-full bg-gray-100">
          <div
            className="h-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]"
            style={{ width: `${width}%` }}
          />
        </div>
      )}
    </div>
  );
}

function HoldRateCell({ value }: { value: string }) {
  const n = Number(value);
  return (
    <span
      className={`inline-flex rounded-md px-1.5 py-0.5 text-[11px] font-mono font-medium ${holdRateBandClass(value)}`}
    >
      {Number.isFinite(n) && n > 0 ? formatPercent(value) : '—'}
    </span>
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
