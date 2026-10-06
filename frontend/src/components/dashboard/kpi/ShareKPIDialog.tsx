'use client';

import { Check, Copy, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import ReportAPI from '@/lib/api/reportApi';
import type { ReportShareLink, ShareLinkDays } from '@/types/report';

const DAY_OPTIONS: ShareLinkDays[] = [7, 14, 30];

interface ShareKPIDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectSlug: string;
}

type CurrentLink = ReportShareLink & { days_left: number };

function shareUrl(token: string): string {
  const path = `/share/kpis/${token}`;
  if (typeof window === 'undefined') return path;
  return `${window.location.origin}${path}`;
}

function isUnexpired(expiresAt: string): boolean {
  return new Date(expiresAt).getTime() > Date.now();
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

export default function ShareKPIDialog({
  open,
  onOpenChange,
  projectSlug,
}: ShareKPIDialogProps) {
  const [days, setDays] = useState<ShareLinkDays>(7);
  const [link, setLink] = useState<CurrentLink | null>(null);
  const [loading, setLoading] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const loadCurrent = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await ReportAPI.getShareLink(projectSlug);
      const current = response.data.link;
      setLink(current && isUnexpired(current.expires_at) ? current : null);
    } catch {
      setError('Could not load the share link.');
    } finally {
      setLoading(false);
    }
  }, [projectSlug]);

  useEffect(() => {
    if (!open) return;
    setDays(7);
    setCopied(false);
    void loadCurrent();
  }, [open, loadCurrent]);

  const handleCreate = useCallback(async () => {
    setWorking(true);
    setError(null);
    try {
      const response = await ReportAPI.createShareLink({
        project: projectSlug,
        days,
      });
      const created = response.data;
      setLink({
        id: created.id,
        token: created.token,
        expires_at: created.expires_at,
        project: created.project,
        days_left: days,
      });
    } catch {
      setError('Could not create the share link.');
    } finally {
      setWorking(false);
    }
  }, [projectSlug, days]);

  const handleRevoke = useCallback(async () => {
    setWorking(true);
    setError(null);
    try {
      await ReportAPI.revokeShareLink(projectSlug);
      setLink(null);
      toast.success('Share link revoked');
    } catch {
      setError('Could not revoke the share link.');
    } finally {
      setWorking(false);
    }
  }, [projectSlug]);

  const handleCopy = useCallback(async () => {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(shareUrl(link.token));
      setCopied(true);
      toast.success('Link copied');
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Copy failed');
    }
  }, [link]);

  const live = link != null && isUnexpired(link.expires_at);
  const expiryLabel =
    link && link.days_left > 0
      ? `Expires in ${link.days_left} day${link.days_left === 1 ? '' : 's'}.`
      : 'Expires today.';

  return (
    <BrandDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Share Custom KPIs"
      subtitle="Generate a public read-only link"
      width="max-w-md"
    >
      <div className="space-y-4" data-testid="share-kpi-dialog">
        <div>
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-500">
            Link duration
          </div>
          <div className="flex items-center gap-2" role="radiogroup" aria-label="Link duration">
            {DAY_OPTIONS.map((option) => {
              const selected = !live && days === option;
              return (
                <button
                  key={option}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  onClick={() => setDays(option)}
                  disabled={live || loading || working}
                  className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    selected
                      ? 'bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-white shadow-sm'
                      : 'bg-white text-gray-700 ring-1 ring-gray-200 hover:ring-gray-300'
                  }`}
                >
                  {selected && <Check className="h-3 w-3 shrink-0" aria-hidden="true" />}
                  {option} days
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-500">
            Shareable link
          </div>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              aria-label="Share link"
              value={live && link ? shareUrl(link.token) : ''}
              placeholder={
                loading ? 'Loading link…' : working && !live ? 'Generating link…' : 'Not generated yet'
              }
              aria-busy={loading || working}
              className="min-w-0 flex-1 rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs text-gray-700 outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
            />
            <button
              type="button"
              onClick={() => void handleCopy()}
              disabled={!live || copied}
              aria-label="Copy link"
              title="Copy link"
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white text-gray-600 ring-1 ring-gray-200 transition hover:ring-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
          </div>
          {live && link ? (
            <p className="mt-2 text-xs text-gray-500" data-testid="share-kpi-days-left">
              {expiryLabel} {formatExpiry(link.expires_at)}
            </p>
          ) : (
            <p className="mt-2 text-xs text-gray-500">
              Link expires {days} days after generation.
            </p>
          )}
          {error && (
            <p role="alert" className="mt-2 text-xs text-rose-600">
              {error}
            </p>
          )}
        </div>
      </div>

      <div className="-mx-5 -mb-5 mt-5 flex items-center justify-between gap-2 border-t border-gray-100 bg-gray-50 px-5 py-3">
        {live ? (
          <button
            type="button"
            onClick={() => void handleRevoke()}
            disabled={working}
            data-testid="revoke-share-link"
            className="rounded-md bg-white px-3 py-1.5 text-xs font-medium text-red-600 ring-1 ring-red-200 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {working ? 'Revoking…' : 'Revoke link'}
          </button>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="rounded-md bg-white px-3 py-1.5 text-xs font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300"
          >
            Close
          </button>
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={loading || working || live}
            data-testid="create-share-link"
            className="inline-flex items-center gap-1.5 rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-1.5 text-xs font-medium text-white shadow-sm transition hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {working && !live && (
              <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden="true" />
            )}
            Generate link
          </button>
        </div>
      </div>
    </BrandDialog>
  );
}
