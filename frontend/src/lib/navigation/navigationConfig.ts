import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget, type OptionTarget } from '@/lib/orgCustomization/optionConfig';

export const NAV_OVERVIEW = createOptionTarget({ overview: 'Overview' });

export const NAV_MANAGE = createOptionTarget({
  campaigns: 'Campaigns',
  meta_ads: 'Meta Ads',
  tasks: 'Tasks',
  decisions: 'Decisions',
  budget_pools: 'Budget Pools',
  spreadsheets: 'Spreadsheets',
});

export const NAV_CONTENT = createOptionTarget({ ads_draft: 'Ads Draft', email_draft: 'Email Draft', notion: 'Notion' });

export const NAV_COLLABORATE = createOptionTarget({
  meetings: 'Meetings',
  calendar: 'Calendar',
  messages: 'Messages',
  miro: 'Miro',
});

export const NAV_TOOLS = createOptionTarget({ workflows: 'Workflows', timeline: 'Timeline' });

const navigationConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('navigation', projectId),
  (config) => applyOptionConfig(config.options, {
    overview: NAV_OVERVIEW,
    manage: NAV_MANAGE,
    content: NAV_CONTENT,
    collaborate: NAV_COLLABORATE,
    tools: NAV_TOOLS,
  }),
);

export const configureNavItems = <T extends { key?: string; label: string }>(items: T[], target?: OptionTarget): T[] => {
  if (!target) return items;
  const ordered = target.values.flatMap((key) => items.filter((item) => item.key === key));
  let slot = 0;
  return items.flatMap((item) => {
    if (!item.key) return [item];
    const next = ordered[slot++];
    return next ? [{ ...next, label: target.labels[next.key!] }] : [];
  });
};

export const loadNavigationConfig = navigationConfigLoader.load;
export const storeNavigationConfig = navigationConfigLoader.store;
