'use client';

import { Check, Copy, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" data-testid="share-kpi-dialog">
        <DialogHeader>
          <DialogTitle>Share Custom KPIs</DialogTitle>
          <DialogDescription>
            Anyone with the link can view these metrics. They cannot edit or delete them.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading link…
          </div>
        ) : live && link ? (
          <div className="space-y-3">
            <p className="text-sm text-gray-600" data-testid="share-kpi-days-left">
              {link.days_left > 0
                ? `Expires in ${link.days_left} day${link.days_left === 1 ? '' : 's'}.`
                : 'Expires today.'}
            </p>
            <div className="flex items-center gap-2">
              <input
                readOnly
                aria-label="Share link"
                value={shareUrl(link.token)}
                className="min-w-0 flex-1 rounded-md border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs text-gray-700"
              />
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => void handleCopy()}
                disabled={copied}
                aria-label="Copy link"
              >
                {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              </Button>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void handleRevoke()}
              disabled={working}
              data-testid="revoke-share-link"
            >
              {working ? 'Revoking…' : 'Revoke link'}
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-gray-500">
                Link expires in
              </div>
              <div className="flex gap-2" role="radiogroup" aria-label="Link duration">
                {DAY_OPTIONS.map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={days === option}
                    onClick={() => setDays(option)}
                    disabled={working}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                      days === option
                        ? 'bg-[#3CCED7] text-white'
                        : 'bg-white text-gray-700 ring-1 ring-gray-200'
                    }`}
                  >
                    {option} days
                  </button>
                ))}
              </div>
            </div>
            <Button
              type="button"
              size="sm"
              onClick={() => void handleCreate()}
              disabled={working}
              data-testid="create-share-link"
            >
              {working ? 'Creating…' : 'Create link'}
            </Button>
          </div>
        )}

        {error && (
          <p role="alert" className="text-xs text-red-600">
            {error}
          </p>
        )}

        <DialogFooter>
          <Button type="button" variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
