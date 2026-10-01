'use client';

import { Clock, Link2Off, Loader2 } from 'lucide-react';
import Image from 'next/image';
import Link from 'next/link';
import { useEffect, useState, type ReactNode } from 'react';
import { getPublicKPIShare } from '@/lib/api/reportPublicApi';
import type { PublicCustomKPI, PublicKPIShare } from '@/types/report';
import { formatKPIValue } from './formatKPIValue';
import { isNoData } from './kpiErrors';

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; share: PublicKPIShare }
  | { status: 'expired' }
  | { status: 'missing' }
  | { status: 'error' };

function responseStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null || !('response' in error)) {
    return undefined;
  }
  return (error as { response?: { status?: number } }).response?.status;
}

function formatExpiry(expiresAt: string): string {
  return new Date(expiresAt).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function MetricValue({ kpi }: { kpi: PublicCustomKPI }) {
  if (isNoData(kpi.error)) {
    return (
      <span className="text-sm font-medium text-gray-400" data-testid="public-kpi-tile-no-data">
        No data
      </span>
    );
  }
  if (kpi.error) {
    return (
      <span
        className="max-w-[10rem] text-right text-sm font-medium leading-snug text-red-600 sm:max-w-[12rem]"
        data-testid="public-kpi-tile-error"
      >
        {kpi.error.message}
      </span>
    );
  }
  return (
    <span
      className="text-[26px] font-semibold leading-none tracking-tight text-gray-900 tabular-nums sm:text-[30px]"
      data-testid="public-kpi-tile-value"
    >
      {formatKPIValue(kpi.value, kpi.display_format)}
    </span>
  );
}

function MetricRow({ kpi }: { kpi: PublicCustomKPI }) {
  return (
    <div
      className="group flex items-center justify-between gap-5 py-6 transition-colors"
      data-testid="public-kpi-tile"
      data-kpi-name={kpi.name}
    >
      <div className="min-w-0">
        <div className="truncate text-[15px] font-medium text-gray-800">{kpi.name}</div>
        <div className="mt-1.5 truncate font-mono text-[11px] tracking-wide text-gray-400">
          {kpi.formula}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <MetricValue kpi={kpi} />
      </div>
    </div>
  );
}

function StatusNote({
  testId,
  icon,
  title,
  detail,
}: {
  testId: string;
  icon: ReactNode;
  title: string;
  detail: string;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl bg-[#f4fbfb] px-4 py-5">
      <div className="mt-0.5 text-[#0E8A96]">{icon}</div>
      <div>
        <p className="text-sm font-medium text-gray-900" data-testid={testId}>
          {title}
        </p>
        <p className="mt-1 text-sm text-gray-500">{detail}</p>
      </div>
    </div>
  );
}

export default function PublicKPIShareView({ token }: { token: string }) {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    getPublicKPIShare(token)
      .then((response) => {
        if (!cancelled) setState({ status: 'ready', share: response.data });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const status = responseStatus(error);
        if (status === 410) setState({ status: 'expired' });
        else if (status === 404) setState({ status: 'missing' });
        else setState({ status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <div
      className="min-h-screen bg-[linear-gradient(180deg,#f3fafa_0%,#f7f8f8_42%,#f5f5f5_100%)]"
      data-testid="public-kpi-share"
    >
      <div className="mx-auto max-w-[720px] px-4 py-8 sm:px-6 sm:py-12">
        <div className="overflow-hidden rounded-2xl border border-gray-200/70 bg-white shadow-[0_18px_50px_-28px_rgba(14,138,150,0.35)]">
          <div className="h-[3px] w-full bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />

          <div className="px-6 pb-10 pt-6 sm:px-9 sm:pb-12 sm:pt-7">
            <header className="flex items-center justify-between gap-4">
              <Link href="/" className="flex items-center" aria-label="Marketing Simplified home">
                <Image
                  src="/marketing_simplified_logo.png"
                  alt="Marketing Simplified"
                  width={220}
                  height={104}
                  className="h-9 w-auto sm:h-11"
                />
              </Link>
              <div className="text-right">
                <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[#0E8A96]">
                  View only
                </p>
                {state.status === 'ready' && (
                  <p className="mt-1 text-xs text-gray-400" data-testid="public-kpi-expires">
                    Expires {formatExpiry(state.share.expires_at)}
                  </p>
                )}
              </div>
            </header>

            <div className="mt-9 flex items-end justify-between gap-4">
              <div className="min-w-0">
                <h1 className="text-[28px] font-semibold tracking-tight text-gray-900 sm:text-[32px]">
                  Custom KPIs
                </h1>
                <p className="mt-1.5 text-sm text-gray-500">
                  These numbers use the last 30 days of data.
                </p>
              </div>
              {state.status === 'ready' && (
                <span className="shrink-0 rounded-full bg-[#3CCED7]/12 px-2.5 py-1 text-xs font-medium text-[#0E8A96]">
                  {state.share.kpis.length} metric
                  {state.share.kpis.length === 1 ? '' : 's'}
                </span>
              )}
            </div>

            <div className="mt-8 h-px bg-gradient-to-r from-[#3CCED7]/50 via-gray-200 to-transparent" />

            {state.status === 'loading' && (
              <div className="mt-8 flex items-center gap-2 py-12 text-sm text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin text-[#3CCED7]" />
                Loading KPIs…
              </div>
            )}

            {state.status === 'expired' && (
              <div className="mt-8">
                <StatusNote
                  testId="public-kpi-expired"
                  icon={<Clock className="h-4 w-4" />}
                  title="This link has expired."
                  detail="Ask the person who shared it for a new link."
                />
              </div>
            )}

            {state.status === 'missing' && (
              <div className="mt-8">
                <StatusNote
                  testId="public-kpi-missing"
                  icon={<Link2Off className="h-4 w-4" />}
                  title="This link is not available."
                  detail="Check the address, or ask the person who shared it."
                />
              </div>
            )}

            {state.status === 'error' && (
              <p className="mt-8 text-sm text-red-600" role="alert" data-testid="public-kpi-error">
                Could not load these KPIs.
              </p>
            )}

            {state.status === 'ready' && state.share.kpis.length === 0 && (
              <p className="mt-8 text-sm text-gray-500" data-testid="public-kpi-empty">
                No custom KPIs yet.
              </p>
            )}

            {state.status === 'ready' && state.share.kpis.length > 0 && (
              <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 sm:gap-x-14">
                {state.share.kpis.map((kpi) => (
                  <div key={kpi.name} className="border-b border-gray-100">
                    <MetricRow kpi={kpi} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
