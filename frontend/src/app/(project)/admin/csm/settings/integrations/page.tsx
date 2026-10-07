'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertCircle } from 'lucide-react';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import CsmSettingsPageRoot, { CsmSettingsProjectGuard } from '@/components/csm-settings/CsmSettingsPageRoot';
import SettingsHubLink from '@/components/csm-settings/SettingsHubLink';
import CredentialsPanel from '@/components/csm-settings/integrations/CredentialsPanel';
import WebhooksPanel from '@/components/csm-settings/integrations/WebhooksPanel';
import { useProjectIdFromUrl } from '@/components/csm-settings/useProjectIdFromUrl';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { CsmIntegrationsAPI } from '@/lib/api/csmIntegrationsApi';
import type { IntegrationsVocabulary } from '@/types/csmIntegrations';

const TABS = [
  { id: 'api-key', label: 'API keys' },
  { id: 'oauth-client', label: 'OAuth clients' },
  { id: 'webhooks', label: 'Webhooks' },
] as const;

type TabId = (typeof TABS)[number]['id'];

function IntegrationsSettings() {
  const { projectId, projectValid } = useProjectIdFromUrl();
  const [tab, setTab] = useState<TabId>('api-key');
  const [vocabulary, setVocabulary] = useState<IntegrationsVocabulary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectValid) return;
    setError(null);
    try {
      setVocabulary(await CsmIntegrationsAPI.vocabulary(projectId));
    } catch (err: unknown) {
      const forbidden = (err as { response?: { status?: number } })?.response?.status === 403;
      setError(forbidden
        ? 'Only an organisation admin or a CSM admin of this workspace can manage API access and webhooks.'
        : 'Failed to load API & webhook settings.');
    }
  }, [projectId, projectValid]);

  useEffect(() => { load(); }, [load]);

  return (
    <CsmSettingsPageRoot>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">API &amp; Webhooks</h1>
          <p className="mt-1 text-sm text-gray-500">
            Let external systems use the REST API at <code className="font-mono">/api/v1/csm/</code>, and send them
            signed events when tickets change.
          </p>
        </div>
        {projectValid && <SettingsHubLink projectId={projectId} />}
      </div>

      {!projectValid ? (
        <CsmSettingsProjectGuard />
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
                id={`integrations-tab-${id}`}
                aria-selected={tab === id}
                aria-controls={`integrations-panel-${id}`}
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
          <div role="tabpanel" id={`integrations-panel-${tab}`} aria-labelledby={`integrations-tab-${tab}`}>
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
    </CsmSettingsPageRoot>
  );
}

export default function IntegrationsSettingsPage() {
  return (
    <ProtectedRoute requiredAuth requireAdmin fallback="/unauthorized">
      <IntegrationsSettings />
    </ProtectedRoute>
  );
}
