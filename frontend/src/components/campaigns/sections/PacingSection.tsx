'use client';

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { OptimizationAPI } from '@/lib/api/optimizationApi';
import type { CampaignPacingForecast } from '@/types/campaign';
import CampaignPacingBadge from '../CampaignPacingBadge';

export interface PacingSectionProps {
  campaignSlug: string;
}

function formatComputedAt(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

export default function PacingSection({ campaignSlug }: PacingSectionProps) {
  const [pacing, setPacing] = useState<CampaignPacingForecast | null>(null);
  const [loading, setLoading] = useState(true);
  const [recomputing, setRecomputing] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!campaignSlug) return;
    setLoading(true);
    setErrorMessage(null);
    try {
      const response = await OptimizationAPI.getCampaignPacing(campaignSlug);
      setPacing(response.data);
    } catch (err) {
      const anyErr = err as any;
      setErrorMessage(
        anyErr?.response?.data?.detail || anyErr?.message || 'Failed to load pacing forecast'
      );
    } finally {
      setLoading(false);
    }
  }, [campaignSlug]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRecompute = useCallback(async () => {
    if (!campaignSlug) return;
    setRecomputing(true);
    setErrorMessage(null);
    try {
      const response = await OptimizationAPI.recomputeCampaignPacing(campaignSlug);
      setPacing(response.data);
    } catch (err) {
      const anyErr = err as any;
      setErrorMessage(
        anyErr?.response?.data?.detail || anyErr?.message || 'Failed to recompute pacing forecast'
      );
    } finally {
      setRecomputing(false);
    }
  }, [campaignSlug]);

  return (
    <section
      data-testid="pacing-section"
      className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Budget Pacing
        </h2>
        <button
          type="button"
          data-testid="pacing-recompute"
          onClick={handleRecompute}
          disabled={recomputing || loading}
          className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] font-medium text-gray-600 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw className={`h-3 w-3 ${recomputing ? 'animate-spin' : ''}`} />
          Recompute
        </button>
      </div>

      {loading && !pacing ? (
        <div className="flex items-center justify-center py-8">
          <div className="h-6 w-6 animate-spin rounded-full border-b-2 border-[#3CCED7]" />
          <span className="ml-3 text-sm text-gray-600">Loading pacing forecast...</span>
        </div>
      ) : errorMessage && !pacing ? (
        <div className="rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          {errorMessage}
        </div>
      ) : (
        <>
          {errorMessage ? (
            <div className="mb-3 rounded-md border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800">
              {errorMessage}
            </div>
          ) : null}

          <CampaignPacingBadge pacing={pacing} variant="detail" />

          {pacing ? (
            <p className="mt-3 text-[11px] text-gray-400">
              Updated {formatComputedAt(pacing.computed_at)}
              {pacing.seasonality_applied ? ' · day-of-week adjusted' : ' · linear'}
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
