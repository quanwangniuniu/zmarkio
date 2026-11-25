'use client';

import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Check, Plus, Trash2 } from 'lucide-react';
import type { DecisionOptionDraft } from '@/types/decision';
import { FIELD_MESSAGES, isOptionTextBlank } from '@/components/decisions/decisionValidation';
import {
  applySaved,
  buildOptionsSave,
  newOptionRow,
  optionsKey,
  rowsFromOptions,
  type OptionRow,
} from './optionRows';

interface Props {
  value: DecisionOptionDraft[];
  editable: boolean;
  error?: string | null;
  selectedError?: string | null;
  onSave: (next: DecisionOptionDraft[]) => void | Promise<void>;
}

export default function DecisionOptionsSection({
  value,
  editable,
  error,
  selectedError,
  onSave,
}: Props) {
  const [rows, setRowsState] = useState<OptionRow[]>(() => rowsFromOptions(value));
  const [blankShown, setBlankShown] = useState<Set<string>>(() => new Set());
  const [savingCount, setSavingCount] = useState(0);

  // Handlers can run right after a blur, before re-render; read rows from here.
  const rowsRef = useRef(rows);
  const setRows = (next: OptionRow[]) => {
    rowsRef.current = next;
    setRowsState(next);
  };

  const syncedKeyRef = useRef(optionsKey(value));
  const sentRef = useRef<{ key: string; sent: Map<string, string> }[]>([]);
  const requestingRef = useRef(new Set<string>());

  // Saving any field returns a new options array; reset only on a real content change.
  useEffect(() => {
    const key = optionsKey(value);
    if (key === syncedKeyRef.current) return;
    syncedKeyRef.current = key;

    const index = sentRef.current.findIndex((s) => s.key === key);
    if (index >= 0) {
      // Our own save: keep local edits.
      const { sent } = sentRef.current[index];
      sentRef.current = sentRef.current.slice(index + 1);
      setRows(applySaved(rowsRef.current, sent));
      return;
    }

    sentRef.current = [];
    setRows(rowsFromOptions(value));
    setBlankShown(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const showBlank = (key: string, show: boolean) =>
    setBlankShown((prev) => {
      if (prev.has(key) === show) return prev;
      const next = new Set(prev);
      if (show) next.add(key);
      else next.delete(key);
      return next;
    });

  const save = async (next: OptionRow[]) => {
    if (!editable) return;
    const { options, sent } = buildOptionsSave(next);
    const key = optionsKey(options);
    if (key === syncedKeyRef.current || requestingRef.current.has(key)) return;
    sentRef.current = [...sentRef.current.slice(-19), { key, sent }];
    requestingRef.current.add(key);
    setSavingCount((n) => n + 1);
    try {
      await onSave(options);
    } catch {
      // Toast already shown; keep edits.
    } finally {
      requestingRef.current.delete(key);
      setSavingCount((n) => n - 1);
    }
  };

  const addOption = () => {
    // Not saved until it has text (the backend rejects blank options).
    setRows([...rowsRef.current, newOptionRow()]);
  };

  const changeText = (key: string, text: string) => {
    setRows(rowsRef.current.map((row) => (row.key === key ? { ...row, text } : row)));
    showBlank(key, false);
  };

  const blurText = (key: string) => {
    const row = rowsRef.current.find((r) => r.key === key);
    if (row && isOptionTextBlank(row.text)) showBlank(key, true);
    void save(rowsRef.current);
  };

  const selectOption = (key: string) => {
    const next = rowsRef.current.map((row) => ({ ...row, isSelected: row.key === key }));
    setRows(next);
    const row = next.find((r) => r.key === key);
    if (row && isOptionTextBlank(row.text)) showBlank(key, true);
    void save(next);
  };

  const deleteOption = (key: string) => {
    const next = rowsRef.current.filter((row) => row.key !== key);
    setRows(next);
    showBlank(key, false);
    void save(next);
  };

  const groupError = error || selectedError || null;

  return (
    <section
      id="decision-section-options"
      className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:rounded-xl sm:p-5"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Options
          {rows.length > 0 && (
            <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">
              {rows.length}
            </span>
          )}
        </h2>
        {savingCount > 0 && <span className="text-[11px] text-gray-400">Saving…</span>}
      </div>

      {rows.length === 0 ? (
        <p id="decision-field-options" className="rounded-md text-xs text-gray-400 transition">
          No options yet.
        </p>
      ) : (
        <ul id="decision-field-options" className="space-y-2 rounded-md transition">
          {rows.map((row, i) => (
            <li key={row.key} className="group flex items-start gap-2">
              {editable ? (
                <button
                  type="button"
                  onClick={() => selectOption(row.key)}
                  aria-label={row.isSelected ? 'Selected option' : 'Mark as selected'}
                  className={`mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition ${
                    row.isSelected
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
                    row.isSelected ? 'bg-[#3CCED7] text-white' : 'bg-gray-100 text-gray-300'
                  }`}
                >
                  {row.isSelected && <Check className="h-3 w-3" />}
                </span>
              )}
              {editable ? (
                <div className="min-w-0 flex-1">
                  <textarea
                    value={row.text}
                    onChange={(e) => changeText(row.key, e.target.value)}
                    onBlur={() => blurText(row.key)}
                    rows={1}
                    placeholder={`Option ${i + 1}`}
                    aria-invalid={blankShown.has(row.key) || undefined}
                    aria-describedby={blankShown.has(row.key) ? `option-error-${row.key}` : undefined}
                    className={`min-h-[32px] w-full resize-none rounded-md border bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder:text-gray-400 outline-none transition ${
                      blankShown.has(row.key)
                        ? 'border-rose-400 focus:border-rose-500 focus:ring-2 focus:ring-rose-200'
                        : 'border-gray-200 focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30'
                    }`}
                  />
                  {blankShown.has(row.key) && (
                    <p id={`option-error-${row.key}`} className="mt-1 flex items-start gap-1 text-[12px] text-rose-600">
                      <AlertCircle aria-hidden="true" className="mt-px h-3.5 w-3.5 shrink-0" />
                      {FIELD_MESSAGES.optionEmpty}
                    </p>
                  )}
                </div>
              ) : (
                <span className="min-h-[32px] flex-1 break-words whitespace-pre-wrap py-1 text-sm text-gray-900">
                  {row.text || <span className="text-gray-400">—</span>}
                </span>
              )}
              {editable && (
                <button
                  type="button"
                  // Keep focus so the field doesn't blur and flash an error before deleting.
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => deleteOption(row.key)}
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

      {groupError && (
        <p role="alert" className="mt-2 flex items-start gap-1 text-[12px] text-rose-600">
          <AlertCircle aria-hidden="true" className="mt-px h-3.5 w-3.5 shrink-0" />
          {groupError}
        </p>
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
