import api from '../api';
import type {
  ApiKey,
  CreateCredentialData,
  CreatedApiKey,
  CreatedOAuthClient,
  DeliveryFilters,
  IntegrationsVocabulary,
  OAuthClient,
  Paginated,
  WebhookDelivery,
  WebhookEndpoint,
  WebhookEndpointData,
  WebhookEndpointWithSecret,
} from '@/types/csmIntegrations';

const BASE = '/api/csm/integrations';

function unwrap<T>(data: T[] | Paginated<T>): T[] {
  return Array.isArray(data) ? data : data?.results ?? [];
}

export const CsmIntegrationsAPI = {
  vocabulary(projectId: number) {
    return api
      .get<IntegrationsVocabulary>(`${BASE}/vocabulary/`, { params: { project: projectId } })
      .then((res) => res.data);
  },

  listApiKeys(projectId: number) {
    return api
      .get<ApiKey[] | Paginated<ApiKey>>(`${BASE}/api-keys/`, { params: { project: projectId, page_size: 100 } })
      .then((res) => unwrap(res.data));
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
      .get<OAuthClient[] | Paginated<OAuthClient>>(`${BASE}/oauth-clients/`, {
        params: { project: projectId, page_size: 100 },
      })
      .then((res) => unwrap(res.data));
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
      .get<WebhookEndpoint[] | Paginated<WebhookEndpoint>>(`${BASE}/webhooks/`, {
        params: { project: projectId, page_size: 100 },
      })
      .then((res) => unwrap(res.data));
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

  listDeliveries(projectId: number, filters: DeliveryFilters = {}) {
    const params: Record<string, string | number> = { project: projectId };
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params[key] = value;
    });
    return api
      .get<Paginated<WebhookDelivery>>(`${BASE}/webhook-deliveries/`, { params })
      .then((res) => res.data);
  },

  redeliver(projectId: number, deliveryId: number) {
    return api.post<{ event_id: string }>(`${BASE}/webhook-deliveries/${deliveryId}/redeliver/`, null, {
      params: { project: projectId },
    });
  },
};
