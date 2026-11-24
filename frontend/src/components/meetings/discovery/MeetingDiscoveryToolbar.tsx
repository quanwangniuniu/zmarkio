'use client';

import { useEffect, useRef, useState } from 'react';
import { Filter, Loader2, Search, X } from 'lucide-react';

import { cn } from '@/lib/utils';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';

import {
  MeetingFiltersPanel,
  type MeetingFiltersPanelProps,
} from './MeetingFiltersPanel';
import { MeetingTypeMultiSelectButton } from './MeetingTypeMultiSelectButton';

const SEARCH_DEBOUNCE_MS = 320;

export type FilterChip = {
  id: string;
  label: string;
  onRemove: () => void;
};

export interface MeetingDiscoveryToolbarProps
  extends Omit<
    MeetingFiltersPanelProps,
    'onClosePanel' | 'onAdvancedFiltersApply'
  > {
  qValue: string;
  onQDebouncedChange: (next: string | undefined) => void;
  searchDisabled?: boolean;
  searchLoading?: boolean;
  /** Structured filters only (not keyword, not meeting type toolbar); shown on Filter button. */
  filterBadgeCount: number;
  /** Meeting type slugs from URL; toolbar multi-select updates immediately. */
  selectedMeetingTypeSlugs: string[];
  onMeetingTypeSlugsChange: (next: string[]) => void;
  onAdvancedFiltersApply: MeetingFiltersPanelProps['onAdvancedFiltersApply'];
  onPopoverCancel: () => void;
  canClear: boolean;
  onClearAll: () => void;
}

function MeetingToolbarSearch({
  value,
  onDebouncedChange,
  disabled,
  isLoading,
}: {
  value: string;
  onDebouncedChange: (next: string | undefined) => void;
  disabled?: boolean;
  isLoading?: boolean;
}) {
  const [local, setLocal] = useState(value);

  useEffect(() => {
    setLocal(value);
  }, [value]);

  useEffect(() => {
    const t = window.setTimeout(() => {
      const next = local.trim();
      const prev = value.trim();
      if (next !== prev) {
        onDebouncedChange(next ? next : undefined);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [local, onDebouncedChange, value]);

  const clear = () => {
    setLocal('');
    onDebouncedChange(undefined);
  };

  return (
    <div
      className={cn(
        'relative w-full sm:max-w-md',
        disabled && 'pointer-events-none opacity-60',
      )}
    >
      <Search
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
        aria-hidden
      />
      <input
        type="text"
        inputMode="search"
        autoComplete="off"
        placeholder="Search titles, summaries & transcripts…"
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        disabled={disabled}
        className="h-9 w-full rounded-md border border-slate-200 bg-white pl-9 pr-16 text-sm text-slate-700 placeholder:text-slate-400 focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-blue-100"
        aria-label="Search meetings"
      />
      <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
        {isLoading ? (
          <Loader2 className="h-4 w-4 animate-spin text-[#3CCED7]" aria-hidden />
        ) : null}
        {local ? (
          <button
            type="button"
            onClick={clear}
            className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
            aria-label="Clear search"
          >
            <X className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    </div>
  );
}

export function MeetingDiscoveryToolbar({
  qValue,
  onQDebouncedChange,
  searchDisabled,
  searchLoading,
  filterBadgeCount,
  selectedMeetingTypeSlugs,
  onMeetingTypeSlugsChange,
  onAdvancedFiltersApply,
  onPopoverCancel,
  canClear,
  onClearAll,
  ...panelProps
}: MeetingDiscoveryToolbarProps) {
  const [filterOpen, setFilterOpen] = useState(false);
  const justAppliedRef = useRef(false);

  const handleFilterOpenChange = (open: boolean) => {
    if (open) {
      justAppliedRef.current = false;
      setFilterOpen(true);
      return;
    }
    if (!justAppliedRef.current) {
      onPopoverCancel();
    }
    justAppliedRef.current = false;
    setFilterOpen(false);
  };

  const handleApplyAndClose = () => {
    justAppliedRef.current = true;
    setFilterOpen(false);
  };

  const handleCancel = () => {
    onPopoverCancel();
    setFilterOpen(false);
  };

  return (
    <div className="flex w-full flex-wrap items-center gap-3">
      <MeetingToolbarSearch
        value={qValue}
        onDebouncedChange={onQDebouncedChange}
        disabled={searchDisabled}
        isLoading={searchLoading}
      />

      <MeetingTypeMultiSelectButton
        value={selectedMeetingTypeSlugs}
        onChange={onMeetingTypeSlugsChange}
        disabled={panelProps.disabled}
      />

      <Popover open={filterOpen} onOpenChange={handleFilterOpenChange}>
        <PopoverTrigger asChild>
          <button
            type="button"
            data-testid="meetings-filter-trigger"
            disabled={panelProps.disabled}
            className={cn(
              'inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:opacity-50 sm:w-auto',
            )}
            aria-expanded={filterOpen}
            aria-haspopup="true"
            aria-label="Filter meetings"
          >
            <Filter className="h-4 w-4 text-slate-500" aria-hidden />
            <span>Filter</span>
            {filterBadgeCount > 0 ? (
              <span className="min-w-[1.25rem] rounded-full bg-[#3CCED7] px-1.5 py-0.5 text-center text-xs font-semibold text-white">
                {filterBadgeCount}
              </span>
            ) : null}
          </button>
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="start"
          sideOffset={8}
          collisionPadding={16}
          className={cn(
            'max-h-[min(85vh,680px)] w-[min(100vw-2rem,720px)] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-4 shadow-xl sm:p-5',
          )}
          onInteractOutside={(e) => {
            const el = e.target as HTMLElement;
            if (
              el.closest('[data-radix-popper-content-wrapper]') ||
              el.closest('[data-radix-portal]')
            ) {
              e.preventDefault();
            }
          }}
        >
          <div role="region" aria-label="Meeting filters">
            <div className="mb-4 flex flex-row items-start justify-between gap-4 border-b border-slate-100 pb-4">
              <div className="min-w-0 flex-1">
                <h2 className="text-base font-semibold text-slate-900">
                  Filters
                </h2>
              </div>
              {canClear ? (
                <button
                  type="button"
                  onClick={onClearAll}
                  className="shrink-0 text-sm font-medium text-[#3CCED7] hover:text-[#1a9ba3]"
                >
                  Clear
                </button>
              ) : null}
            </div>
            <MeetingFiltersPanel
              {...panelProps}
              onAdvancedFiltersApply={(payload) => {
                onAdvancedFiltersApply(payload);
                handleApplyAndClose();
              }}
              onClosePanel={handleCancel}
            />
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
