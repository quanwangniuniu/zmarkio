'use client';

import { AlertTriangle, ArrowRight, ChevronDown, ChevronRight } from 'lucide-react';
import type { RoutingTrace } from '@/types/routingRule';
import type { SummaryLookups } from '../routing/conditionSummary';
import RuleStepItem from './RuleStepItem';

const FALLBACK_LABELS = {
  channel_default_queue: "Channel's default queue",
  first_organisation_queue: "Organisation's first queue",
  none: 'No queue',
};

interface Props {
  trace: RoutingTrace;
  turn: number;
  message: string;
  previous: RoutingTrace | null;
  expanded: boolean;
  lookups: SummaryLookups;
  onToggle: () => void;
}

export default function TraceTurnCard({ trace, turn, message, previous, expanded, lookups, onToggle }: Props) {
  const { outcome } = trace;
  const changed = previous !== null && previous.outcome.queue_id !== outcome.queue_id;
  const decidedBy =
    outcome.decided_by === 'rule'
      ? `rule “${outcome.rule_name}”`
      : outcome.decided_by === 'fallback' ? 'fallback' : 'nothing matched';

  return (
    <section className="rounded-xl border border-gray-200 bg-white" data-testid="trace-turn">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left"
      >
        {expanded
          ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" aria-hidden />
          : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" aria-hidden />}
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium uppercase tracking-wider text-gray-400">Message {turn}</p>
          <p className="truncate text-xs text-gray-600">“{message}”</p>
          <p className="mt-1 flex flex-wrap items-center gap-1 text-sm text-gray-900">
            <ArrowRight className="h-3.5 w-3.5 text-gray-400" aria-hidden />
            <span className="font-semibold" data-testid="trace-outcome">{outcome.queue_name ?? 'Unqueued'}</span>
            <span className="text-xs text-gray-500">via {decidedBy}</span>
            {changed && (
              <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-800">
                Changed from {previous!.outcome.queue_name ?? 'unqueued'}
              </span>
            )}
          </p>
        </div>
      </button>

      {expanded && (
        <div className="flex flex-col gap-2 border-t border-gray-100 px-3 py-3">
          {trace.steps.length === 0 ? (
            <p className="text-xs text-gray-500">No routing rules in this experience group.</p>
          ) : (
            <ol className="flex flex-col gap-2">
              {trace.steps.map((step) => <RuleStepItem key={step.rule_id} step={step} lookups={lookups} />)}
            </ol>
          )}

          {trace.fallback && (
            <div className="rounded-lg border border-dashed border-gray-300 p-3 text-xs" data-testid="trace-fallback">
              <p className="font-medium text-gray-800">
                Fallback: {FALLBACK_LABELS[trace.fallback.source]}
                {trace.fallback.queue_name && <> → {trace.fallback.queue_name}</>}
              </p>
              <p className="mt-1 text-gray-500">{trace.fallback.reason}</p>
            </div>
          )}

          {outcome.tags.length > 0 && (
            <p className="text-xs text-gray-600">Tags added: {outcome.tags.join(', ')}</p>
          )}

          {trace.warnings.map((w) => (
            <p key={w} className="flex items-start gap-1.5 text-xs text-amber-700">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {w}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
