import api from '@/lib/api';
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
} from '@/external-api/types';

const BASE = '/api/external-api';

export const ExternalApiAdminAPI = {
  vocabulary(projectId: number | string) {
    return api
      .get<IntegrationsVocabulary>(`${BASE}/vocabulary/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listApiKeys(projectId: number | string) {
    return api.get<ApiKey[]>(`${BASE}/api-keys/`, { params: { project: projectId } }).then((res) => res.data);
  },

  createApiKey(projectId: number | string, data: CreateCredentialData) {
    return api
      .post<CreatedApiKey>(`${BASE}/api-keys/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  revokeApiKey(projectId: number | string, id: number) {
    return api
      .post<ApiKey>(`${BASE}/api-keys/${id}/revoke/`, null, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listOAuthClients(projectId: number | string) {
    return api
      .get<OAuthClient[]>(`${BASE}/oauth-clients/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  createOAuthClient(projectId: number | string, data: CreateCredentialData) {
    return api
      .post<CreatedOAuthClient>(`${BASE}/oauth-clients/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  revokeOAuthClient(projectId: number | string, id: number) {
    return api
      .post<OAuthClient>(`${BASE}/oauth-clients/${id}/revoke/`, null, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listWebhooks(projectId: number | string) {
    return api
      .get<WebhookEndpoint[]>(`${BASE}/webhooks/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  createWebhook(projectId: number | string, data: WebhookEndpointData) {
    return api
      .post<WebhookEndpointWithSecret>(`${BASE}/webhooks/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  deleteWebhook(projectId: number | string, id: number) {
    return api.delete(`${BASE}/webhooks/${id}/`, { params: { project: projectId } });
  },

  /** The newest 20 attempts. */
  listDeliveries(projectId: number | string) {
    return api
      .get<{ results: WebhookDelivery[] }>(`${BASE}/webhook-deliveries/`, { params: { project: projectId } })
      .then((res) => res.data.results);
  },
};
