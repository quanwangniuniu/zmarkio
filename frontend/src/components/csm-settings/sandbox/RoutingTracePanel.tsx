'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import type { RoutingSandboxResult, RoutingTrace } from '@/types/routingRule';
import type { SummaryLookups } from '../routing/conditionSummary';
import { SECONDARY_BUTTON_CLASS } from '../constants';
import TraceTurnCard from './TraceTurnCard';

interface Props {
  traces: RoutingTrace[];
  customerMessages: string[];
  meta: Omit<RoutingSandboxResult, 'traces'> | null;
  evaluating: boolean;
  error: string | null;
  lookups: SummaryLookups;
  onRerun: () => void;
}

export default function RoutingTracePanel({
  traces,
  customerMessages,
  meta,
  evaluating,
  error,
  lookups,
  onRerun,
}: Props) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Always open the newest turn when a new message is evaluated.
  useEffect(() => {
    if (traces.length > 0) setExpanded(new Set([traces.length - 1]));
  }, [traces.length]);

  const toggle = (index: number) => setExpanded((prev) => {
    const next = new Set(prev);
    if (next.has(index)) next.delete(index); else next.add(index);
    return next;
  });

  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-gray-500">
          {evaluating ? 'Evaluating…' : meta ? `${meta.rule_count} rule(s) evaluated against current settings.` : ''}
        </p>
        <button
          type="button"
          onClick={onRerun}
          disabled={evaluating || traces.length === 0}
          className={SECONDARY_BUTTON_CLASS}
        >
          <RefreshCw className={`h-4 w-4 ${evaluating ? 'animate-spin' : ''}`} aria-hidden />
          Re-run
        </button>
      </div>

      {error && <p className="rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">{error}</p>}

      {meta?.warnings.map((w) => (
        <p key={w} className="flex items-start gap-1.5 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {w}
        </p>
      ))}

      {traces.length === 0 ? (
        <p className="rounded-lg border-2 border-dashed border-gray-200 p-6 text-center text-sm text-gray-400">
          Send a test message to see which rules run.
        </p>
      ) : (
        traces.map((_, i) => i).reverse().map((index) => (
          <TraceTurnCard
            key={index}
            trace={traces[index]}
            turn={index + 1}
            message={customerMessages[index] ?? ''}
            previous={index > 0 ? traces[index - 1] : null}
            expanded={expanded.has(index)}
            lookups={lookups}
            onToggle={() => toggle(index)}
          />
        ))
      )}
    </div>
  );
}
