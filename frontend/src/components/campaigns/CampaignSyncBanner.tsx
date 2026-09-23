'use client';

import { useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { CampaignAPI } from '@/lib/api/campaignApi';
import type { CampaignPlatformIntegration } from '@/types/campaign';

function IntegrationWarning({ campaignSlug, integration }: {
  campaignSlug: string;
  integration: CampaignPlatformIntegration;
}) {
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsReconnect = integration.last_sync_error === 'auth';

  const reconnect = async () => {
    setConnecting(true);
    setError(null);
    try {
      const { data } = await CampaignAPI.reconnectPlatform(campaignSlug, integration.id);
      window.location.assign(data.authorize_url);
    } catch {
      setError('Unable to start reconnection. Please try again.');
      setConnecting(false);
    }
  };

  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-4 text-amber-950">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-semibold">
          {needsReconnect ? 'Reconnect Meta to resume syncing' : 'Meta metrics could not be synced'}
          {integration.account_name && ` — ${integration.account_name}`}
        </p>
        <p className="mt-1">
          {needsReconnect
            ? 'Authorization has expired or been revoked. Campaign metrics may be out of date.'
            : integration.last_sync_error === 'transient'
              ? 'A temporary problem interrupted the sync. Metrics may be out of date until the next successful retry.'
              : 'The latest sync failed. Campaign metrics may be out of date.'}
        </p>
        {needsReconnect && !integration.can_reconnect && (
          <p className="mt-1">Ask {integration.connector_name} to reconnect this account.</p>
        )}
        <p className="mt-1 text-xs">
          {integration.last_synced_at
            ? `Last successful sync: ${new Date(integration.last_synced_at).toLocaleString()}`
            : 'No successful sync yet.'}
        </p>
        {error && <p className="mt-2 font-medium">{error}</p>}
      </div>
      {needsReconnect && integration.can_reconnect && (
        <Button variant="outline" size="sm" disabled={connecting} onClick={reconnect}>
          {connecting ? 'Connecting…' : 'Reconnect'}
        </Button>
      )}
    </div>
  );
}

export default function CampaignSyncBanner({ campaignSlug, integrations = [] }: {
  campaignSlug: string;
  integrations?: CampaignPlatformIntegration[];
}) {
  const failed = integrations.filter((integration) => integration.last_sync_error);
  if (!failed.length) return null;

  return (
    <div className="mb-5 space-y-3" aria-label="Campaign sync warnings">
      {failed.map((integration) => (
        <IntegrationWarning key={integration.id} campaignSlug={campaignSlug} integration={integration} />
      ))}
    </div>
  );
}
