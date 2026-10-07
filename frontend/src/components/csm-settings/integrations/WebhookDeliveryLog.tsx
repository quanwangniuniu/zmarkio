'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertCircle, ChevronDown, ChevronRight, RefreshCw, RotateCcw } from 'lucide-react';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type {
  DeliveryFilters,
  WebhookDelivery,
  WebhookDeliveryStatus,
  WebhookEndpoint,
  WebhookEventType,
} from '@/types/csmIntegrations';
import { BUILDER_CONTROL_CLASS, SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { DELIVERY_STATUS_CLASS, DELIVERY_STATUS_LABELS, eventLabel, formatDateTime } from './labels';

const PAGE_SIZE = 20;

interface Props {
  projectId: number;
  endpoints: WebhookEndpoint[];
  events: WebhookEventType[];
  /** Pre-selects one endpoint, e.g. from its "View log" action. */
  endpointId: number | null;
  onEndpointChange: (id: number | null) => void;
}

/** One row per delivery attempt: retries of an event share its event id. */
export default function WebhookDeliveryLog({ projectId, endpoints, events, endpointId, onEndpointChange }: Props) {
  const [eventType, setEventType] = useState('');
  const [status, setStatus] = useState<WebhookDeliveryStatus | ''>('');
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<WebhookDelivery[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const filters: DeliveryFilters = { page };
    if (endpointId) filters.endpoint = endpointId;
    if (eventType) filters.event_type = eventType;
    if (status) filters.status = status;
    try {
      const data = await CsmIntegrationsAPI.listDeliveries(projectId, filters);
      setRows(data.results);
      setCount(data.count);
    } catch {
      setError('Failed to load the delivery log.');
    } finally {
      setLoading(false);
    }
  }, [projectId, endpointId, eventType, status, page]);

  useEffect(() => { load(); }, [load]);

  // A different endpoint (also chosen from the webhooks table) starts from the first page.
  useEffect(() => { setPage(1); }, [endpointId]);

  const handleRedeliver = async (row: WebhookDelivery) => {
    try {
      await CsmIntegrationsAPI.redeliver(projectId, row.id);
      toast.success('Redelivery queued. It appears here as a new attempt.');
    } catch {
      toast.error('Could not queue the redelivery.');
    }
  };

  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));

  return (
    <section className="flex flex-col gap-3" aria-label="Delivery log">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          Endpoint
          <select
            aria-label="Filter by endpoint"
            value={endpointId ?? ''}
            onChange={(e) => onEndpointChange(e.target.value ? Number(e.target.value) : null)}
            className={BUILDER_CONTROL_CLASS}
          >
            <option value="">All endpoints</option>
            {endpoints.map((endpoint) => (
              <option key={endpoint.id} value={endpoint.id}>{endpoint.url}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          Event
          <select
            aria-label="Filter by event"
            value={eventType}
            onChange={(e) => { setEventType(e.target.value); setPage(1); }}
            className={BUILDER_CONTROL_CLASS}
          >
            <option value="">All events</option>
            {[...events, 'ping'].map((event) => (
              <option key={event} value={event}>{eventLabel(event)}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-500">
          Status
          <select
            aria-label="Filter by status"
            value={status}
            onChange={(e) => { setStatus(e.target.value as WebhookDeliveryStatus | ''); setPage(1); }}
            className={BUILDER_CONTROL_CLASS}
          >
            <option value="">All statuses</option>
            {(Object.keys(DELIVERY_STATUS_LABELS) as WebhookDeliveryStatus[]).map((value) => (
              <option key={value} value={value}>{DELIVERY_STATUS_LABELS[value]}</option>
            ))}
          </select>
        </label>
        <button type="button" onClick={load} className={`ml-auto ${SECONDARY_BUTTON_CLASS}`}>
          <RefreshCw className="h-4 w-4" aria-hidden />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex min-h-[200px] items-center justify-center"><LoadingSpinner /></div>
      ) : (
        <div className="overflow-x-auto overflow-hidden rounded-xl border border-gray-200 bg-white">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
              <tr>
                <th className="w-8 px-2 py-3" aria-label="Details" />
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Event</th>
                <th className="px-4 py-3">Target URL</th>
                <th className="px-4 py-3">Attempt</th>
                <th className="px-4 py-3">HTTP</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <Fragment key={row.id}>
                  <tr className="border-t border-gray-100">
                    <td className="px-2 py-3">
                      <button
                        type="button"
                        onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                        aria-expanded={expanded === row.id}
                        aria-label={`Details of delivery ${row.id}`}
                        className="rounded p-1 text-gray-400 hover:bg-gray-100"
                      >
                        {expanded === row.id
                          ? <ChevronDown className="h-4 w-4" aria-hidden />
                          : <ChevronRight className="h-4 w-4" aria-hidden />}
                      </button>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-gray-700">{formatDateTime(row.created_at)}</td>
                    <td className="px-4 py-3 text-gray-900">{eventLabel(row.event_type)}</td>
                    <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-gray-600" title={row.target_url}>
                      {row.target_url}
                    </td>
                    <td className="px-4 py-3 text-gray-700">{row.attempt} / 4</td>
                    <td className="px-4 py-3 font-mono text-gray-700">{row.response_code ?? '—'}</td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-3 py-1 text-xs font-medium ${DELIVERY_STATUS_CLASS[row.status]}`}>
                        {DELIVERY_STATUS_LABELS[row.status]}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {row.status === 'failed' && (
                        <button
                          type="button"
                          onClick={() => handleRedeliver(row)}
                          title="Redeliver"
                          aria-label={`Redeliver delivery ${row.id}`}
                          className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-indigo-50 hover:text-indigo-600"
                        >
                          <RotateCcw className="h-4 w-4" aria-hidden />
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded === row.id && (
                    <tr className="bg-gray-50">
                      <td colSpan={8} className="px-6 py-4 text-xs text-gray-700">
                        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
                          <dt className="text-gray-500">Event ID</dt>
                          <dd className="font-mono">{row.event_id}</dd>
                          {row.error && (<><dt className="text-gray-500">Error</dt><dd>{row.error}</dd></>)}
                          {row.duration_ms !== null && (<><dt className="text-gray-500">Duration</dt><dd>{row.duration_ms} ms</dd></>)}
                          {row.next_retry_at && (
                            <><dt className="text-gray-500">Next retry</dt><dd>{formatDateTime(row.next_retry_at)}</dd></>
                          )}
                        </dl>
                        <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-gray-200 bg-white p-3 font-mono">
                          {JSON.stringify(row.payload, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-sm italic text-gray-400">
                    No deliveries yet. Use &quot;Send test&quot; on a webhook to try one.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {count > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-3 text-sm text-gray-600">
          <span>Page {page} of {pages}</span>
          <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className={SECONDARY_BUTTON_CLASS}>
            Previous
          </button>
          <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)} className={SECONDARY_BUTTON_CLASS}>
            Next
          </button>
        </div>
      )}
    </section>
  );
}
