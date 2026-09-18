'use client';

import { useEffect, useRef, useState } from 'react';
import { ArrowUpDown, Check } from 'lucide-react';
import type { MeetingListItem } from '@/types/meeting';
import {
  applyMeetingSort,
  MEETING_SORT_OPTIONS,
  type MeetingSortKey,
} from '@/lib/meetings/meetingSectionSort';
import MeetingCard from './MeetingCard';
import MeetingEmptyColumn from './MeetingEmptyColumn';

interface Props {
  title: string;
  variant: 'incoming' | 'completed';
  meetings: MeetingListItem[];
  resultCount: number;
  laneTotal: number;
  sortKey: MeetingSortKey;
  onSortChange: (key: MeetingSortKey) => void;
  projectId: number | string;
  onCreate?: () => void;
  searchQuery?: string;
}

function SortMenu({
  value,
  onChange,
  ariaLabel,
}: {
  value: MeetingSortKey;
  onChange: (key: MeetingSortKey) => void;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!ref.current) return;
      if (!ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const activeLabel =
    MEETING_SORT_OPTIONS.find((o) => o.value === value)?.label ?? 'Sort';

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 text-xs font-medium text-gray-700 transition hover:border-gray-300"
      >
        <ArrowUpDown className="h-3 w-3" aria-hidden="true" />
        <span>{activeLabel}</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-40 mt-1 w-44 overflow-hidden rounded-lg bg-white shadow-lg ring-1 ring-gray-100"
        >
          {MEETING_SORT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              role="menuitem"
              onClick={() => {
                onChange(opt.value);
                setOpen(false);
              }}
              className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-sm text-gray-800 transition hover:bg-gray-50"
            >
              <span>{opt.label}</span>
              {opt.value === value && (
                <Check className="h-3.5 w-3.5 text-[#3CCED7]" aria-hidden="true" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function MeetingColumn({
  title,
  variant,
  meetings,
  resultCount,
  laneTotal,
  sortKey,
  onSortChange,
  projectId,
  onCreate,
  searchQuery,
}: Props) {
  const sorted = applyMeetingSort(meetings, sortKey);
  const hasRows = sorted.length > 0;

  return (
    <section className="flex min-h-[360px] flex-col rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 flex items-center gap-2">
          <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
            {title}
          </h2>
          <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
            {resultCount} of {laneTotal}
          </span>
        </div>
        <SortMenu
          ariaLabel={`Sort ${variant} meetings`}
          value={sortKey}
          onChange={onSortChange}
        />
      </header>

      {hasRows ? (
        <ul className="flex flex-col gap-2.5">
          {sorted.map((m) => (
            <li key={m.id}>
              <MeetingCard meeting={m} projectId={projectId} searchQuery={searchQuery} />
            </li>
          ))}
        </ul>
      ) : (
        <MeetingEmptyColumn variant={variant} onCreate={onCreate} />
      )}
    </section>
  );
}
