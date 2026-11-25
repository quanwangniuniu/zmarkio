'use client';

import { CheckCircle2, Circle } from 'lucide-react';
import type { DecisionOptionDraft, DecisionRiskLevel, DecisionSignal } from '@/types/decision';
import { isConfidenceOutOfRange } from '@/components/decisions/decisionValidation';

interface Props {
  contextSummary: string;
  options: DecisionOptionDraft[];
  reasoning: string;
  signals: DecisionSignal[];
  riskLevel: DecisionRiskLevel | null;
  confidenceScore: number | null;
  onJump?: (anchorId: string) => void;
}

interface Item {
  key: string;
  label: string;
  ok: boolean;
  anchorId: string;
}

export default function DecisionCommitChecklistAside({
  contextSummary,
  options,
  reasoning,
  signals,
  riskLevel,
  confidenceScore,
  onJump,
}: Props) {
  const nonEmptyOptions = options.filter((o) => (o.text ?? '').trim().length > 0);
  const selectedCount = options.filter((o) => o.isSelected).length;

  const items: Item[] = [
    {
      key: 'context',
      label: 'Context summary',
      ok: contextSummary.trim().length > 0,
      anchorId: 'decision-field-context',
    },
    {
      key: 'options',
      label: 'At least two options',
      ok: nonEmptyOptions.length >= 2,
      anchorId: 'decision-field-options',
    },
    {
      key: 'selected',
      label: 'One option selected',
      ok: selectedCount === 1,
      anchorId: 'decision-field-options',
    },
    {
      key: 'reasoning',
      label: 'Reasoning',
      ok: reasoning.trim().length > 0,
      anchorId: 'decision-field-reasoning',
    },
    {
      key: 'signals',
      label: 'At least one signal',
      ok: signals.length >= 1,
      anchorId: 'decision-field-signals',
    },
    {
      key: 'risk',
      label: 'Risk level',
      ok: !!riskLevel,
      anchorId: 'decision-field-risk',
    },
    {
      key: 'confidence',
      label: 'Confidence score',
      ok: confidenceScore != null && !isConfidenceOutOfRange(confidenceScore),
      anchorId: 'decision-field-confidence',
    },
  ];

  const okCount = items.filter((i) => i.ok).length;
  const total = items.length;
  const allReady = okCount === total;

  return (
    <section className="rounded-lg bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:rounded-xl">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
          Commit readiness
        </h3>
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
            allReady
              ? 'bg-emerald-50 text-emerald-700'
              : 'bg-amber-50 text-amber-700'
          }`}
        >
          {okCount}/{total}
        </span>
      </div>

      <ul className="space-y-1.5">
        {items.map((item) => {
          const Icon = item.ok ? CheckCircle2 : Circle;
          return (
            <li key={item.key}>
              <button
                type="button"
                onClick={() => onJump?.(item.anchorId)}
                className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left transition hover:bg-gray-50"
              >
                <Icon
                  className={`h-4 w-4 shrink-0 ${
                    item.ok ? 'text-emerald-500' : 'text-gray-300'
                  }`}
                />
                <span
                  className={`text-[13px] ${
                    item.ok ? 'text-gray-500 line-through' : 'text-gray-800'
                  }`}
                >
                  {item.label}
                </span>
              </button>
            </li>
          );
        })}
      </ul>

      {allReady ? (
        <p className="mt-3 rounded-md bg-emerald-50 px-2.5 py-1.5 text-[11px] text-emerald-700">
          All requirements met. Ready to commit.
        </p>
      ) : (
        <p className="mt-3 text-[11px] text-gray-500">
          Needed to commit — not errors. Drafts save anytime.
        </p>
      )}
    </section>
  );
}
