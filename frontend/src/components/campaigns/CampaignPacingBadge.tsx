'use client';

import type { CampaignPacingForecast, PacingReason, PacingStatus } from '@/types/campaign';

interface Props {
  pacing?: CampaignPacingForecast | null;
  /** 'compact' for table cells (badge only), 'detail' for the campaign page. */
  variant?: 'compact' | 'detail';
  className?: string;
}

const STATUS_CONFIG: Record<PacingStatus, { label: string; className: string }> = {
  on_track: { label: 'On track', className: 'border-emerald-200 bg-emerald-50 text-emerald-700' },
  under_pacing: { label: 'Under pacing', className: 'border-sky-200 bg-sky-50 text-sky-700' },
  over_pacing: { label: 'Over pacing', className: 'border-rose-200 bg-rose-50 text-rose-700' },
  not_started: { label: 'Not started', className: 'border-gray-200 bg-gray-50 text-gray-600' },
  no_data: { label: 'No spend data', className: 'border-gray-200 bg-gray-50 text-gray-600' },
  not_configured: { label: 'Not configured', className: 'border-amber-200 bg-amber-50 text-amber-700' },
};

/**
 * What the account manager has to do to get a real forecast. Every
 * non-actionable status resolves to one of these.
 */
const REASON_PROMPT: Record<PacingReason, string> = {
  missing_budget: 'Add a budget estimate to this campaign to see pacing.',
  missing_end_date: 'Add an end date to this campaign to see pacing.',
  missing_budget_and_end_date:
    'Add a budget estimate and an end date to this campaign to see pacing.',
  invalid_period: 'This campaign ends before it starts — fix the dates to see pacing.',
  no_linked_spend:
    'No Meta spend is linked to this campaign yet, so there is nothing to pace against.',
};

const NOT_STARTED_PROMPT = 'Pacing starts once the campaign is under way.';

export function pacingPrompt(pacing: CampaignPacingForecast): string | null {
  if (pacing.status === 'not_started') return NOT_STARTED_PROMPT;
  if (pacing.reason && pacing.reason in REASON_PROMPT) {
    return REASON_PROMPT[pacing.reason as PacingReason];
  }
  return null;
}

function formatMoney(value: string | null): string {
  if (value === null) return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
}

function formatPaceRatio(value: string | null): string | null {
  if (value === null) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return `${Math.round(parsed * 100)}% of budget`;
}

export default function CampaignPacingBadge({ pacing, variant = 'compact', className }: Props) {
  // A campaign the nightly task has never covered has no forecast at all.
  if (!pacing) {
    return <span className={`text-xs text-gray-400 ${className ?? ''}`}>—</span>;
  }

  const config = STATUS_CONFIG[pacing.status] ?? STATUS_CONFIG.no_data;
  const prompt = pacingPrompt(pacing);
  const projected = formatPaceRatio(pacing.pace_ratio);

  const badge = (
    <span
      data-testid="pacing-badge"
      data-pacing-status={pacing.status}
      title={prompt ?? projected ?? undefined}
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium ${config.className}`}
    >
      {config.label}
    </span>
  );

  if (variant === 'compact') {
    return <span className={className}>{badge}</span>;
  }

  return (
    <div className={`space-y-2 ${className ?? ''}`}>
      <div className="flex items-center gap-2">
        {badge}
        {projected && !prompt ? (
          <span className="text-xs text-gray-500">{projected} projected</span>
        ) : null}
      </div>

      {prompt ? (
        <p data-testid="pacing-prompt" className="text-xs text-gray-500">
          {prompt}
        </p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="text-gray-500">Spent so far</dt>
          <dd className="text-right font-medium text-gray-900">
            {formatMoney(pacing.spend_to_date)}
          </dd>

          <dt className="text-gray-500">Expected by now</dt>
          <dd className="text-right text-gray-700">
            {formatMoney(pacing.expected_spend_to_date)}
          </dd>

          <dt className="text-gray-500">Projected total</dt>
          <dd className="text-right text-gray-700">
            {formatMoney(pacing.projected_total_spend)}
          </dd>

          <dt className="text-gray-500">Budget</dt>
          <dd className="text-right text-gray-700">{formatMoney(pacing.budget)}</dd>

          <dt className="text-gray-500">Suggested daily cap</dt>
          <dd
            data-testid="pacing-suggested-cap"
            className="text-right font-medium text-gray-900"
          >
            {formatMoney(pacing.suggested_daily_cap)}
          </dd>

          <dt className="text-gray-500">Days left</dt>
          <dd className="text-right text-gray-700">{pacing.days_remaining}</dd>
        </dl>
      )}
    </div>
  );
}
