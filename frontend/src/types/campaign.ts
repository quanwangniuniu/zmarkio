import type { Id } from './common';

// Campaign enums
export type CampaignStatus = 'PLANNING' | 'TESTING' | 'SCALING' | 'OPTIMIZING' | 'PAUSED' | 'COMPLETED' | 'ARCHIVED';
export type CampaignObjective = 'AWARENESS' | 'CONSIDERATION' | 'CONVERSION' | 'RETENTION' | 'ENGAGEMENT' | 'TRAFFIC' | 'LEAD_GENERATION' | 'APP_PROMOTION';
export type CampaignPlatform = 'META' | 'GOOGLE_ADS' | 'TIKTOK' | 'LINKEDIN' | 'SNAPCHAT' | 'TWITTER' | 'PINTEREST' | 'REDDIT' | 'PROGRAMMATIC' | 'EMAIL';

// User and Project summaries (reuse from task types if available)
export interface UserSummary {
  id: number;
  username: string;
  email: string;
  first_name?: string;
  last_name?: string;
}

export interface ProjectSummary {
  id: number;
  slug?: string;
  name: string;
}

// Campaign data types
export interface CampaignPlatformIntegration {
  id: number;
  account_name: string;
  connector_name: string;
  can_reconnect: boolean;
  last_sync_error: '' | 'auth' | 'transient' | 'unknown';
  last_sync_attempted_at: string | null;
  last_synced_at: string | null;
}

export interface CampaignData {
  id: string;
  slug: string;
  name: string;
  objective: CampaignObjective;
  platforms: CampaignPlatform[];
  hypothesis?: string;
  tags?: string[];
  start_date: string;
  end_date?: string;
  actual_completion_date?: string;
  owner: UserSummary;
  owner_id?: number;
  creator?: UserSummary;
  assignee?: UserSummary;
  assignee_id?: number;
  project: ProjectSummary;
  project_id?: Id;
  budget_estimate?: number;
  status: CampaignStatus;
  status_note?: string;
  latest_performance_summary?: Record<string, any>;
  platform_integrations?: CampaignPlatformIntegration[];
  created_at: string;
  updated_at: string;
  is_deleted?: boolean;
}

export interface CreateCampaignData {
  name: string;
  objective: CampaignObjective;
  platforms: CampaignPlatform[];
  start_date: string;
  end_date?: string;
  owner_id: number; // User ID (integer)
  project_id: Id; // Project ID (integer or slug)
  hypothesis?: string;
  tags?: string[];
  budget_estimate?: number;
}

export interface UpdateCampaignData {
  name?: string;
  objective?: CampaignObjective;
  platforms?: CampaignPlatform[];
  start_date?: string;
  end_date?: string;
  hypothesis?: string;
  tags?: string[];
  budget_estimate?: number;
  status_note?: string;
  assignee_id?: number;
  owner_id?: number;
}

// Campaign Task Link types
export interface CampaignTaskLink {
  id: string;
  campaign: string; // Campaign UUID
  task: number; // Task ID
  task_slug?: string; // Task slug (API lookups are slug-only)
  link_type?: string;
  created_at: string;
  updated_at: string;
}

// Campaign Activity Timeline types
export interface CampaignActivityTimelineItem {
  type: 'status_change' | 'check_in' | 'performance_snapshot';
  id: string;
  timestamp: string; // ISO datetime
  user: UserSummary | null;
  details: {
    // Status change
    from_status?: string;
    from_status_display?: string;
    to_status?: string;
    to_status_display?: string;
    note?: string;
    // Check-in
    sentiment?: string;
    sentiment_display?: string;
    // Performance snapshot
    milestone_type?: string;
    milestone_type_display?: string;
    spend?: string;
    metric_type?: string;
    metric_type_display?: string;
    metric_value?: string;
    percentage_change?: string;
    notes?: string;
    screenshot_url?: string;
    additional_metrics?: Record<string, any>;
  };
}

