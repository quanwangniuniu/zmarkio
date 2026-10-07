/** CSM settings → API & Webhooks (MED-226). Mirrors public_api/serializers_admin.py. */

export type CredentialKind = 'api-key' | 'oauth-client';

export interface IntegrationsVocabulary {
  resources: string[];
  /** `<resource>:read` | `<resource>:write`; write implies read. */
  scopes: string[];
  events: WebhookEventType[];
}

interface CredentialBase {
  id: number;
  name: string;
  scopes: string[];
  is_active: boolean;
  created_by_name: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface ApiKey extends CredentialBase {
  prefix: string;
  /** e.g. `zmk_1a2b3c4d_…` — the secret part is never returned again. */
  display_key: string;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface OAuthClient extends CredentialBase {
  client_id: string;
}

export interface CreateCredentialData {
  name: string;
  scopes: string[];
}

/** Create responses carry the secret once. */
export interface CreatedApiKey extends ApiKey {
  key: string;
}

export interface CreatedOAuthClient extends OAuthClient {
  client_secret: string;
}

export type WebhookEventType = 'ticket.created' | 'ticket.status_changed' | 'sla.breached';

export type WebhookDeliveryStatus = 'pending' | 'succeeded' | 'retrying' | 'failed';

export interface WebhookEndpoint {
  id: number;
  url: string;
  description: string;
  events: WebhookEventType[];
  is_active: boolean;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
  last_delivery_status: WebhookDeliveryStatus | null;
  last_delivery_at: string | null;
}

export interface WebhookEndpointWithSecret extends WebhookEndpoint {
  secret: string;
}

export interface WebhookEndpointData {
  url: string;
  description?: string;
  events: WebhookEventType[];
  is_active?: boolean;
}

export interface WebhookDelivery {
  id: number;
  endpoint: number;
  event_id: string;
  event_type: WebhookEventType | 'ping';
  target_url: string;
  attempt: number;
  status: WebhookDeliveryStatus;
  response_code: number | null;
  error: string;
  duration_ms: number | null;
  next_retry_at: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface DeliveryFilters {
  endpoint?: number;
  event_type?: string;
  status?: WebhookDeliveryStatus;
  page?: number;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}
