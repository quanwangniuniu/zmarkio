'use client';

import { SIGNAL_METRIC, SIGNAL_MOVEMENT, SIGNAL_PERIOD } from '@/lib/decisions/decisionConfig';
import { optionLabel } from '@/lib/orgCustomization/optionConfig';
import type { DecisionSignal } from '@/types/decision';

interface Props {
  signals: DecisionSignal[];
}

export default function DecisionExecutionSummarySection({ signals }: Props) {
  return (
    <section
      id="decision-section-execution"
      className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:rounded-xl sm:p-5"
    >
      <div className="mb-3">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Execution summary
        </h2>
        <p className="mt-0.5 text-xs text-gray-500">
          Outcome signals captured for this decision.
        </p>
      </div>
      {signals.length === 0 ? (
        <p className="text-xs text-gray-400">No outcome signals yet.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {signals.map((signal) => (
            <li key={signal.id} className="py-2.5">
              <div className="break-words text-sm text-gray-900">
                {signal.displayTextOverride || signal.displayText || '—'}
              </div>
              <div className="mt-0.5 break-words text-[11px] text-gray-500">
                {optionLabel(SIGNAL_METRIC, signal.metric)} · {optionLabel(SIGNAL_MOVEMENT, signal.movement)} · {optionLabel(SIGNAL_PERIOD, signal.period)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