// Campaign Status History types
export interface CampaignStatusHistoryItem {
  id: string;
  campaign: string;
  from_status: CampaignStatus;
  from_status_display: string;
  to_status: CampaignStatus;
  to_status_display: string;
  changed_by: UserSummary | null;
  note: string | null;
  created_at: string;
}

// Campaign Check-in types
export type CheckInSentiment = 'POSITIVE' | 'NEUTRAL' | 'NEGATIVE';

export interface CampaignCheckIn {
  id: string;
  campaign: string;
  sentiment: CheckInSentiment;
  sentiment_display: string;
  note: string | null;
  checked_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}

export interface CreateCheckInData {
  sentiment: CheckInSentiment;
  note?: string;
}

export interface UpdateCheckInData {
  sentiment?: CheckInSentiment;
  note?: string;
}

// Campaign Performance Snapshot types
export type MilestoneType = 'LAUNCH' | 'MID_TEST' | 'TEST_COMPLETE' | 'OPTIMIZATION' | 'WEEKLY_REVIEW' | 'MONTHLY_REVIEW' | 'CUSTOM';

export type MetricType = 'CPA' | 'ROAS' | 'CTR' | 'CPM' | 'CPC' | 'CONVERSIONS' | 'REVENUE' | 'IMPRESSIONS' | 'CLICKS' | 'ENGAGEMENT_RATE';

export interface PerformanceSnapshot {
  id: string;
  campaign: string;
  milestone_type: MilestoneType;
  milestone_type_display: string;
  spend: string; // Decimal as string
  metric_type: MetricType;
  metric_type_display: string;
  metric_value: string; // Decimal as string
  percentage_change: string | null; // Decimal as string or null
  notes: string | null;
  screenshot: string | null; // File path
  screenshot_url: string | null; // Full URL
  additional_metrics: Record<string, any>;
  snapshot_by: UserSummary | null;
  created_at: string;
  updated_at: string;
}

export interface CreateSnapshotData {
  milestone_type: MilestoneType;
  spend: number;
  metric_type: MetricType;
  metric_value: number;
  percentage_change?: number;
  notes?: string;
  screenshot?: File;
  additional_metrics?: Record<string, any>;
}

export interface UpdateSnapshotData {
  milestone_type?: MilestoneType;
  spend?: number;
  metric_type?: MetricType;
  metric_value?: number;
  percentage_change?: number;
  notes?: string;
  screenshot?: File;
  additional_metrics?: Record<string, any>;
}

// Campaign Template types
export type TemplateSharingScope = 'PERSONAL' | 'TEAM' | 'ORGANIZATION';

export interface CampaignTemplate {
  id: string;
  slug: string;
  name: string;
  description?: string;
  creator: UserSummary;
  version_number: number;
  sharing_scope: TemplateSharingScope;
  sharing_scope_display: string;
  project?: ProjectSummary;
  project_id?: Id;
  objective?: CampaignObjective;
  platforms?: CampaignPlatform[];
  hypothesis_framework?: string;
  tag_suggestions?: string[];
  task_checklist?: any[];
  review_schedule_pattern?: Record<string, any>;
  decision_point_triggers?: any[];
  recommended_variation_count?: number;
  variation_templates?: any[];
  usage_count: number;
  is_archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateTemplateData {
  name: string;
  description?: string;
  sharing_scope?: TemplateSharingScope;
  project_id?: Id;
}

export interface UpdateTemplateData {
  name?: string;
  description?: string;
  sharing_scope?: TemplateSharingScope;
  project_id?: Id;
  objective?: CampaignObjective;
  platforms?: CampaignPlatform[];
  hypothesis_framework?: string;
  tag_suggestions?: string[];
}

export interface CreateCampaignFromTemplateData {
  name: string;
  project: number;
  owner: number;
  start_date?: string;
  end_date?: string;
  assignee?: number;
  objective?: CampaignObjective;
  platforms?: CampaignPlatform[];
  hypothesis?: string;
  tags?: string[];
  budget_estimate?: number;
}

export interface SaveCampaignAsTemplateData {
  name: string;
  description?: string;
  sharing_scope?: TemplateSharingScope;
  project_id?: number;
}

