'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertCircle, KeyRound, ListChecks, Pencil, Plus, Send, Trash2 } from 'lucide-react';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { WebhookEndpoint, WebhookEndpointData, WebhookEventType } from '@/types/csmIntegrations';
import { PORTAL_SUBMIT_BUTTON_CLASS } from '@/components/ticket-form/constants';
import StatusBadge from '@/components/csm-settings/StatusBadge';
import ConfirmModal from '@/components/ui/ConfirmModal';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import SecretRevealModal, { type RevealedSecret } from './SecretRevealModal';
import WebhookDeliveryLog from './WebhookDeliveryLog';
import WebhookEndpointDrawer from './WebhookEndpointDrawer';
import { DELIVERY_STATUS_CLASS, DELIVERY_STATUS_LABELS, eventLabel, formatDateTime } from './labels';

const ICON_BUTTON_CLASS =
  'rounded-md p-1.5 text-gray-400 transition-colors hover:bg-indigo-50 hover:text-indigo-600 disabled:opacity-40';

type Pending = { kind: 'delete' | 'rotate'; endpoint: WebhookEndpoint } | null;

interface Props {
  projectId: number;
  events: WebhookEventType[];
}

function secretReveal(endpoint: WebhookEndpoint, secret: string, rotated: boolean): RevealedSecret {
  return {
    title: rotated ? 'Signing secret rotated' : 'Webhook created',
    description:
      'Verify each request: X-Zmarkio-Signature is t=<timestamp>,v1=<HMAC-SHA256 of "<timestamp>.<raw body>" with this secret>.'
      + (rotated ? ' The previous secret stopped working immediately.' : ''),
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
  const [editing, setEditing] = useState<WebhookEndpoint | null>(null);
  const [revealed, setRevealed] = useState<RevealedSecret | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [pendingBusy, setPendingBusy] = useState(false);
  const [logEndpointId, setLogEndpointId] = useState<number | null>(null);

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

  const replace = (updated: WebhookEndpoint) =>
    setEndpoints((prev) => prev.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)));

  const handleSubmit = async (data: WebhookEndpointData) => {
    if (editing) {
      replace(await CsmIntegrationsAPI.updateWebhook(projectId, editing.id, data));
      toast.success('Webhook updated.');
    } else {
      const { secret, ...created } = await CsmIntegrationsAPI.createWebhook(projectId, data);
      setEndpoints((prev) => [created, ...prev]);
      setRevealed(secretReveal(created, secret, false));
    }
    setDrawerOpen(false);
    setEditing(null);
  };

  const handleTest = async (endpoint: WebhookEndpoint) => {
    try {
      await CsmIntegrationsAPI.sendTestEvent(projectId, endpoint.id);
      toast.success('Test event queued. Check the delivery log in a few seconds.');
      setLogEndpointId(endpoint.id);
    } catch {
      toast.error('Could not send a test event.');
    }
  };

  const handleConfirm = async () => {
    if (!pending) return;
    setPendingBusy(true);
    try {
      if (pending.kind === 'delete') {
        await CsmIntegrationsAPI.deleteWebhook(projectId, pending.endpoint.id);
        setEndpoints((prev) => prev.filter((row) => row.id !== pending.endpoint.id));
        if (logEndpointId === pending.endpoint.id) setLogEndpointId(null);
        toast.success('Webhook deleted.');
      } else {
        const { secret, ...updated } = await CsmIntegrationsAPI.rotateWebhookSecret(projectId, pending.endpoint.id);
        replace(updated);
        setRevealed(secretReveal(updated, secret, true));
      }
      setPending(null);
    } catch {
      toast.error(pending.kind === 'delete' ? 'Could not delete the webhook.' : 'Could not rotate the secret.');
    } finally {
      setPendingBusy(false);
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
            onClick={() => { setEditing(null); setDrawerOpen(true); }}
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
                  <th className="px-4 py-3">Last delivery</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.map((endpoint) => (
                  <tr key={endpoint.id} className={`border-t border-gray-100 ${endpoint.is_active ? '' : 'opacity-60'}`}>
                    <td className="max-w-sm px-4 py-3">
                      <div className="truncate font-mono text-xs text-gray-900" title={endpoint.url}>{endpoint.url}</div>
                      {endpoint.description && <div className="text-xs text-gray-500">{endpoint.description}</div>}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {endpoint.events.map((event) => (
                          <span key={event} className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">
                            {eventLabel(event)}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {endpoint.last_delivery_status ? (
                        <div className="flex flex-col gap-1">
                          <span className={`w-fit rounded-full px-2 py-0.5 text-xs font-medium ${DELIVERY_STATUS_CLASS[endpoint.last_delivery_status]}`}>
                            {DELIVERY_STATUS_LABELS[endpoint.last_delivery_status]}
                          </span>
                          <span className="text-xs text-gray-500">{formatDateTime(endpoint.last_delivery_at)}</span>
                        </div>
                      ) : (
                        <span className="text-gray-400">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3"><StatusBadge active={endpoint.is_active} /></td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        <button type="button" onClick={() => setLogEndpointId(endpoint.id)} title="View log"
                          aria-label={`View log of ${endpoint.url}`} className={ICON_BUTTON_CLASS}>
                          <ListChecks className="h-4 w-4" aria-hidden />
                        </button>
                        <button type="button" onClick={() => handleTest(endpoint)} disabled={!endpoint.is_active}
                          title="Send test event" aria-label={`Send test event to ${endpoint.url}`} className={ICON_BUTTON_CLASS}>
                          <Send className="h-4 w-4" aria-hidden />
                        </button>
                        <button type="button" onClick={() => { setEditing(endpoint); setDrawerOpen(true); }}
                          title="Edit" aria-label={`Edit ${endpoint.url}`} className={ICON_BUTTON_CLASS}>
                          <Pencil className="h-4 w-4" aria-hidden />
                        </button>
                        <button type="button" onClick={() => setPending({ kind: 'rotate', endpoint })}
                          title="Rotate signing secret" aria-label={`Rotate secret of ${endpoint.url}`} className={ICON_BUTTON_CLASS}>
                          <KeyRound className="h-4 w-4" aria-hidden />
                        </button>
                        <button type="button" onClick={() => setPending({ kind: 'delete', endpoint })}
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
                    <td colSpan={5} className="px-4 py-8 text-center text-sm italic text-gray-400">
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
        <WebhookDeliveryLog
          projectId={projectId}
          endpoints={endpoints}
          events={events}
          endpointId={logEndpointId}
          onEndpointChange={setLogEndpointId}
        />
      </div>

      <WebhookEndpointDrawer
        isOpen={drawerOpen}
        editing={editing}
        events={events}
        onClose={() => { setDrawerOpen(false); setEditing(null); }}
        onSubmit={handleSubmit}
      />
      <SecretRevealModal revealed={revealed} onClose={() => setRevealed(null)} />
      <ConfirmModal
        isOpen={pending !== null}
        onClose={() => setPending(null)}
        onConfirm={handleConfirm}
        title={pending?.kind === 'delete' ? 'Delete webhook?' : 'Rotate signing secret?'}
        message={
          pending?.kind === 'delete'
            ? `${pending.endpoint.url} stops receiving events, pending retries are dropped and its delivery log is deleted.`
            : 'The current secret stops working immediately. Update the receiving system with the new secret right away.'
        }
        confirmText={pending?.kind === 'delete' ? 'Delete' : 'Rotate'}
        type={pending?.kind === 'delete' ? 'danger' : 'warning'}
        loading={pendingBusy}
      />
    </div>
  );
}
