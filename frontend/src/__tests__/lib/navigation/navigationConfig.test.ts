import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import {
  NAV_CONTENT,
  NAV_MANAGE,
  configureNavItems,
  loadNavigationConfig,
  storeNavigationConfig,
} from '@/lib/navigation/navigationConfig';
import type { OptionConfig, OptionModuleConfig } from '@/types/orgCustomization';

jest.mock('@/lib/api/orgCustomizationApi', () => ({ fetchOptionConfig: jest.fn() }));

const option = (field: string, value: string, overrides: Partial<OptionConfig> = {}): OptionConfig => ({
  field, value, default_label: value, label: null, color: null, enabled: true, order: 0, is_default: false, editable: [], ...overrides,
});

const moduleConfig = (options: OptionConfig[]): OptionModuleConfig => ({ options, can_edit: true });

const manageItems = [
  { key: 'campaigns', label: 'Campaigns' },
  { key: 'meta_ads', label: 'Meta Ads' },
  { key: 'tasks', label: 'Tasks' },
];

const contentItems = [
  { label: 'Variations Studio' },
  { key: 'ads_draft', label: 'Ads Draft' },
  { key: 'email_draft', label: 'Email Draft' },
  { key: 'notion', label: 'Notion' },
];

describe('navigation config', () => {
  afterEach(() => storeNavigationConfig(1, moduleConfig([])));

  it('renames, hides and reorders configured items', async () => {
    (fetchOptionConfig as jest.Mock).mockResolvedValueOnce(moduleConfig([
      option('manage', 'tasks', { label: 'Jobs', order: 0 }),
      option('manage', 'campaigns', { order: 1 }),
      option('manage', 'meta_ads', { enabled: false, order: 2 }),
    ]));

    await loadNavigationConfig(1);

    expect(fetchOptionConfig).toHaveBeenCalledWith('navigation', 1);
    expect(configureNavItems(manageItems, NAV_MANAGE).map((item) => item.label)).toEqual(['Jobs', 'Campaigns']);
  });

  it('keeps fixed items in place', () => {
    storeNavigationConfig(1, moduleConfig([
      option('content', 'notion', { order: 0 }),
      option('content', 'ads_draft', { order: 1 }),
      option('content', 'email_draft', { enabled: false, order: 2 }),
    ]));

    expect(configureNavItems(contentItems, NAV_CONTENT).map((item) => item.label)).toEqual(['Variations Studio', 'Notion', 'Ads Draft']);
  });

  it('leaves the default sidebar untouched when nothing is configured', () => {
    expect(configureNavItems(manageItems, NAV_MANAGE)).toEqual(manageItems);
    expect(configureNavItems(contentItems)).toBe(contentItems);
  });
});
