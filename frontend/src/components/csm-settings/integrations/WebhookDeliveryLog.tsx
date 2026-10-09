'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { VocabularyOption, WebhookDelivery, WebhookDeliveryStatus } from '@/types/csmIntegrations';
import { SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';
import { formatDateTime } from '@/components/csm/quality/formatDates';
import LoadingSpinner from '@/components/ui/LoadingSpinner';

const STATUS_LABEL: Record<WebhookDeliveryStatus, string> = {
  succeeded: 'Succeeded',
  retrying: 'Retry scheduled',
  failed: 'Failed',
};

const STATUS_CLASS: Record<WebhookDeliveryStatus, string> = {
  succeeded: 'bg-green-100 text-green-800',
  retrying: 'bg-amber-100 text-amber-800',
  failed: 'bg-red-100 text-red-700',
};

interface Props {
  projectId: number;
  events: VocabularyOption[];
}

/** The newest 20 delivery attempts. Retries of an event appear as further attempts. */
export default function WebhookDeliveryLog({ projectId, events }: Props) {
  const [rows, setRows] = useState<WebhookDelivery[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(await CsmIntegrationsAPI.listDeliveries(projectId));
    } catch {
      setError('Failed to load the delivery log.');
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const eventLabel = (value: string) => events.find((event) => event.value === value)?.label ?? value;

  return (
    <section className="flex flex-col gap-3" aria-label="Delivery log">
      <div className="flex justify-end">
        <button type="button" onClick={load} className={SECONDARY_BUTTON_CLASS}>
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
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Event</th>
                <th className="px-4 py-3">Target URL</th>
                <th className="px-4 py-3">Attempt</th>
                <th className="px-4 py-3">HTTP</th>
                <th className="px-4 py-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="whitespace-nowrap px-4 py-3 text-gray-700">{formatDateTime(row.created_at)}</td>
                  <td className="px-4 py-3 text-gray-900">{eventLabel(row.event_type)}</td>
                  <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-gray-600" title={row.target_url}>
                    {row.target_url}
                  </td>
                  <td className="px-4 py-3 text-gray-700">{row.attempt} / 4</td>
                  <td className="px-4 py-3 font-mono text-gray-700" title={row.error || undefined}>
                    {row.response_code ?? '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_CLASS[row.status]}`}>
                      {STATUS_LABEL[row.status]}
                    </span>
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-sm italic text-gray-400">
                    No deliveries yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
