'use client';

import { Check, ChevronDown } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { MEETING_TYPE_FILTER_OPTIONS } from '@/lib/meetings/meetingTypeSlugs';
import { cn } from '@/lib/utils';

export type MeetingTypeMultiSelectButtonProps = {
  /** Selected type slugs (OR). Empty = no filter. */
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  /** Optional D12 configuration seam. Omitted means all meeting types are visible. */
  visibleTypeSlugs?: string[];
};

/**
 * Toolbar control: multi-select meeting types with checkboxes; changes apply immediately via `onChange`.
 */
export function MeetingTypeMultiSelectButton({
  value,
  onChange,
  disabled,
  visibleTypeSlugs,
}: MeetingTypeMultiSelectButtonProps) {
  const visibleOptions = visibleTypeSlugs
    ? MEETING_TYPE_FILTER_OPTIONS.filter((option) =>
        visibleTypeSlugs.includes(option.slug),
      )
    : MEETING_TYPE_FILTER_OPTIONS;

  const n = value.length;
  const label =
    n === 0 ? 'Meeting type' : n === 1 ? 'Meeting type · 1' : `Meeting type · ${n}`;

  const toggle = (slug: string) => {
    if (value.includes(slug)) {
      onChange(value.filter((s) => s !== slug));
    } else {
      onChange([...value, slug]);
    }
  };

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          className={cn(
            'h-9 w-full justify-between gap-1.5 border-slate-200 bg-white px-3 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50 sm:w-auto',
            n > 0 && 'border-[#3CCED7]/30 bg-[#3CCED7]/10/90 text-[#0f757a]',
          )}
          aria-label="Filter by meeting type"
          data-testid="meetings-meeting-type-trigger"
        >
          <span className="max-w-[12rem] truncate">{label}</span>
          <ChevronDown className="h-4 w-4 shrink-0 opacity-60" aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="z-[60] w-[min(100vw-2rem,20rem)] p-0"
        align="start"
        sideOffset={4}
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <div className="max-h-[min(60vh,320px)] overflow-y-auto py-1">
          {visibleOptions.map((o) => {
            const checked = value.includes(o.slug);
            return (
              <button
                key={o.slug}
                type="button"
                role="checkbox"
                aria-checked={checked}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-slate-900 hover:bg-slate-50"
                onClick={() => toggle(o.slug)}
              >
                <span
                  className={cn(
                    'flex h-4 w-4 shrink-0 items-center justify-center rounded border border-slate-300',
                    checked && 'border-[#3CCED7] bg-[#3CCED7] text-white',
                  )}
                >
                  {checked ? (
                    <Check className="h-3 w-3" strokeWidth={3} aria-hidden />
                  ) : null}
                </span>
                <span>{o.label}</span>
              </button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
