'use client';

import { useMemo, useState } from 'react';
import { Search, SlidersHorizontal } from 'lucide-react';
import InlineSelect, { type InlineSelectOption } from '@/components/tasks/detail/InlineSelect';
import AdvancedFilterDialog, {
  type AdvancedFilterState,
  countActiveAdvancedFilters,
} from './AdvancedFilterDialog';

const ALL_TYPES_VALUE = '__all__';
const ALL_TAGS_VALUE = '__all__';

interface Props {
  search: string;
  onSearchChange: (value: string) => void;
  typeSlug: string;
  onTypeSlugChange: (slug: string) => void;
  typeOptions: { slug: string; label: string }[];
  tagSlug: string;
  onTagSlugChange: (slug: string) => void;
  tagOptions: { slug: string; label: string }[];
  advanced: AdvancedFilterState;
  onAdvancedChange: (next: AdvancedFilterState) => void;
}

export default function MeetingsFilterBar({
  search,
  onSearchChange,
  typeSlug,
  onTypeSlugChange,
  typeOptions,
  tagSlug,
  onTagSlugChange,
  tagOptions,
  advanced,
  onAdvancedChange,
}: Props) {
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const typeSelectOptions: InlineSelectOption[] = useMemo(
    () => [
      { value: ALL_TYPES_VALUE, label: 'All types' },
      ...typeOptions.map((t) => ({ value: t.slug, label: t.label })),
    ],
    [typeOptions],
  );

  const tagSelectOptions: InlineSelectOption[] = useMemo(
    () => [
      { value: ALL_TAGS_VALUE, label: 'All tags' },
      ...tagOptions.map((t) => ({ value: t.slug, label: t.label })),
    ],
    [tagOptions],
  );

  const activeAdvancedCount = countActiveAdvancedFilters(advanced);

  return (
    <section className="rounded-xl bg-white p-3 shadow-sm ring-1 ring-gray-100">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 basis-full sm:min-w-[220px] sm:flex-1 sm:basis-0">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400"
            aria-hidden="true"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder="Search titles, summaries & transcripts…"
            aria-label="Search titles, summaries and transcripts"
            className="w-full rounded-md border border-gray-200 bg-white py-1.5 pl-8 pr-3 text-sm text-gray-900 outline-none transition placeholder:text-gray-400 focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </div>

        <div className="w-full sm:w-44">
          <InlineSelect
            ariaLabel="Filter by meeting type"
            value={typeSlug || ALL_TYPES_VALUE}
            onValueChange={(v) => onTypeSlugChange(v === ALL_TYPES_VALUE ? '' : v)}
            options={typeSelectOptions}
          />
        </div>

        {tagOptions.length > 0 && (
          <div className="w-full sm:w-40">
            <InlineSelect
              ariaLabel="Filter by tag"
              value={tagSlug || ALL_TAGS_VALUE}
              onValueChange={(v) => onTagSlugChange(v === ALL_TAGS_VALUE ? '' : v)}
              options={tagSelectOptions}
            />
          </div>
        )}

        <button
          type="button"
          onClick={() => setAdvancedOpen(true)}
          className="inline-flex h-9 w-full items-center justify-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 text-sm font-medium text-gray-700 transition hover:border-gray-300 sm:w-auto"
        >
          <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Advanced</span>
          {activeAdvancedCount > 0 && (
            <span className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-full bg-[#3CCED7]/15 px-1 text-[11px] font-semibold text-[#0E8A96]">
              {activeAdvancedCount}
            </span>
          )}
        </button>
      </div>

      <AdvancedFilterDialog
        open={advancedOpen}
        onOpenChange={setAdvancedOpen}
        value={advanced}
        onApply={onAdvancedChange}
      />
    </section>
  );
}
