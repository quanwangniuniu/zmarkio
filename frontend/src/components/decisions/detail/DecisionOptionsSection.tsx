'use client';

import { useEffect, useState } from 'react';
import { Check, Plus, Trash2 } from 'lucide-react';
import type { DecisionOptionDraft } from '@/types/decision';

interface Props {
  value: DecisionOptionDraft[];
  editable: boolean;
  error?: string | null;
  selectedError?: string | null;
  stageNewOptions?: boolean;
  onSave: (next: DecisionOptionDraft[]) => void | Promise<void>;
}

export default function DecisionOptionsSection({
  value,
  editable,
  error,
  selectedError,
  stageNewOptions = false,
  onSave,
}: Props) {
  const [local, setLocal] = useState<DecisionOptionDraft[]>(value ?? []);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLocal(value ?? []);
  }, [value]);

  const commit = async (next: DecisionOptionDraft[]) => {
    setLocal(next);
    if (!editable) return;
    setSaving(true);
    try {
      await onSave(next);
    } finally {
      setSaving(false);
    }
  };

  const addOption = () => {
    const appendEmptyOption = (previous: DecisionOptionDraft[]) => [
      ...previous,
      { text: '', isSelected: false, order: previous.length },
    ];
    if (stageNewOptions) {
      void commit(appendEmptyOption(local));
      return;
    }
    setLocal(appendEmptyOption);
  };

  const updateText = (index: number, text: string) => {
    const next = local.map((opt, i) => (i === index ? { ...opt, text } : opt));
    setLocal(next);
  };

  const commitText = async (index: number) => {
    if (!error && local[index]?.text === value[index]?.text) return;
    await commit(local);
  };

  const selectOption = (index: number) => {
    const next = local.map((opt, i) => ({ ...opt, isSelected: i === index }));
    commit(next);
  };

  const deleteOption = (index: number) => {
    const next = local.filter((_, i) => i !== index).map((opt, i) => ({ ...opt, order: i }));
    commit(next);
  };

  return (
    <section
      id="decision-section-options"
      className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:rounded-xl sm:p-5"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Options
          {local.length > 0 && (
            <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">
              {local.length}
            </span>
          )}
        </h2>
        {saving && <span className="text-[11px] text-gray-400">Saving…</span>}
      </div>

      {(error || selectedError) && (
        <div className="mb-3 space-y-1">
          {error && (
            <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700">
              {error}
            </div>
          )}
          {selectedError && (
            <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700">
              {selectedError}
            </div>
          )}
        </div>
      )}

      {local.length === 0 ? (
        <p id="decision-field-options" className="rounded-md text-xs text-gray-400 transition">
          No options yet.
        </p>
      ) : (
        <ul id="decision-field-options" className="space-y-2 rounded-md transition">
          {local.map((opt, i) => (
            <li key={opt.id ?? `new-${i}`} className="group flex items-start gap-2">
              {editable ? (
                <button
                  type="button"
                  onClick={() => selectOption(i)}
                  aria-label={opt.isSelected ? 'Selected option' : 'Mark as selected'}
                  className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition ${
                    opt.isSelected
                      ? 'border-transparent bg-[#3CCED7] text-white'
                      : 'border-gray-300 text-transparent hover:border-[#3CCED7]'
                  }`}
                >
                  <Check className="h-3 w-3" />
                </button>
              ) : (
                <span
                  aria-hidden="true"
                  className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full ${
                    opt.isSelected ? 'bg-[#3CCED7] text-white' : 'bg-gray-100 text-gray-300'
                  }`}
                >
                  {opt.isSelected && <Check className="h-3 w-3" />}
                </span>
              )}
              {editable ? (
                <textarea
                  value={opt.text ?? ''}
                  onChange={(e) => updateText(i, e.target.value)}
                  onBlur={() => commitText(i)}
                  rows={1}
                  placeholder={`Option ${i + 1}`}
                  className="min-h-[32px] w-full resize-none rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 outline-none transition focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
                />
              ) : (
                <span className="min-h-[32px] flex-1 break-words whitespace-pre-wrap py-1 text-sm text-gray-900">
                  {opt.text || <span className="text-gray-400">—</span>}
                </span>
              )}
              {editable && (
                <button
                  type="button"
                  onClick={() => deleteOption(i)}
                  aria-label="Delete option"
                  title="Delete"
                  className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-gray-400 transition hover:bg-rose-50 hover:text-rose-600 sm:opacity-0 sm:group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {editable && (
        <button
          type="button"
          onClick={addOption}
          className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-500 transition hover:border-[#3CCED7]/60 hover:text-[#3CCED7] sm:w-auto"
        >
          <Plus className="h-3.5 w-3.5" />
          Add option
        </button>
      )}
    </section>
  );
}
