import api, { SYNC_TIMEOUT_MS } from "../api";

export interface FacebookAdAccount {
  id: number;
  meta_account_id: string;
  name: string;
  currency: string;
  timezone_name: string;
  account_status: number | null;
  business_id: string;
  is_owned: boolean;
  project_id: number | null;
  connected_by_current_user: boolean;
  can_manage: boolean;
  can_sync: boolean;
  connector_name: string;
}

export interface FacebookStatus {
  connected: boolean;
  fb_user_name?: string;
  fb_email?: string | null;
  business_id?: string;
  business_name?: string;
  token_expires_at?: string | null;
  last_synced_at?: string | null;
  ad_accounts?: FacebookAdAccount[];
}

export interface FacebookAdAccountListParams {
  project_id?: number | string;
  page?: number;
  page_size?: number;
  search?: string;
}

export interface FacebookAdAccountListResponse {
  count: number;
  page: number;
  page_size: number;
  results: FacebookAdAccount[];
}

export interface FacebookConnectPayload {
  authorize_url: string;
  state: string;
}

export interface MetaSummaryAggregates {
  spend: string;
  impressions: number;
  clicks: number;
  reach: number;
  leads: number;
  calls: number;
  purchases: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpm: string;
  cpl: string;
  cpcall: string;
  roas: string;
}

export interface MetaTimeseriesPoint {
  date: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  calls: number;
  purchases: number;
  revenue: string;
}

export interface MetaSummary {
  ad_account_id: number;
  currency: string;
  days: number;
  window: { since: string; until: string };
  aggregates: MetaSummaryAggregates;
  timeseries: MetaTimeseriesPoint[];
}

export interface MetaCampaignPerformanceRow {
  id: number;
  slug: string;
  meta_campaign_id: string;
  name: string;
  objective: string;
  effective_status: string;
  daily_budget_cents: number | null;
  lifetime_budget_cents: number | null;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpm: string;
  cpl: string;
  cpa: string;
  roas: string;
}

export interface MetaCampaignPerformance {
  ad_account_id: number;
  currency: string;
  days: number;
  window: { since: string; until: string };
  campaigns: MetaCampaignPerformanceRow[];
}

export interface MetaSyncRun {
  id: number;
  ad_account: number;
  kind: string;
  status: "running" | "ok" | "partial" | "error";
  level_counts: Record<string, number>;
  error_message: string;
  started_at: string;
  finished_at: string | null;
  current_phase: string;
  current_progress: string;
}

export interface MetaCreativePerformanceRow {
  id: number;
  slug: string;
  meta_creative_id: string;
  name: string;
  title: string;
  body: string;
  thumbnail_url: string;
  image_url: string;
  video_id: string;
  object_type: string;
  call_to_action_type: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  calls: number;
  purchases: number;
  messages: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpl: string;
  cpa: string;
  roas: string;
  video_p25: number;
  video_p75: number;
  video_p100: number;
  hook_rate: string;
  hook_rate_strict: string;
  hold_rate: string;
  completion_rate: string;
  video_3sec_count: number;
  lpv_count: number;
  cost_per_lpv: string;
  comment_count: number;
  cost_per_comment: string;
  total_events: number;
  days_with_data: number;
  is_in_learning: boolean | null;
  ad_count: number;
}

export interface MetaCreativePerformanceFilters {
  min_impressions: number;
  min_spend: string;
  min_events: number;
  min_days_with_data: number;
  include_inactive: boolean;
  include_shared_creatives: boolean;
}

export interface MetaCreativePerformance {
  ad_account_id: number;
  currency: string;
  days: number;
  window: { since: string; until: string };
  filters: MetaCreativePerformanceFilters;
  creatives: MetaCreativePerformanceRow[];
}

export interface MetaAdPerformanceRow {
  id: number;
  meta_ad_id: string;
  name: string;
  effective_status: string;
  adset_id: number;
  adset_name: string;
  campaign_id: number | null;
  campaign_name: string;
  creative:
    | {
        id: number;
        slug: string;
        meta_creative_id: string;
        title: string;
        thumbnail_url: string;
        video_id: string;
        object_type: string;
      }
    | null;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  calls: number;
  purchases: number;
  messages: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpl: string;
  cpa: string;
  roas: string;
  hook_rate: string;
  hook_rate_strict: string;
  hold_rate: string;
  completion_rate: string;
  video_3sec_count: number;
  lpv_count: number;
  cost_per_lpv: string;
  comment_count: number;
  cost_per_comment: string;
  total_events: number;
  days_with_data: number;
  is_in_learning: boolean | null;
}

