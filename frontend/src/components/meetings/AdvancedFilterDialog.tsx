'use client';

import { useEffect, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { MeetingListQueryParams } from '@/types/meeting';

export interface AdvancedFilterState {
  date_from: string;
  date_to: string;
  is_archived: boolean | null;
  has_generated_decisions: boolean | null;
  has_generated_tasks: boolean | null;
}

export const EMPTY_ADVANCED_FILTER: AdvancedFilterState = {
  date_from: '',
  date_to: '',
  is_archived: null,
  has_generated_decisions: null,
  has_generated_tasks: null,
};

export function advancedFilterToParams(
  state: AdvancedFilterState,
): Partial<MeetingListQueryParams> {
  const out: Partial<MeetingListQueryParams> = {};
  if (state.date_from) out.date_from = state.date_from;
  if (state.date_to) out.date_to = state.date_to;
  if (state.is_archived != null) out.is_archived = state.is_archived;
  if (state.has_generated_decisions != null)
    out.has_generated_decisions = state.has_generated_decisions;
  if (state.has_generated_tasks != null)
    out.has_generated_tasks = state.has_generated_tasks;
  return out;
}

export function countActiveAdvancedFilters(state: AdvancedFilterState): number {
  let n = 0;
  if (state.date_from) n++;
  if (state.date_to) n++;
  if (state.is_archived != null) n++;
  if (state.has_generated_decisions != null) n++;
  if (state.has_generated_tasks != null) n++;
  return n;
}

export type AdvancedFilterField =
  | 'date_from'
  | 'date_to'
  | 'has_generated_decisions'
  | 'has_generated_tasks'
  | 'is_archived';

export type AdvancedFilterVisibility = Partial<
  Record<AdvancedFilterField, boolean>
>;

export function maskAdvancedFilter(
  state: AdvancedFilterState,
  visibility?: AdvancedFilterVisibility,
): AdvancedFilterState {
  const visible = (field: AdvancedFilterField) => visibility?.[field] ?? true;
  return {
    date_from: visible('date_from') ? state.date_from : '',
    date_to: visible('date_to') ? state.date_to : '',
    has_generated_decisions: visible('has_generated_decisions')
      ? state.has_generated_decisions
      : null,
    has_generated_tasks: visible('has_generated_tasks')
      ? state.has_generated_tasks
      : null,
    is_archived: visible('is_archived') ? state.is_archived : null,
  };
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: AdvancedFilterState;
  onApply: (state: AdvancedFilterState) => void;
  fieldVisibility?: AdvancedFilterVisibility;
}

function TriState({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (next: boolean | null) => void;
}) {
  const options: { key: 'any' | 'yes' | 'no'; text: string; v: boolean | null }[] = [
    { key: 'any', text: 'Any', v: null },
    { key: 'yes', text: 'Yes', v: true },
    { key: 'no', text: 'No', v: false },
  ];
  return (
    <div>
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-gray-500">
        {label}
      </div>
      <div className="inline-flex overflow-hidden rounded-md ring-1 ring-gray-200">
        {options.map((opt) => {
          const active = opt.v === value;
          return (
            <button
              key={opt.key}
              type="button"
              onClick={() => onChange(opt.v)}
              className={`px-3 py-1 text-xs transition ${
                active
                  ? 'bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              {opt.text}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function AdvancedFilterDialog({
  open,
  onOpenChange,
  value,
  onApply,
  fieldVisibility,
}: Props) {
  const visible = (field: AdvancedFilterField) =>
    fieldVisibility?.[field] ?? true;

  const [draft, setDraft] = useState<AdvancedFilterState>(value);

  useEffect(() => {
    if (open) setDraft(value);
  }, [open, value]);

  const handleApply = () => {
    const next = maskAdvancedFilter(draft, fieldVisibility);

    if (next.date_from && next.date_to && next.date_from > next.date_to) {
      return;
    }
    onApply(next);
    onOpenChange(false);
  };

  const handleReset = () => setDraft(EMPTY_ADVANCED_FILTER);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl bg-white shadow-2xl ring-1 ring-gray-100 outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
          <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
          <div className="flex items-start justify-between px-5 pt-4">
            <div className="min-w-0">
              <Dialog.Title className="text-[15px] font-semibold text-gray-900">
                Advanced filters
              </Dialog.Title>
              <Dialog.Description className="mt-0.5 text-xs text-gray-500">
                Narrow meetings by date range, generated artifacts, or archived state.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="-mr-1 inline-flex h-7 w-7 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>

          <div className="space-y-5 px-5 pb-5 pt-4">
            {(visible('date_from') || visible('date_to')) && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {visible('date_from') && <div>
                <label
                  htmlFor="filter-date-from"
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-gray-500"
                >
                  Scheduled from
                </label>
                <input
                  id="filter-date-from"
                  type="date"
                  value={draft.date_from}
                  onChange={(e) => setDraft({ ...draft, date_from: e.target.value })}
                  className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
                />
              </div>}
              {visible('date_to') && <div>
                <label
                  htmlFor="filter-date-to"
                  className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-gray-500"
                >
                  Scheduled to
                </label>
                <input
                  id="filter-date-to"
                  type="date"
                  value={draft.date_to}
                  onChange={(e) => setDraft({ ...draft, date_to: e.target.value })}
                  className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
                />
              </div>}
            </div>
            )}
            {visible('date_from') && visible('date_to') &&
              draft.date_from && draft.date_to && draft.date_from > draft.date_to && (
              <p className="text-xs text-rose-600">End date must be on or after start date.</p>
            )}

            {(visible('has_generated_decisions') || visible('has_generated_tasks') || visible('is_archived')) && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {visible('has_generated_decisions') && <TriState
                label="Has generated decisions"
                value={draft.has_generated_decisions}
                onChange={(v) => setDraft({ ...draft, has_generated_decisions: v })}
              />}
              {visible('has_generated_tasks') && <TriState
                label="Has generated tasks"
                value={draft.has_generated_tasks}
                onChange={(v) => setDraft({ ...draft, has_generated_tasks: v })}
              />}
              {visible('is_archived') && <TriState
                label="Archived"
                value={draft.is_archived}
                onChange={(v) => setDraft({ ...draft, is_archived: v })}
              />}
            </div>
            )}

            <div className="flex flex-col-reverse gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="button"
                onClick={handleReset}
                className="text-xs font-medium text-gray-500 transition hover:text-gray-900"
              >
                Reset
              </button>
              <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center">
                <button
                  type="button"
                  onClick={() => onOpenChange(false)}
                  className="inline-flex h-9 w-full items-center justify-center rounded-lg bg-white px-4 text-sm font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300 sm:w-auto"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleApply}
                  className="inline-flex h-9 w-full items-center justify-center rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-4 text-sm font-semibold text-white shadow-sm transition hover:opacity-95 sm:w-auto"
                >
                  Apply
                </button>
              </div>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
