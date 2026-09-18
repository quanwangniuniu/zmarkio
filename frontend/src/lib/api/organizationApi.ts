import api from '../api';

export interface OrganizationData {
  id: number;
  name: string;
  slug: string;
  member_count?: number;
  is_deleted?: boolean;
  created_at?: string;
  updated_at?: string;
}

// List item returned by GET /api/core/organizations/
export interface OrgListItem {
  id: number;
  name: string;
  slug: string;
  desc?: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  is_current: boolean;
  user_role: string | null;
  member_count: number;
}

export interface OrgListResponse {
  organizations: OrgListItem[];
  current_organization_id: number | null;
}

// Detail returned by GET /api/core/organizations/<id>/
export interface OrgSubscription {
  is_active: boolean;
  seat_count: number;
  start_date: string | null;
  end_date: string | null;
  cancel_at_period_end: boolean;
  plan: {
    name: string;
    desc: string | null;
    base_price_cents: number;
    included_seats: number;
    monthly_token_quota: number | null;
    currency: string;
  };
}

export interface OrgUsage {
  tokens_used: number;
  tokens_reserved: number;
  overage_tokens: number;
  updated_at: string | null;
}

export type OrgActivityCategory = 'member' | 'plan' | 'token' | 'other';

export interface OrgActivityUser {
  id: number;
  username: string;
  name: string | null;
}

export interface OrgActivityEvent {
  id: number;
  event_type: string;
  category: OrgActivityCategory;
  actor: OrgActivityUser | null;
  target_user: OrgActivityUser | null;
  metadata: Record<string, unknown>;
  message: string;
  created_at: string;
}

export interface OrgUsageBreakdownItem {
  purpose: string;
  label: string;
  normalized_tokens: number;
  total_cost_cents: number;
}

export interface OrgDetail {
  id: number;
  name: string;
  slug: string;
  desc?: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  is_current: boolean;
  user_role: string | null;
  member_count: number;
  subscription: OrgSubscription | null;
  usage: OrgUsage | null;
  usage_breakdown: OrgUsageBreakdownItem[];
  recent_activity: OrgActivityEvent[];
  max_concurrent_sessions: number;
}

// Member returned by GET /api/core/organizations/<id>/members/
export interface OrgMember {
  id: number;
  user: {
    id: number;
    username: string;
    email: string;
    name: string;
  };
  role: string;
  joined_at: string;
}

export interface OrgMembersResponse {
  organization: { id: number; name: string; slug: string };
  members: OrgMember[];
  member_count: number;
}

export interface CreateOrganizationPayload {
  name: string;
}

export interface CreateOrganizationResponse {
  organization: OrganizationData;
  message?: string;
}

export interface ValidateSlugResponse {
  exists: boolean;
  organization: OrganizationData | null;
}

export interface JoinOrganizationPayload {
  slug: string;
}

export interface JoinOrganizationResponse {
  organization: OrganizationData;
  message?: string;
}

export const OrganizationAPI = {
  // Create a new organization
  createOrganization: (payload: CreateOrganizationPayload): Promise<CreateOrganizationResponse> => {
    return api
      .post<CreateOrganizationResponse>('/api/core/organizations/create/', payload)
      .then((response) => response.data);
  },

  // Validate if an organization slug exists
  validateSlug: (slug: string): Promise<ValidateSlugResponse> => {
    return api
      .get<ValidateSlugResponse>('/api/core/organizations/validate-slug/', {
        params: { slug },
      })
      .then((response) => response.data);
  },

  // Join an organization by slug
  joinOrganization: (payload: JoinOrganizationPayload): Promise<JoinOrganizationResponse> => {
    return api
      .post<JoinOrganizationResponse>('/api/core/organizations/join/', payload)
      .then((response) => response.data);
  },

  // Get all organizations the current user belongs to
  getUserOrganizations: (): Promise<OrgListResponse> => {
    return api
      .get<OrgListResponse>('/api/core/organizations/')
      .then((response) => response.data);
  },

  // Get detailed info for a single organization (incl. subscription + usage)
  getOrganizationDetail: (orgId: number | string): Promise<OrgDetail> => {
    return api
      .get<OrgDetail>(`/api/core/organizations/${orgId}/`)
      .then((response) => response.data);
  },

  // Get members list for an organization (admin only)
  getOrganizationMembers: (orgId: number | string): Promise<OrgMembersResponse> => {
    return api
      .get<OrgMembersResponse>(`/api/core/organizations/${orgId}/members/`)
      .then((response) => response.data);
  },

  // Remove a member from an organization (admin only)
  removeMember: (orgId: number, userId: number): Promise<{ message: string }> => {
    return api
      .delete(`/api/core/organizations/${orgId}/members/${userId}/`)
      .then((response) => response.data);
  },

  // Update an organization's slug (admin only)
  updateOrgSlug: (orgId: number, slug: string): Promise<{ slug: string; message: string }> => {
    return api
      .patch<{ slug: string; message: string }>(`/api/core/organizations/${orgId}/slug/`, { slug })
      .then((response) => response.data);
  },

  // Update organization settings (admin only)
  updateOrgSettings: (orgId: number, settings: { max_concurrent_sessions: number }): Promise<{ max_concurrent_sessions: number }> => {
    return api
      .patch<{ max_concurrent_sessions: number }>(`/api/core/organizations/${orgId}/`, settings)
      .then((response) => response.data);
  },

  // Delete an organization (admin only, must have ≥1 other org)
  // force=true bypasses the last-org check (used by onboarding undo flow)
  deleteOrganization: (orgId: number, force = false): Promise<{ message: string }> => {
    return api
      .delete(`/api/core/organizations/${orgId}/`, { params: force ? { force: 'true' } : undefined })
      .then((response) => response.data);
  },

  // Switch the current user's active organization
  switchOrganization: (orgId: number): Promise<{ message: string; current_organization_id: number }> => {
    return api
      .post('/api/core/organizations/switch/', { organization_id: orgId })
      .then((response) => response.data);
  },

  // Create an org invitation and send notification (admin only)
  createOrgInvitation: (
    orgId: number,
    email: string,
    role: 'admin' | 'member' | 'viewer' = 'member'
  ): Promise<{ id: number; token: string; invitation_url: string }> => {
    return api
      .post(`/api/core/organizations/${orgId}/invitations/`, {
        email,
        role,
        max_uses: 1,
        expires_days: 7,
      })
      .then((response) => response.data);
  },
};