export interface MetaAdPerformanceFilters {
  min_impressions: number;
  min_spend: string;
  min_events: number;
  min_days_with_data: number;
  include_inactive: boolean;
  ids: number[];
}

export interface MetaAdPerformance {
  ad_account_id: number;
  currency: string;
  days: number;
  window: { since: string; until: string };
  filters: MetaAdPerformanceFilters;
  ads: MetaAdPerformanceRow[];
}

export interface MetaAdInsightPoint {
  date: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
  video_p25: number;
  video_p75: number;
  video_p100: number;
  hook_rate: string;
  hold_rate: string;
}

export interface MetaAdInsightTimeseries {
  ad_account_id: number;
  ad_id: number;
  meta_ad_id: string;
  ad_name: string;
  currency: string;
  days: number;
  window: { since: string; until: string };
  points: MetaAdInsightPoint[];
}

export interface MetaCreativeDetailLinkedAd {
  id: number;
  meta_ad_id: string;
  name: string;
  effective_status: string;
  campaign_name: string;
  adset_name: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
  roas: string;
}

export interface MetaCreativeDetailAggregates {
  spend: string;
  impressions: number;
  clicks: number;
  reach: number;
  leads: number;
  calls: number;
  purchases: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpm: string;
  cpl: string;
  cpa: string;
  roas: string;
  video_p25: number;
  video_p50: number;
  video_p75: number;
  video_p100: number;
  hook_rate: string;
  hold_rate: string;
  completion_rate: string;
}

export interface MetaCreativeDetail {
  id: number;
  slug: string;
  meta_creative_id: string;
  ad_account_id: number;
  currency: string;
  name: string;
  title: string;
  body: string;
  image_url: string;
  thumbnail_url: string;
  video_id: string;
  object_type: string;
  call_to_action_type: string;
  asset_feed_spec: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  days: number;
  window: { since: string; until: string };
  aggregates: MetaCreativeDetailAggregates;
  linked_ads: MetaCreativeDetailLinkedAd[];
  linked_ads_count: number;
}

export type MetaAdPreviewFormat =
  | "MOBILE_FEED_STANDARD"
  | "DESKTOP_FEED_STANDARD"
  | "FACEBOOK_STORY_MOBILE"
  | "INSTAGRAM_STANDARD"
  | "INSTAGRAM_STORY"
  | "FACEBOOK_REELS_MOBILE";

export interface MetaCreativePreview {
  creative_id: number;
  video_id: string;
  meta_ad_id: string;
  ad_name: string;
  ad_format: MetaAdPreviewFormat;
  iframe_src: string;
  iframe_html: string;
  thumbnail_url: string;
  permalink_url: string;
}

export interface MetaAdSetPerformanceRow {
  id: number;
  slug: string;
  meta_adset_id: string;
  name: string;
  effective_status: string;
  optimization_goal: string;
  daily_budget_cents: number | null;
  lifetime_budget_cents: number | null;
  campaign_id: number;
  campaign_name: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpl: string;
  cpa: string;
  roas: string;
}

export interface MetaAdSetPerformance {
  ad_account_id: number;
  currency: string;
  days: number;
  window: { since: string; until: string };
  campaign_id: number | null;
  adsets: MetaAdSetPerformanceRow[];
}

export interface MetaCreativeTimeseries {
  creative_id: number;
  meta_creative_id: string;
  currency: string;
  days: number;
  window: { since: string; until: string };
  points: MetaAdInsightPoint[];
}

export interface MetaCampaignDetailAggregates {
  spend: string;
  impressions: number;
  clicks: number;
  reach: number;
  leads: number;
  calls: number;
  purchases: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpm: string;
  cpl: string;
  cpa: string;
  roas: string;
}

