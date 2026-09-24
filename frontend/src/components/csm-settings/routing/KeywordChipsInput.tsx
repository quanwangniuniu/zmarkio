'use client';

import { useState } from 'react';
import { X } from 'lucide-react';
import { BUILDER_CONTROL_CLASS } from '../constants';

interface Props {
  id?: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  max?: number;
  disabled?: boolean;
}

/** Free-text chips: Enter or comma adds a value, Backspace on empty input removes the last. */
export default function KeywordChipsInput({ id, values, onChange, placeholder, max, disabled }: Props) {
  const [draft, setDraft] = useState('');
  const atLimit = max !== undefined && values.length >= max;

  const commit = () => {
    const next = draft.trim();
    setDraft('');
    if (!next || atLimit) return;
    if (values.some((v) => v.toLowerCase() === next.toLowerCase())) return;
    onChange([...values, next]);
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {values.map((value) => (
        <span
          key={value}
          className="inline-flex items-center gap-1 rounded-full bg-cyan-50 px-2 py-0.5 text-xs font-medium text-cyan-800"
        >
          {value}
          <button
            type="button"
            onClick={() => onChange(values.filter((v) => v !== value))}
            disabled={disabled}
            aria-label={`Remove ${value}`}
            className="text-cyan-600 hover:text-cyan-900"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </span>
      ))}
      <input
        id={id}
        value={draft}
        disabled={disabled || atLimit}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            commit();
          } else if (e.key === 'Backspace' && !draft && values.length > 0) {
            onChange(values.slice(0, -1));
          }
        }}
        placeholder={atLimit ? 'Limit reached' : placeholder}
        className={`${BUILDER_CONTROL_CLASS} min-w-[10rem] flex-1`}
      />
    </div>
  );
}
