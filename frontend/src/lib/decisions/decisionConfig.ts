import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget } from '@/lib/orgCustomization/optionConfig';

export const SIGNAL_METRIC = createOptionTarget({
  ROAS: 'ROAS',
  CPA: 'CPA',
  CONVERSION_RATE: 'Conversion rate',
  REVENUE: 'Revenue',
  PURCHASES: 'Purchases',
  CTR: 'CTR',
  CLICKS: 'Clicks',
  IMPRESSIONS: 'Impressions',
  CPC: 'CPC',
  CPM: 'CPM',
  AD_SPEND: 'Ad spend',
  AOV: 'AOV',
});

export const SIGNAL_MOVEMENT = createOptionTarget({
  SHARP_INCREASE: 'Sharp increase',
  MODERATE_INCREASE: 'Moderate increase',
  SLIGHT_INCREASE: 'Slight increase',
  NO_SIGNIFICANT_CHANGE: 'No significant change',
  SLIGHT_DECREASE: 'Slight decrease',
  MODERATE_DECREASE: 'Moderate decrease',
  SHARP_DECREASE: 'Sharp decrease',
  VOLATILE: 'Volatile',
  UNEXPECTED_SPIKE: 'Unexpected spike',
  UNEXPECTED_DROP: 'Unexpected drop',
});

export const SIGNAL_PERIOD = createOptionTarget({
  LAST_24_HOURS: 'Last 24 hours',
  LAST_3_DAYS: 'Last 3 days',
  LAST_7_DAYS: 'Last 7 days',
  LAST_14_DAYS: 'Last 14 days',
  LAST_30_DAYS: 'Last 30 days',
});

export const REVIEW_QUALITY = createOptionTarget({
  GOOD: 'Good',
  ACCEPTABLE: 'Acceptable',
  POOR: 'Poor',
});

export const DECISION_RISK = createOptionTarget({
  LOW: 'Low',
  MEDIUM: 'Medium',
  HIGH: 'High',
});

const decisionConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('decisions', projectId),
  (config) => applyOptionConfig(config.options, {
    signal_metric: SIGNAL_METRIC,
    signal_movement: SIGNAL_MOVEMENT,
    signal_period: SIGNAL_PERIOD,
    review_quality: REVIEW_QUALITY,
    risk_level: DECISION_RISK,
  }),
);

export const loadDecisionConfig = decisionConfigLoader.load;
export const storeDecisionConfig = decisionConfigLoader.store;