export interface MetaCampaignDetailLinkedAdSet {
  id: number;
  slug: string;
  meta_adset_id: string;
  name: string;
  effective_status: string;
  optimization_goal: string;
  daily_budget_cents: number | null;
  lifetime_budget_cents: number | null;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
  roas: string;
}

export interface MetaCampaignDetail {
  id: number;
  slug: string;
  meta_campaign_id: string;
  ad_account_id: number;
  currency: string;
  name: string;
  objective: string;
  status: string;
  effective_status: string;
  start_time: string | null;
  stop_time: string | null;
  daily_budget_cents: number | null;
  lifetime_budget_cents: number | null;
  special_ad_categories: string[];
  created_at: string;
  updated_at: string;
  days: number;
  window: { since: string; until: string };
  aggregates: MetaCampaignDetailAggregates;
  linked_adsets: MetaCampaignDetailLinkedAdSet[];
  linked_adsets_count: number;
}

export interface MetaCampaignTimeseriesPoint {
  date: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
}

export interface MetaCampaignTimeseries {
  campaign_id: number;
  meta_campaign_id: string;
  currency: string;
  days: number;
  window: { since: string; until: string };
  points: MetaCampaignTimeseriesPoint[];
}

export interface MetaAdSetDetailAggregates extends MetaCampaignDetailAggregates {
  hook_rate: string;
  hold_rate: string;
}

export interface MetaAdSetDetailLinkedAd {
  id: number;
  meta_ad_id: string;
  name: string;
  effective_status: string;
  creative: {
    id: number;
    slug: string;
    meta_creative_id: string;
    title: string;
    thumbnail_url: string;
  } | null;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  purchases: number;
  revenue: string;
  roas: string;
}

export interface MetaAdSetDetail {
  id: number;
  slug: string;
  meta_adset_id: string;
  ad_account_id: number;
  currency: string;
  name: string;
  status: string;
  effective_status: string;
  optimization_goal: string;
  billing_event: string;
  bid_amount_cents: number | null;
  daily_budget_cents: number | null;
  lifetime_budget_cents: number | null;
  campaign: {
    id: number;
    slug: string;
    meta_campaign_id: string;
    name: string;
  };
  created_at: string;
  updated_at: string;
  days: number;
  window: { since: string; until: string };
  aggregates: MetaAdSetDetailAggregates;
  linked_ads: MetaAdSetDetailLinkedAd[];
  linked_ads_count: number;
}

export interface MetaAdSetTimeseries {
  adset_id: number;
  meta_adset_id: string;
  currency: string;
  days: number;
  window: { since: string; until: string };
  points: MetaAdInsightPoint[];
}

