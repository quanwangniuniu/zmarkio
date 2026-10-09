'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertCircle, Plus, Trash2 } from 'lucide-react';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { VocabularyOption, WebhookEndpoint, WebhookEndpointData } from '@/types/csmIntegrations';
import { formatDateTime } from '@/components/csm/quality/formatDates';
import { PORTAL_SUBMIT_BUTTON_CLASS } from '@/components/ticket-form/constants';
import ConfirmModal from '@/components/ui/ConfirmModal';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import SecretRevealModal, { type RevealedSecret } from './SecretRevealModal';
import WebhookDeliveryLog from './WebhookDeliveryLog';
import WebhookEndpointDrawer from './WebhookEndpointDrawer';

interface Props {
  projectId: number;
  events: VocabularyOption[];
}

function secretReveal(endpoint: WebhookEndpoint, secret: string): RevealedSecret {
  return {
    title: 'Webhook created',
    description:
      'Verify each request: X-Zmarkio-Signature is t=<timestamp>,v1=<HMAC-SHA256 of "<timestamp>.<raw body>" with this secret>.',
    values: [
      { label: 'Endpoint', value: endpoint.url },
      { label: 'Signing secret', value: secret, secret: true },
    ],
  };
}

export default function WebhooksPanel({ projectId, events }: Props) {
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [revealed, setRevealed] = useState<RevealedSecret | null>(null);
  const [deleting, setDeleting] = useState<WebhookEndpoint | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setEndpoints(await CsmIntegrationsAPI.listWebhooks(projectId));
    } catch {
      setError('Failed to load webhooks.');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const handleSubmit = async (data: WebhookEndpointData) => {
    const { secret, ...created } = await CsmIntegrationsAPI.createWebhook(projectId, data);
    setEndpoints((prev) => [created, ...prev]);
    setRevealed(secretReveal(created, secret));
    setDrawerOpen(false);
  };

  const handleDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await CsmIntegrationsAPI.deleteWebhook(projectId, deleting.id);
      setEndpoints((prev) => prev.filter((row) => row.id !== deleting.id));
      toast.success('Webhook deleted.');
      setDeleting(null);
    } catch {
      toast.error('Could not delete the webhook.');
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-4" aria-label="Webhook endpoints">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-gray-500">
            We POST a signed JSON event to each endpoint and retry failures 3 times (after 30 s, 2 min and 8 min).
          </p>
          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className={`gap-2 ${PORTAL_SUBMIT_BUTTON_CLASS}`}
          >
            <Plus className="h-4 w-4" aria-hidden />
            New webhook
          </button>
        </div>

        {error && (
          <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
            {error}
            <button
              type="button"
              onClick={load}
              className="ml-auto rounded-lg border border-red-300 px-3 py-1.5 text-sm text-red-700 hover:bg-red-100"
            >
              Retry
            </button>
          </div>
        )}

        {loading ? (
          <div className="flex min-h-[200px] items-center justify-center"><LoadingSpinner /></div>
        ) : (
          <div className="overflow-x-auto overflow-hidden rounded-xl border border-gray-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-3">Endpoint</th>
                  <th className="px-4 py-3">Events</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.map((endpoint) => (
                  <tr key={endpoint.id} className="border-t border-gray-100">
                    <td className="max-w-sm truncate px-4 py-3 font-mono text-xs text-gray-900" title={endpoint.url}>
                      {endpoint.url}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {endpoint.events.map((event) => (
                          <span key={event} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                            {events.find((option) => option.value === event)?.label ?? event}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-700">{formatDateTime(endpoint.created_at)}</td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button type="button" onClick={() => setDeleting(endpoint)}
                          title="Delete" aria-label={`Delete ${endpoint.url}`}
                          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600">
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {endpoints.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-sm italic text-gray-400">
                      No webhooks yet. Add an endpoint to receive ticket and SLA events.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div>
        <h2 className="mb-3 text-lg font-semibold text-gray-900">Delivery log</h2>
        <WebhookDeliveryLog projectId={projectId} events={events} />
      </div>

      <WebhookEndpointDrawer
        isOpen={drawerOpen}
        events={events}
        onClose={() => setDrawerOpen(false)}
        onSubmit={handleSubmit}
      />
      <SecretRevealModal revealed={revealed} onClose={() => setRevealed(null)} />
      <ConfirmModal
        isOpen={deleting !== null}
        onClose={() => setDeleting(null)}
        onConfirm={handleDelete}
        title="Delete webhook?"
        message={`${deleting?.url ?? ''} stops receiving events, pending retries are dropped and its delivery log is deleted.`}
        confirmText="Delete"
        type="danger"
        loading={deleteBusy}
      />
    </div>
  );
}
