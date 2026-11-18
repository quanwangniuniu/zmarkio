'use client';

import { Pencil, Plus, Trash2 } from 'lucide-react';
import { SIGNAL_METRIC, SIGNAL_MOVEMENT, SIGNAL_PERIOD } from '@/lib/decisions/decisionConfig';
import { useOptionConfigVersion } from '@/lib/orgCustomization/configLoader';
import { optionLabel } from '@/lib/orgCustomization/optionConfig';
import type { DecisionSignal } from '@/types/decision';

interface Props {
  signals: DecisionSignal[];
  editable: boolean;
  topError?: string | null;
  onAdd: () => void;
  onEdit: (signal: DecisionSignal) => void;
  onDelete: (signal: DecisionSignal) => void;
}

export default function DecisionSignalsSection({
  signals,
  editable,
  topError,
  onAdd,
  onEdit,
  onDelete,
}: Props) {
  useOptionConfigVersion();
  return (
    <section
      id="decision-section-signals"
      className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:rounded-xl sm:p-5"
    >
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Signals
          {signals.length > 0 && (
            <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">
              {signals.length}
            </span>
          )}
        </h2>
        {editable && (
          <button
            id="decision-field-signals"
            type="button"
            onClick={onAdd}
            className="inline-flex h-8 w-full items-center justify-center gap-1.5 rounded-md bg-white px-3 text-xs font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300 sm:w-auto"
          >
            <Plus className="h-3.5 w-3.5" />
            Add signal
          </button>
        )}
      </div>

      {topError && (
        <div className="mb-3 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-700">
          {topError}
        </div>
      )}

      {signals.length === 0 ? (
        <p className="text-xs text-gray-400">
          {editable
            ? 'No signals yet. Add at least one signal before committing.'
            : 'No signals recorded.'}
        </p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {signals.map((signal) => (
            <li
              key={signal.id}
              className="group flex items-start justify-between gap-3 py-2.5"
            >
              <div className="min-w-0 flex-1">
                <div className="break-words text-sm text-gray-900">
                  {signal.displayTextOverride || signal.displayText || '—'}
                </div>
                <div className="mt-0.5 break-words text-[11px] text-gray-500">
                  {optionLabel(SIGNAL_METRIC, signal.metric)} · {optionLabel(SIGNAL_MOVEMENT, signal.movement)} · {optionLabel(SIGNAL_PERIOD, signal.period)}
                  {signal.comparison && signal.comparison !== 'NONE' && ` · ${signal.comparison}`}
                  {signal.scopeType && ` · ${signal.scopeType}`}
                  {signal.scopeValue && `: ${signal.scopeValue}`}
                </div>
              </div>
              {editable && (
                <div className="flex items-center gap-1 transition sm:opacity-0 sm:group-hover:opacity-100">
                  <button
                    type="button"
                    onClick={() => onEdit(signal)}
                    aria-label="Edit signal"
                    title="Edit"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-50 hover:text-gray-900"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(signal)}
                    aria-label="Delete signal"
                    title="Delete"
                    className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-rose-50 hover:text-rose-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
