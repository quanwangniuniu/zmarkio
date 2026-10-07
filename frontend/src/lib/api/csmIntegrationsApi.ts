import api from '../api';
import type {
  ApiKey,
  CreateCredentialData,
  CreatedApiKey,
  CreatedOAuthClient,
  IntegrationsVocabulary,
  OAuthClient,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEndpointData,
  WebhookEndpointWithSecret,
} from '@/types/csmIntegrations';

const BASE = '/api/csm/integrations';

export const CsmIntegrationsAPI = {
  vocabulary(projectId: number) {
    return api
      .get<IntegrationsVocabulary>(`${BASE}/vocabulary/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listApiKeys(projectId: number) {
    return api.get<ApiKey[]>(`${BASE}/api-keys/`, { params: { project: projectId } }).then((res) => res.data);
  },

  createApiKey(projectId: number, data: CreateCredentialData) {
    return api
      .post<CreatedApiKey>(`${BASE}/api-keys/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  revokeApiKey(projectId: number, id: number) {
    return api
      .post<ApiKey>(`${BASE}/api-keys/${id}/revoke/`, null, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listOAuthClients(projectId: number) {
    return api
      .get<OAuthClient[]>(`${BASE}/oauth-clients/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  createOAuthClient(projectId: number, data: CreateCredentialData) {
    return api
      .post<CreatedOAuthClient>(`${BASE}/oauth-clients/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  revokeOAuthClient(projectId: number, id: number) {
    return api
      .post<OAuthClient>(`${BASE}/oauth-clients/${id}/revoke/`, null, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listWebhooks(projectId: number) {
    return api
      .get<WebhookEndpoint[]>(`${BASE}/webhooks/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  createWebhook(projectId: number, data: WebhookEndpointData) {
    return api
      .post<WebhookEndpointWithSecret>(`${BASE}/webhooks/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  updateWebhook(projectId: number, id: number, data: Partial<WebhookEndpointData>) {
    return api
      .patch<WebhookEndpoint>(`${BASE}/webhooks/${id}/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  deleteWebhook(projectId: number, id: number) {
    return api.delete(`${BASE}/webhooks/${id}/`, { params: { project: projectId } });
  },

  rotateWebhookSecret(projectId: number, id: number) {
    return api
      .post<WebhookEndpointWithSecret>(`${BASE}/webhooks/${id}/rotate-secret/`, null, {
        params: { project: projectId },
      })
      .then((res) => res.data);
  },

  sendTestEvent(projectId: number, id: number) {
    return api.post<{ event_id: string }>(`${BASE}/webhooks/${id}/test/`, null, {
      params: { project: projectId },
    });
  },

  /** The newest 20 attempts, optionally for one endpoint. */
  listDeliveries(projectId: number, endpointId: number | null) {
    const params: Record<string, number> = { project: projectId };
    if (endpointId) params.endpoint = endpointId;
    return api
      .get<{ results: WebhookDelivery[] }>(`${BASE}/webhook-deliveries/`, { params })
      .then((res) => res.data.results);
  },
};
