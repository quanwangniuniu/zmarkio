'use client';

import React, { useMemo, useState } from 'react';

export interface MultiSelectOption {
  value: string;
  label: string;
}

interface QualityMultiSelectProps {
  label: string;
  options: MultiSelectOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  searchable?: boolean;
  emptyLabel?: string;
}

/**
 * Compact checkbox dropdown. The repo has no shared generic multi-select —
 * the meetings panel rolls its own — so this keeps the six filters uniform
 * without pulling that participant-specific component in.
 */
export function QualityMultiSelect({
  label,
  options,
  selected,
  onChange,
  searchable = false,
  emptyLabel = 'Any',
}: QualityMultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState('');

  const visible = useMemo(() => {
    if (!searchable || !term.trim()) return options;
    const needle = term.trim().toLowerCase();
    return options.filter((option) => option.label.toLowerCase().includes(needle));
  }, [options, searchable, term]);

  const toggle = (value: string) => {
    onChange(
      selected.includes(value)
        ? selected.filter((entry) => entry !== value)
        : [...selected, value],
    );
  };

  const summary = selected.length === 0 ? emptyLabel : `${selected.length} selected`;

  return (
    <div className="relative">
      <span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-haspopup="listbox"
        // The visible text is the selection summary, so the control needs its
        // own name for screen readers (and for anything querying by role).
        aria-label={label}
        className="h-8 w-full min-w-0 rounded-md border border-slate-300 bg-white px-2 text-left text-sm text-slate-700 hover:bg-slate-50"
      >
        {summary}
      </button>

      {open && (
        <>
          {/* Click-away layer, so the panel closes without a document listener. */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden />
          <div
            role="listbox"
            aria-label={label}
            className="absolute z-20 mt-1 max-h-64 w-full min-w-[12rem] overflow-auto rounded-md border border-slate-200 bg-white p-1 shadow-lg"
          >
            {searchable && (
              <input
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                placeholder={`Search ${label.toLowerCase()}`}
                aria-label={`Search ${label}`}
                className="mb-1 w-full rounded border border-slate-200 px-2 py-1 text-sm focus:border-[#3CCED7] focus:outline-none"
              />
            )}
            {visible.length === 0 && (
              <p className="px-2 py-1.5 text-sm text-slate-400">No matches</p>
            )}
            {visible.map((option) => (
              <label
                key={option.value}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-slate-50"
              >
                <input
                  type="checkbox"
                  checked={selected.includes(option.value)}
                  onChange={() => toggle(option.value)}
                  className="h-3.5 w-3.5 rounded border-slate-300 text-[#3CCED7] focus:ring-[#3CCED7]"
                />
                <span className="truncate">{option.label}</span>
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default QualityMultiSelect;
