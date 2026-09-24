'use client';

import { Check, X } from 'lucide-react';
import type { RuleStep, RuleStepStatus } from '@/types/routingRule';
import { formatConditionValue, type SummaryLookups } from '../routing/conditionSummary';

const STATUS_STYLES: Record<RuleStepStatus, { label: string; className: string }> = {
  matched: { label: 'Matched', className: 'bg-green-100 text-green-800' },
  not_matched: { label: 'No match', className: 'bg-gray-100 text-gray-700' },
  disabled: { label: 'Disabled', className: 'bg-gray-100 text-gray-500' },
  invalid_action: { label: 'Skipped', className: 'bg-amber-100 text-amber-800' },
  not_reached: { label: 'Not reached', className: 'bg-white text-gray-400 ring-1 ring-gray-200' },
};

function fieldLabel(lookups: SummaryLookups, field: string) {
  return lookups.vocabulary?.fields.find((f) => f.field === field)?.label ?? field;
}

function operatorLabel(lookups: SummaryLookups, field: string, operator: string) {
  return lookups.vocabulary?.fields
    .find((f) => f.field === field)?.operators
    .find((o) => o.operator === operator)?.label ?? operator;
}

function formatActual(field: string, actual: string | number | null, lookups: SummaryLookups) {
  if (actual === null || actual === '') return '—';
  return formatConditionValue(field, typeof actual === 'number' ? [actual] : actual, lookups);
}

export default function RuleStepItem({ step, lookups }: { step: RuleStep; lookups: SummaryLookups }) {
  const style = STATUS_STYLES[step.status];
  const muted = step.status === 'not_reached' || step.status === 'disabled';

  return (
    <li className={`rounded-lg border border-gray-200 p-3 ${muted ? 'bg-gray-50' : 'bg-white'}`} data-testid="trace-step">
      <div className="flex items-center justify-between gap-2">
        <span className={`text-sm font-medium ${muted ? 'text-gray-500' : 'text-gray-900'}`}>
          {step.position + 1}. {step.rule_name}
        </span>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${style.className}`}>
          {style.label}
        </span>
      </div>

      {step.conditions.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {step.conditions.map((c) => (
            <li key={c.index} className="flex items-start gap-2 text-xs">
              {c.passed ? (
                <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-green-600" aria-label="passed" />
              ) : (
                <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" aria-label="failed" />
              )}
              <div className="min-w-0">
                <p className="text-gray-800">
                  {fieldLabel(lookups, c.field)} {operatorLabel(lookups, c.field, c.operator)}{' '}
                  {formatConditionValue(c.field, c.expected, lookups)}
                </p>
                <p className="break-words text-gray-500">
                  Actual: {formatActual(c.field, c.actual, lookups)}
                  {c.detail && <> · {c.detail}</>}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}

      {step.match_mode === 'any' && step.conditions.length > 1 && (
        <p className="mt-1 text-[11px] text-gray-400">Any condition can match.</p>
      )}
      {step.note && <p className="mt-2 text-xs text-gray-500">{step.note}</p>}
      {step.action && step.status !== 'not_reached' && (
        <p className="mt-2 text-xs text-gray-600">
          Action: route to <span className="font-medium">{step.action.queue_name ?? 'no queue'}</span>
          {step.action.add_tags.length > 0 && <> · tag {step.action.add_tags.join(', ')}</>}
        </p>
      )}
    </li>
  );
}
