'use client';

import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/button';
import { facebookApi } from '@/lib/api/facebookApi';
import type { CampaignPlatformIntegration } from '@/types/campaign';

export default function CampaignSyncBanner({ projectId, integrations = [] }: {
  projectId: number;
  integrations?: CampaignPlatformIntegration[];
}) {
  const [connecting, setConnecting] = useState(false);
  const failed = integrations.filter((integration) => integration.last_sync_error === 'auth');
  if (!failed.length) return null;

  const reconnect = async () => {
    setConnecting(true);
    try {
      const { authorize_url } = await facebookApi.connect(projectId);
      window.location.assign(authorize_url);
    } catch {
      toast.error('Unable to start reconnection. Please try again.');
      setConnecting(false);
    }
  };

  return (
    <div className="mb-5 space-y-3" aria-label="Campaign sync warnings">
      {failed.map((integration) => (
        <div key={integration.id} role="alert" className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-4 text-amber-950">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1 text-sm">
            <p className="font-semibold">Reconnect Meta to resume syncing — {integration.account_name || 'Ad account'}</p>
            <p className="mt-1">Authorization has expired or been revoked. Campaign metrics may be out of date.</p>
            {integration.last_synced_at && (
              <p className="mt-1 text-xs">Last successful sync: {new Date(integration.last_synced_at).toLocaleString()}</p>
            )}
            {!integration.can_reconnect && <p className="mt-1">Ask {integration.connector_name} to reconnect this account.</p>}
          </div>
          {integration.can_reconnect && (
            <Button variant="outline" size="sm" disabled={connecting} onClick={reconnect}>
              {connecting ? 'Connecting…' : 'Reconnect'}
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}
