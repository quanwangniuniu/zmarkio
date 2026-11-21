import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget } from '@/lib/orgCustomization/optionConfig';

export const CAMPAIGN_OBJECTIVE = createOptionTarget({
  AWARENESS: 'Awareness',
  CONSIDERATION: 'Consideration',
  CONVERSION: 'Conversion',
  RETENTION: 'Retention',
  ENGAGEMENT: 'Engagement',
  TRAFFIC: 'Traffic',
  LEAD_GENERATION: 'Lead Generation',
  APP_PROMOTION: 'App Promotion',
});

export const CAMPAIGN_PLATFORM = createOptionTarget({
  META: 'Meta',
  GOOGLE_ADS: 'Google Ads',
  TIKTOK: 'TikTok',
  LINKEDIN: 'LinkedIn',
  SNAPCHAT: 'Snapchat',
  TWITTER: 'Twitter',
  PINTEREST: 'Pinterest',
  REDDIT: 'Reddit',
  PROGRAMMATIC: 'Programmatic',
  EMAIL: 'Email',
});

export const SNAPSHOT_MILESTONE = createOptionTarget({
  LAUNCH: 'Campaign Launch',
  MID_TEST: 'Mid-Test Review',
  TEST_COMPLETE: 'Test Completion',
  OPTIMIZATION: 'Major Optimization',
  WEEKLY_REVIEW: 'Weekly Review',
  MONTHLY_REVIEW: 'Monthly Review',
  CUSTOM: 'Custom Milestone',
});

export const SNAPSHOT_METRIC = createOptionTarget({
  CPA: 'Cost Per Acquisition',
  ROAS: 'Return on Ad Spend',
  CTR: 'Click-Through Rate',
  CPM: 'Cost Per Mille',
  CPC: 'Cost Per Click',
  CONVERSIONS: 'Conversions',
  REVENUE: 'Revenue',
  IMPRESSIONS: 'Impressions',
  CLICKS: 'Clicks',
  ENGAGEMENT_RATE: 'Engagement Rate',
});

export const CAMPAIGN_STATUS = createOptionTarget({
  PLANNING: 'Planning',
  TESTING: 'Testing',
  SCALING: 'Scaling',
  OPTIMIZING: 'Optimizing',
  PAUSED: 'Paused',
  COMPLETED: 'Completed',
  ARCHIVED: 'Archived',
});

export const CHECKIN_SENTIMENT = createOptionTarget({
  POSITIVE: 'Positive',
  NEUTRAL: 'Neutral',
  NEGATIVE: 'Negative',
});

const campaignConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('campaigns', projectId),
  (config) => applyOptionConfig(config.options, {
    objective: CAMPAIGN_OBJECTIVE,
    platform: CAMPAIGN_PLATFORM,
    snapshot_milestone: SNAPSHOT_MILESTONE,
    snapshot_metric: SNAPSHOT_METRIC,
    status: CAMPAIGN_STATUS,
    checkin_sentiment: CHECKIN_SENTIMENT,
  }),
);

export const loadCampaignConfig = campaignConfigLoader.load;
export const storeCampaignConfig = campaignConfigLoader.store;