export const facebookApi = {
  getStatus: async (projectId?: number | string | null): Promise<FacebookStatus> => {
    const response = await api.get("/api/facebook_integration/status/", {
      params: projectId ? { project_id: projectId } : undefined,
    });
    return response.data;
  },

  listAdAccounts: async (
    params?: FacebookAdAccountListParams
  ): Promise<FacebookAdAccountListResponse> => {
    const response = await api.get<FacebookAdAccountListResponse>(
      "/api/facebook_integration/ad_accounts/",
      { params }
    );
    return response.data;
  },

  connect: async (projectId?: number | string): Promise<FacebookConnectPayload> => {
    const response = await api.get("/api/facebook_integration/connect/", {
      params: projectId ? { project_id: projectId } : undefined,
    });
    return response.data;
  },

  disconnect: async (): Promise<{ connected: boolean }> => {
    const response = await api.post("/api/facebook_integration/disconnect/");
    return response.data;
  },

  sync: async (): Promise<FacebookStatus> => {
    // Synchronously calls the live Facebook Graph API (profile, businesses,
    // ad accounts) before responding; the shared client's 10s default is too
    // short for that round trip.
    const response = await api.post(
      "/api/facebook_integration/sync/",
      undefined,
      { timeout: SYNC_TIMEOUT_MS }
    );
    return response.data;
  },

  linkAdAccountToProject: async (
    adAccountId: number,
    projectId: number | string | null
  ): Promise<FacebookAdAccount> => {
    const response = await api.post(
      `/api/facebook_integration/ad_accounts/${adAccountId}/link_project/`,
      { project_id: projectId }
    );
    return response.data;
  },

  getMetaSummary: async (
    adAccountId: number,
    days: number
  ): Promise<MetaSummary> => {
    const response = await api.get("/api/meta_ads/summary/", {
      params: { ad_account: adAccountId, days },
    });
    return response.data;
  },

  getMetaCampaignPerformance: async (
    adAccountId: number,
    days: number
  ): Promise<MetaCampaignPerformance> => {
    const response = await api.get(
      `/api/meta_ads/ad_accounts/${adAccountId}/campaign_performance/`,
      { params: { days } }
    );
    return response.data;
  },

  triggerAdAccountSync: async (
    adAccountId: number
  ): Promise<{ status: string; task_id: string; ad_account_id: number }> => {
    const response = await api.post(
      `/api/meta_ads/ad_accounts/${adAccountId}/sync/`
    );
    return response.data;
  },

  getSyncRuns: async (adAccountId: number): Promise<MetaSyncRun[]> => {
    const response = await api.get(
      `/api/meta_ads/ad_accounts/${adAccountId}/sync_runs/`
    );
    return response.data;
  },

  getMetaCreativePerformance: async (
    adAccountId: number,
    days: number,
    filters?: {
      minImpressions?: number;
      minSpend?: number | string;
      minEvents?: number;
      minDaysWithData?: number;
      includeInactive?: boolean;
      includeSharedCreatives?: boolean;
    }
  ): Promise<MetaCreativePerformance> => {
    const params: Record<string, string | number> = { days };
    if (filters?.minImpressions) params.min_impressions = filters.minImpressions;
    if (filters?.minSpend) params.min_spend = String(filters.minSpend);
    if (filters?.minEvents) params.min_events = filters.minEvents;
    if (filters?.minDaysWithData) params.min_days_with_data = filters.minDaysWithData;
    if (filters?.includeInactive) params.include_inactive = "true";
    if (filters?.includeSharedCreatives) params.include_shared_creatives = "true";
    const response = await api.get(
      `/api/meta_ads/ad_accounts/${adAccountId}/creative_performance/`,
      { params }
    );
    return response.data;
  },

  getMetaAdPerformance: async (
    adAccountId: number,
    days: number,
    filters?: {
      campaignId?: number | null;
      adsetId?: number | null;
      minImpressions?: number;
      minSpend?: number | string;
      minEvents?: number;
      minDaysWithData?: number;
      includeInactive?: boolean;
      ids?: number[];
    }
  ): Promise<MetaAdPerformance> => {
    const params: Record<string, string | number> = { days };
    if (filters?.campaignId) params.campaign_id = filters.campaignId;
    if (filters?.adsetId) params.adset_id = filters.adsetId;
    if (filters?.minImpressions) params.min_impressions = filters.minImpressions;
    if (filters?.minSpend) params.min_spend = String(filters.minSpend);
    if (filters?.minEvents) params.min_events = filters.minEvents;
    if (filters?.minDaysWithData) params.min_days_with_data = filters.minDaysWithData;
    if (filters?.includeInactive) params.include_inactive = "true";
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.get(
      `/api/meta_ads/ad_accounts/${adAccountId}/ad_performance/`,
      { params }
    );
    return response.data;
  },

  getMetaAdExportCsv: async (
    adAccountId: number,
    days: number,
    filters?: {
      ids?: number[];
      campaignId?: number | null;
      adsetId?: number | null;
      minImpressions?: number;
      minSpend?: number | string;
      minEvents?: number;
      minDaysWithData?: number;
      includeInactive?: boolean;
    }
  ): Promise<{ blob: Blob; filename: string }> => {
    const params: Record<string, string | number> = { days };
    if (filters?.campaignId) params.campaign_id = filters.campaignId;
    if (filters?.adsetId) params.adset_id = filters.adsetId;
    if (filters?.minImpressions) params.min_impressions = filters.minImpressions;
    if (filters?.minSpend) params.min_spend = String(filters.minSpend);
    if (filters?.minEvents) params.min_events = filters.minEvents;
    if (filters?.minDaysWithData) {
      params.min_days_with_data = filters.minDaysWithData;
    }
    if (filters?.includeInactive) params.include_inactive = "true";
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.get<Blob>(
      `/api/meta_ads/ad_accounts/${adAccountId}/ad_performance/export.csv/`,
      { params, responseType: "blob" }
    );
    const disposition =
      (response.headers["content-disposition"] as string | undefined) ?? "";
    const match = disposition.match(/filename="([^"]+)"/i);
    const filename = match?.[1] ?? `meta-ads-${adAccountId}-${days}d.csv`;
    return { blob: response.data, filename };
  },

  getMetaCreativePerformanceCsv: async (
    adAccountId: number,
    days: number,
    filters?: {
      ids?: number[];
      includeInactive?: boolean;
      includeSharedCreatives?: boolean;
      minImpressions?: number;
      minSpend?: number | string;
      minEvents?: number;
      minDaysWithData?: number;
    }
  ): Promise<{ blob: Blob; filename: string }> => {
    const params: Record<string, string | number> = { days };
    if (filters?.minImpressions) params.min_impressions = filters.minImpressions;
    if (filters?.minSpend) params.min_spend = String(filters.minSpend);
    if (filters?.minEvents) params.min_events = filters.minEvents;
    if (filters?.minDaysWithData) {
      params.min_days_with_data = filters.minDaysWithData;
    }
    if (filters?.includeInactive) params.include_inactive = "true";
    if (filters?.includeSharedCreatives) {
      params.include_shared_creatives = "true";
    }
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.get<Blob>(
      `/api/meta_ads/ad_accounts/${adAccountId}/creative_performance/export.csv/`,
      { params, responseType: "blob" }
    );
    const disposition =
      (response.headers["content-disposition"] as string | undefined) ?? "";
    const match = disposition.match(/filename="([^"]+)"/i);
    const filename =
      match?.[1] ?? `meta-creatives-${adAccountId}-${days}d.csv`;
    return { blob: response.data, filename };
  },

  getMetaCampaignPerformanceCsv: async (
    adAccountId: number,
    days: number,
    filters?: { ids?: number[] }
  ): Promise<{ blob: Blob; filename: string }> => {
    const params: Record<string, string | number> = { days };
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.get<Blob>(
      `/api/meta_ads/ad_accounts/${adAccountId}/campaign_performance/export.csv/`,
      { params, responseType: "blob" }
    );
    const disposition =
      (response.headers["content-disposition"] as string | undefined) ?? "";
    const match = disposition.match(/filename="([^"]+)"/i);
    const filename =
      match?.[1] ?? `meta-campaigns-${adAccountId}-${days}d.csv`;
    return { blob: response.data, filename };
  },

  exportAdsToSpreadsheet: async (
    adAccountId: number,
    projectId: number | string,
    name: string,
    days: number,
    filters?: {
      ids?: number[];
      includeInactive?: boolean;
      minImpressions?: number;
      minSpend?: number | string;
      minEvents?: number;
      minDaysWithData?: number;
    }
  ): Promise<{ id: number; name: string; url: string }> => {
    const params: Record<string, string | number> = { project_id: projectId, days };
    if (filters?.minImpressions) params.min_impressions = filters.minImpressions;
    if (filters?.minSpend) params.min_spend = String(filters.minSpend);
    if (filters?.minEvents) params.min_events = filters.minEvents;
    if (filters?.minDaysWithData) {
      params.min_days_with_data = filters.minDaysWithData;
    }
    if (filters?.includeInactive) params.include_inactive = "true";
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.post<{
      id: number;
      name: string;
      url: string;
    }>(
      `/api/meta_ads/ad_accounts/${adAccountId}/ad_performance/export.spreadsheet/`,
      { name },
      { params }
    );
    return response.data;
  },

  exportCreativesToSpreadsheet: async (
    adAccountId: number,
    projectId: number | string,
    name: string,
    days: number,
    filters?: {
      ids?: number[];
      includeInactive?: boolean;
      includeSharedCreatives?: boolean;
      minImpressions?: number;
      minSpend?: number | string;
      minEvents?: number;
      minDaysWithData?: number;
    }
  ): Promise<{ id: number; name: string; url: string }> => {
    const params: Record<string, string | number> = { project_id: projectId, days };
    if (filters?.minImpressions) params.min_impressions = filters.minImpressions;
    if (filters?.minSpend) params.min_spend = String(filters.minSpend);
    if (filters?.minEvents) params.min_events = filters.minEvents;
    if (filters?.minDaysWithData) {
      params.min_days_with_data = filters.minDaysWithData;
    }
    if (filters?.includeInactive) params.include_inactive = "true";
    if (filters?.includeSharedCreatives) {
      params.include_shared_creatives = "true";
    }
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.post<{
      id: number;
      name: string;
      url: string;
    }>(
      `/api/meta_ads/ad_accounts/${adAccountId}/creative_performance/export.spreadsheet/`,
      { name },
      { params }
    );
    return response.data;
  },

  exportCampaignsToSpreadsheet: async (
    adAccountId: number,
    projectId: number | string,
    name: string,
    days: number,
    filters?: { ids?: number[] }
  ): Promise<{ id: number; name: string; url: string }> => {
    const params: Record<string, string | number> = { project_id: projectId, days };
    if (filters?.ids && filters.ids.length > 0) {
      params.ids = filters.ids.join(",");
    }
    const response = await api.post<{
      id: number;
      name: string;
      url: string;
    }>(
      `/api/meta_ads/ad_accounts/${adAccountId}/campaign_performance/export.spreadsheet/`,
      { name },
      { params }
    );
    return response.data;
  },

  getMetaAdSetPerformance: async (
    adAccountId: number,
    days: number,
    campaignId?: number | null
  ): Promise<MetaAdSetPerformance> => {
    const params: Record<string, number> = { days };
    if (campaignId) params.campaign_id = campaignId;
    const response = await api.get(
      `/api/meta_ads/ad_accounts/${adAccountId}/adset_performance/`,
      { params }
    );
    return response.data;
  },

  getMetaAdInsightTimeseries: async (
    adAccountId: number,
    adId: number,
    days: number
  ): Promise<MetaAdInsightTimeseries> => {
    const response = await api.get(
      `/api/meta_ads/ad_accounts/${adAccountId}/ads/${adId}/insights_timeseries/`,
      { params: { days } }
    );
    return response.data;
  },

  getMetaCreativeDetail: async (
    creativeId: number | string,
    days: number
  ): Promise<MetaCreativeDetail> => {
    const response = await api.get(`/api/meta_ads/creatives/${creativeId}/`, {
      params: { days },
    });
    return response.data;
  },

  getMetaCreativePreview: async (
    creativeId: number | string,
    format: MetaAdPreviewFormat = "MOBILE_FEED_STANDARD"
  ): Promise<MetaCreativePreview> => {
    const response = await api.get(
      `/api/meta_ads/creatives/${creativeId}/video_source/`,
      { params: { ad_format: format } }
    );
    return response.data;
  },

  getMetaCreativeTimeseries: async (
    creativeId: number | string,
    days: number
  ): Promise<MetaCreativeTimeseries> => {
    const response = await api.get(
      `/api/meta_ads/creatives/${creativeId}/performance_timeseries/`,
      { params: { days } }
    );
    return response.data;
  },

  getMetaCampaignDetail: async (
    campaignId: number | string,
    days: number
  ): Promise<MetaCampaignDetail> => {
    const response = await api.get(`/api/meta_ads/campaigns/${campaignId}/`, {
      params: { days },
    });
    return response.data;
  },

  getMetaCampaignTimeseries: async (
    campaignId: number | string,
    days: number
  ): Promise<MetaCampaignTimeseries> => {
    const response = await api.get(
      `/api/meta_ads/campaigns/${campaignId}/performance_timeseries/`,
      { params: { days } }
    );
    return response.data;
  },

  getMetaAdSetDetail: async (
    adsetId: number | string,
    days: number
  ): Promise<MetaAdSetDetail> => {
    const response = await api.get(`/api/meta_ads/adsets/${adsetId}/`, {
      params: { days },
    });
    return response.data;
  },

  getMetaAdSetTimeseries: async (
    adsetId: number | string,
    days: number
  ): Promise<MetaAdSetTimeseries> => {
    const response = await api.get(
      `/api/meta_ads/adsets/${adsetId}/performance_timeseries/`,
      { params: { days } }
    );
    return response.data;
  },
};
