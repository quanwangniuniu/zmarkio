'use client';

import { useCallback, useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { AlertCircle, Ban, Plus } from 'lucide-react';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type {
  ApiKey, CreateCredentialData, CredentialKind, OAuthClient, VocabularyOption,
} from '@/types/csmIntegrations';
import { PORTAL_SUBMIT_BUTTON_CLASS } from '@/components/ticket-form/constants';
import StatusBadge from '@/components/csm-settings/StatusBadge';
import { formatDateTime } from '@/components/csm/quality/formatDates';
import ConfirmModal from '@/components/ui/ConfirmModal';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import CredentialFormModal from './CredentialFormModal';
import SecretRevealModal, { type RevealedSecret } from './SecretRevealModal';

type Credential = ApiKey | OAuthClient;

/** "Tickets (write), Queues (read)": a resource with both scopes shows as write. */
function summarizeScopes(scopes: string[], resources: VocabularyOption[]): string {
  return resources
    .map(({ value, label }) => {
      if (scopes.includes(`${value}:write`)) return `${label} (write)`;
      if (scopes.includes(`${value}:read`)) return `${label} (read)`;
      return null;
    })
    .filter(Boolean)
    .join(', ');
}

const COPY: Record<CredentialKind, { noun: string; newLabel: string; empty: string; intro: string }> = {
  'api-key': {
    noun: 'API key',
    newLabel: 'New API key',
    empty: 'No API keys yet.',
    intro: 'Send the key in an X-API-Key header. Each key only reaches this project.',
  },
  'oauth-client': {
    noun: 'OAuth client',
    newLabel: 'New OAuth client',
    empty: 'No OAuth clients yet.',
    intro:
      'Exchange the client ID and secret for a one-hour bearer token at /api/v1/oauth/token/ (client credentials grant).',
  },
};

interface Props {
  kind: CredentialKind;
  projectId: number;
  resources: VocabularyOption[];
}

export default function CredentialsPanel({ kind, projectId, resources }: Props) {
  const copy = COPY[kind];
  const [rows, setRows] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [revealed, setRevealed] = useState<RevealedSecret | null>(null);
  const [revoking, setRevoking] = useState<Credential | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(
        kind === 'api-key'
          ? await CsmIntegrationsAPI.listApiKeys(projectId)
          : await CsmIntegrationsAPI.listOAuthClients(projectId),
      );
    } catch {
      setError(`Failed to load ${copy.noun}s.`);
    } finally {
      setLoading(false);
    }
  }, [kind, projectId, copy.noun]);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async (data: CreateCredentialData) => {
    if (kind === 'api-key') {
      const created = await CsmIntegrationsAPI.createApiKey(projectId, data);
      setRows((prev) => [created, ...prev]);
      setRevealed({
        title: 'API key created',
        description: `Send it as the X-API-Key header from "${created.name}".`,
        values: [{ label: 'API key', value: created.key, secret: true }],
      });
    } else {
      const created = await CsmIntegrationsAPI.createOAuthClient(projectId, data);
      setRows((prev) => [created, ...prev]);
      setRevealed({
        title: 'OAuth client created',
        description: `Use these with the client credentials grant at /api/v1/oauth/token/ from "${created.name}".`,
        values: [
          { label: 'Client ID', value: created.client_id },
          { label: 'Client secret', value: created.client_secret, secret: true },
        ],
      });
    }
    setCreating(false);
  };

  const handleRevoke = async () => {
    if (!revoking) return;
    setRevokeBusy(true);
    try {
      const updated = kind === 'api-key'
        ? await CsmIntegrationsAPI.revokeApiKey(projectId, revoking.id)
        : await CsmIntegrationsAPI.revokeOAuthClient(projectId, revoking.id);
      setRows((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      toast.success(`${copy.noun} revoked.`);
      setRevoking(null);
    } catch {
      toast.error(`Could not revoke the ${copy.noun}.`);
    } finally {
      setRevokeBusy(false);
    }
  };

  return (
    <section className="flex flex-col gap-4" aria-label={`${copy.noun}s`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-gray-500">{copy.intro}</p>
        <button type="button" onClick={() => setCreating(true)} className={`gap-2 ${PORTAL_SUBMIT_BUTTON_CLASS}`}>
          <Plus className="h-4 w-4" aria-hidden />
          {copy.newLabel}
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
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">{kind === 'api-key' ? 'Key' : 'Client ID'}</th>
                <th className="px-4 py-3">Permissions</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className={`border-t border-gray-100 ${row.is_active ? '' : 'opacity-60'}`}>
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{row.name}</div>
                    {row.created_by_name && <div className="text-xs text-gray-500">by {row.created_by_name}</div>}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-gray-700">
                    {'display_key' in row ? row.display_key : row.client_id}
                  </td>
                  <td className="max-w-xs px-4 py-3 text-xs text-gray-600">{summarizeScopes(row.scopes, resources)}</td>
                  <td className="px-4 py-3 text-gray-700">
                    {formatDateTime(row.created_at)}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge active={row.is_active} inactiveLabel="Revoked" />
                  </td>
                  <td className="px-4 py-3 text-right">
                    {row.is_active && (
                      <button
                        type="button"
                        onClick={() => setRevoking(row)}
                        title="Revoke"
                        aria-label={`Revoke ${row.name}`}
                        className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-600"
                      >
                        <Ban className="h-4 w-4" aria-hidden />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-sm italic text-gray-400">{copy.empty}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <CredentialFormModal
        isOpen={creating}
        title={copy.newLabel}
        resources={resources}
        onClose={() => setCreating(false)}
        onSubmit={handleCreate}
      />
      <SecretRevealModal revealed={revealed} onClose={() => setRevealed(null)} />
      <ConfirmModal
        isOpen={revoking !== null}
        onClose={() => setRevoking(null)}
        onConfirm={handleRevoke}
        title={`Revoke ${copy.noun}?`}
        message={`"${revoking?.name ?? ''}" stops working immediately. Integrations using it will get 401 errors. This cannot be undone.`}
        confirmText="Revoke"
        type="danger"
        loading={revokeBusy}
      />
    </section>
  );
}
