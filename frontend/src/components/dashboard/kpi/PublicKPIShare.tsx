'use client';

import { AlertCircle, Clock, Link2Off, Loader2 } from 'lucide-react';
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

function ReadOnlyTile({ kpi }: { kpi: PublicCustomKPI }) {
  return (
    <div
      className="flex min-h-[148px] flex-col rounded-xl border border-gray-200/80 bg-[#f7fafa] p-4"
      data-testid="public-kpi-tile"
      data-kpi-name={kpi.name}
    >
      <div className="truncate text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-400">
        {kpi.name}
      </div>
      {isNoData(kpi.error) ? (
        <div className="mt-3 text-sm text-gray-400" data-testid="public-kpi-tile-no-data">
          {kpi.error?.message}
        </div>
      ) : kpi.error ? (
        <div
          className="mt-3 flex items-start gap-1.5 text-sm text-red-600"
          data-testid="public-kpi-tile-error"
        >
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{kpi.error.message}</span>
        </div>
      ) : (
        <div
          className="mt-2 text-3xl font-semibold tracking-tight text-gray-900 tabular-nums"
          data-testid="public-kpi-tile-value"
        >
          {formatKPIValue(kpi.value, kpi.display_format)}
        </div>
      )}
      <div className="mt-auto pt-4">
        <div className="inline-flex max-w-full truncate rounded-md bg-white px-2 py-1 font-mono text-[11px] text-gray-500 ring-1 ring-gray-200">
          {kpi.formula}
        </div>
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
    <div className="flex items-start gap-3 rounded-xl border border-gray-200/80 bg-[#f7fafa] px-4 py-4">
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
    <div className="min-h-screen bg-[#f3f6f6]" data-testid="public-kpi-share">
      <header className="border-b border-gray-200/80 bg-white">
        <div className="mx-auto flex h-16 max-w-5xl items-center px-6">
          <Link href="/" className="flex items-center" aria-label="Marketing Simplified home">
            <Image
              src="/marketing_simplified_logo.png"
              alt="Marketing Simplified"
              width={220}
              height={104}
              className="h-12 w-auto"
            />
          </Link>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
        <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          <div className="h-1 bg-gradient-to-r from-[#3CCED7] to-[#A6E661]" />
          <div className="px-5 py-6 sm:px-8 sm:py-8">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full bg-[#3CCED7]/15 px-2.5 py-0.5 text-xs font-medium text-[#0E8A96]">
                View only
              </span>
              {state.status === 'ready' && (
                <span className="text-xs text-gray-400" data-testid="public-kpi-expires">
                  Expires {formatExpiry(state.share.expires_at)}
                </span>
              )}
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight text-gray-900">
              Custom KPIs
            </h1>
            <p className="mt-1 max-w-xl text-sm text-gray-500">
              These numbers use the last 30 days of data. This page cannot create, edit, or delete them.
            </p>

            <div className="mt-8">
              {state.status === 'loading' && (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {[0, 1, 2].map((item) => (
                    <div
                      key={item}
                      className="flex h-[148px] items-center justify-center rounded-xl bg-[#f7fafa]"
                    >
                      {item === 0 && (
                        <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
                      )}
                    </div>
                  ))}
                </div>
              )}

              {state.status === 'expired' && (
                <StatusNote
                  testId="public-kpi-expired"
                  icon={<Clock className="h-4 w-4" />}
                  title="This link has expired."
                  detail="Ask the person who shared it for a new link."
                />
              )}

              {state.status === 'missing' && (
                <StatusNote
                  testId="public-kpi-missing"
                  icon={<Link2Off className="h-4 w-4" />}
                  title="This link is not available."
                  detail="Check the address, or ask the person who shared it."
                />
              )}

              {state.status === 'error' && (
                <p className="text-sm text-red-600" role="alert" data-testid="public-kpi-error">
                  Could not load these KPIs.
                </p>
              )}

              {state.status === 'ready' && state.share.kpis.length === 0 && (
                <p className="text-sm text-gray-500" data-testid="public-kpi-empty">
                  No custom KPIs yet.
                </p>
              )}

              {state.status === 'ready' && state.share.kpis.length > 0 && (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {state.share.kpis.map((kpi) => (
                    <ReadOnlyTile key={kpi.name} kpi={kpi} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
