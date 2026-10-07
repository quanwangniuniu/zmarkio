/** CSM settings → API & Webhooks. Mirrors public_api/serializers_admin.py. */

export type CredentialKind = 'api-key' | 'oauth-client';

export interface VocabularyOption {
  value: string;
  label: string;
}

export interface IntegrationsVocabulary {
  resources: VocabularyOption[];
  events: VocabularyOption[];
}

interface CredentialBase {
  id: number;
  name: string;
  /** `<resource>:read` | `<resource>:write`; write implies read. */
  scopes: string[];
  is_active: boolean;
  created_by_name: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface ApiKey extends CredentialBase {
  /** e.g. `zmk_1a2b3c4d_…` — the secret part is never returned again. */
  display_key: string;
  last_used_at: string | null;
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

export interface WebhookEndpoint {
  id: number;
  url: string;
  description: string;
  events: string[];
  is_active: boolean;
  created_at: string;
}

export interface WebhookEndpointWithSecret extends WebhookEndpoint {
  secret: string;
}

export interface WebhookEndpointData {
  url: string;
  description?: string;
  events: string[];
  is_active?: boolean;
}

export type WebhookDeliveryStatus = 'succeeded' | 'retrying' | 'failed';

export interface WebhookDelivery {
  id: number;
  event_type: string;
  target_url: string;
  attempt: number;
  status: WebhookDeliveryStatus;
  response_code: number | null;
  error: string;
  created_at: string;
}
