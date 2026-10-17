'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { ExternalApiAdminAPI } from '@/external-api/api';
import CredentialsPanel from '@/external-api/components/CredentialsPanel';
import WebhooksPanel from '@/external-api/components/WebhooksPanel';
import type { IntegrationsVocabulary } from '@/external-api/types';
import { useActiveProjectForFlatRoute } from '@/lib/useActiveProjectForFlatRoute';

const TABS = [
  { id: 'api-key', label: 'API keys' },
  { id: 'oauth-client', label: 'OAuth clients' },
  { id: 'webhooks', label: 'Webhooks' },
] as const;

type TabId = (typeof TABS)[number]['id'];

function ExternalApiAdmin() {
  const { activeProject } = useActiveProjectForFlatRoute();
  const projectId = activeProject?.id ?? '';
  const projectValid = Boolean(activeProject?.id);
  const [tab, setTab] = useState<TabId>('api-key');
  const [vocabulary, setVocabulary] = useState<IntegrationsVocabulary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectValid) return;
    setError(null);
    try {
      setVocabulary(await ExternalApiAdminAPI.vocabulary(projectId));
    } catch (err: unknown) {
      const forbidden = (err as { response?: { status?: number } })?.response?.status === 403;
      setError(forbidden
        ? 'Only an organisation admin or a CSM admin of this workspace can manage API access and webhooks.'
        : 'Failed to load API & webhook settings.');
    }
  }, [projectId, projectValid]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">API &amp; Webhooks</h1>
        <p className="mt-1 text-sm text-gray-500">
          Let external systems use the REST API at <code className="font-mono">/api/v1/csm/</code>, and send them
          signed events when tickets change.
        </p>
      </div>

      {!projectValid ? (
        <p className="text-sm text-gray-500">Select a project to manage its API access and webhooks.</p>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
          {error}
        </div>
      ) : !vocabulary ? (
        <div className="flex min-h-[300px] items-center justify-center"><LoadingSpinner /></div>
      ) : (
        <>
          <div role="tablist" aria-label="API & Webhooks sections" className="flex gap-1 border-b border-gray-200">
            {TABS.map(({ id, label }) => (
              <button
                key={id}
                type="button"
                role="tab"
                id={`external-api-tab-${id}`}
                aria-selected={tab === id}
                aria-controls={`external-api-panel-${id}`}
                onClick={() => setTab(id)}
                className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
                  tab === id
                    ? 'border-indigo-600 text-indigo-700'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`external-api-panel-${tab}`} aria-labelledby={`external-api-tab-${tab}`}>
            {tab === 'webhooks' ? (
              <WebhooksPanel projectId={projectId} events={vocabulary.events} />
            ) : (
              <CredentialsPanel
                key={tab}
                kind={tab}
                projectId={projectId}
                resources={vocabulary.resources}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function ExternalApiPage() {
  return (
    <ProtectedRoute requiredAuth requireAdmin fallback="/unauthorized">
      <DashboardLayout alerts={[]} upcomingMeetings={[]}>
        <ExternalApiAdmin />
      </DashboardLayout>
    </ProtectedRoute>
  );
}
