/**
 *  Meta Ads preview account-switch E2E helpers.
 *
 * Fully mocked (no real Meta / DB seed). “Two accounts” means the status and
 * ad-account list APIs return two synthetic accounts; preview payloads are
 * fulfilled with distinctive ad_name strings so stale content is detectable.
 */

import { type Page } from '@playwright/test';

export const PROJECT = {
  id: 2481,
  name: 'MED-248 Preview Project',
  slug: 'med-248-preview',
  member_count: 1,
  is_active: true,
  organization: {
    id: 248,
    name: 'MED-248 Org',
    slug: 'med-248-org',
  },
};

export const E2E_USER = {
  id: 1,
  email: 'e2e@example.com',
  username: 'e2e-user',
  is_verified: true,
  is_staff: false,
  roles: ['Media Buyer'],
  current_organization: {
    id: PROJECT.organization.id,
    name: PROJECT.organization.name,
    slug: PROJECT.organization.slug,
  },
};

export const ACCOUNT_A = {
  id: 101,
  meta_account_id: 'act-a-101',
  name: 'MED-248 Account A',
  currency: 'USD',
  timezone_name: 'UTC',
  account_status: 1,
  business_id: 'biz-248',
  is_owned: true,
  project_id: PROJECT.id,
  connected_by_current_user: true,
  can_manage: true,
  can_sync: true,
  connector_name: 'e2e-user',
};

export const ACCOUNT_B = {
  id: 102,
  meta_account_id: 'act-b-102',
  name: 'MED-248 Account B',
  currency: 'USD',
  timezone_name: 'UTC',
  account_status: 1,
  business_id: 'biz-248',
  is_owned: true,
  project_id: PROJECT.id,
  connected_by_current_user: true,
  can_manage: true,
  can_sync: true,
  connector_name: 'e2e-user',
};

/** Distinctive strings shown in VideoModal title (from creative.title). */
export const PREVIEW_AD_NAME_A = 'ACCOUNT-A-PREVIEW-MARKER';
export const PREVIEW_AD_NAME_B = 'ACCOUNT-B-PREVIEW-MARKER';

export const CREATIVE_A = {
  id: 1001,
  slug: 'creative-a-preview',
  meta_creative_id: 'shared-meta-id',
  name: 'Creative A',
  // Used as VideoModal title via setPreviewTitle(c.title || …)
  title: PREVIEW_AD_NAME_A,
  body: 'Body A',
  thumbnail_url: 'https://example.test/a.jpg',
  image_url: '',
  video_id: '',
  object_type: 'SHARE',
  call_to_action_type: 'LEARN_MORE',
  spend: '10.00',
  impressions: 1000,
  clicks: 50,
  leads: 2,
  calls: 0,
  purchases: 1,
  messages: 0,
  revenue: '20.00',
  ctr: '5.00',
  cpc: '0.20',
  cpl: '5.00',
  cpa: '10.00',
  roas: '2.00',
  video_p25: 0,
  video_p75: 0,
  video_p100: 0,
  hook_rate: '0',
  hook_rate_strict: '0',
  hold_rate: '0',
  completion_rate: '0',
  video_3sec_count: 0,
  lpv_count: 0,
  cost_per_lpv: '0',
  comment_count: 0,
  cost_per_comment: '0',
  total_events: 3,
  days_with_data: 7,
  is_in_learning: false,
  ad_count: 1,
};

export const CREATIVE_B = {
  ...CREATIVE_A,
  id: 1002,
  slug: 'creative-b-preview',
  name: 'Creative B',
  title: PREVIEW_AD_NAME_B,
  thumbnail_url: 'https://example.test/b.jpg',
  spend: '12.00',
};

const EMPTY_FILTERS = {
  min_impressions: 0,
  min_spend: '0',
  min_events: 0,
  min_days_with_data: 0,
  include_inactive: false,
  include_shared_creatives: false,
};

const EMPTY_AGGREGATES = {
  spend: '0',
  impressions: 0,
  clicks: 0,
  reach: 0,
  leads: 0,
  calls: 0,
  purchases: 0,
  revenue: '0',
  ctr: '0',
  cpc: '0',
  cpm: '0',
  cpl: '0',
  cpcall: '0',
  roas: '0',
};

function json(data: unknown) {
  return {
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(data),
  };
}

function accountIdFromPath(pathname: string): number | null {
  const match = pathname.match(/\/ad_accounts\/(\d+)\//);
  return match ? Number(match[1]) : null;
}

export function metaAdsPagePath(): string {
  return `/${PROJECT.organization.slug}/${PROJECT.slug}/meta-ads?tab=creatives`;
}

/** Prefer Account A on first load. */
export async function seedSelectedAdAccount(page: Page, accountId: number = ACCOUNT_A.id) {
  await page.addInitScript((id) => {
    window.localStorage.setItem('meta-ads:selected-ad-account', String(id));
  }, accountId);
}

export async function mockMetaAdsPreviewApis(page: Page) {
  // Org/project context for /[orgSlug]/[projectSlug]/… layout.
  await page.route('**/api/core/organizations/**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.includes('/switch/')) {
      await route.fulfill(
        json({
          message: 'ok',
          current_organization_id: PROJECT.organization.id,
        })
      );
      return;
    }
    await route.fulfill(
      json({
        id: PROJECT.organization.id,
        name: PROJECT.organization.name,
        slug: PROJECT.organization.slug,
      })
    );
  });

  // Override the empty projects list from mockProjectShellApis (last route wins).
  await page.route('**/api/core/projects**', async (route) => {
    const pathname = new URL(route.request().url()).pathname.replace(/\/+$/, '');
    if (
      pathname === `/api/core/projects/${PROJECT.slug}` ||
      pathname === `/api/core/projects/${PROJECT.id}`
    ) {
      await route.fulfill(json(PROJECT));
      return;
    }
    if (pathname === '/api/core/projects') {
      await route.fulfill(json([PROJECT]));
      return;
    }
    await route.fulfill(json(PROJECT));
  });

  await page.route('**/api/facebook_integration/status/**', async (route) => {
    await route.fulfill(
      json({
        connected: true,
        fb_user_name: 'E2E Meta User',
        fb_email: 'e2e-meta@example.com',
        business_id: 'biz-248',
        business_name: 'MED-248 Business',
        token_expires_at: null,
        last_synced_at: '2026-09-20T00:00:00Z',
        ad_accounts: [ACCOUNT_A, ACCOUNT_B],
      })
    );
  });

  await page.route('**/api/facebook_integration/ad_accounts/**', async (route) => {
    const url = new URL(route.request().url());
    // link_project and detail paths fall through if needed
    if (url.pathname.includes('/link_project/')) {
      await route.fulfill(json(ACCOUNT_A));
      return;
    }
    await route.fulfill(
      json({
        count: 2,
        page: 1,
        page_size: 5,
        results: [ACCOUNT_A, ACCOUNT_B],
      })
    );
  });

  await page.route('**/api/meta_ads/summary/**', async (route) => {
    const accountId = Number(new URL(route.request().url()).searchParams.get('ad_account'));
    await route.fulfill(
      json({
        ad_account_id: accountId || ACCOUNT_A.id,
        currency: 'USD',
        days: 28,
        window: { since: '2026-08-21', until: '2026-09-17' },
        aggregates: EMPTY_AGGREGATES,
        timeseries: [],
      })
    );
  });

  await page.route('**/api/meta_ads/ad_accounts/*/campaign_performance/**', async (route) => {
    const accountId = accountIdFromPath(new URL(route.request().url()).pathname) ?? ACCOUNT_A.id;
    await route.fulfill(
      json({
        ad_account_id: accountId,
        currency: 'USD',
        days: 28,
        window: { since: '2026-08-21', until: '2026-09-17' },
        campaigns: [],
      })
    );
  });

  await page.route('**/api/meta_ads/ad_accounts/*/sync_runs/**', async (route) => {
    await route.fulfill(json([]));
  });

  await page.route('**/api/meta_ads/ad_accounts/*/creative_performance/**', async (route) => {
    const accountId = accountIdFromPath(new URL(route.request().url()).pathname);
    const creatives =
      accountId === ACCOUNT_B.id ? [CREATIVE_B] : [CREATIVE_A];
    await route.fulfill(
      json({
        ad_account_id: accountId ?? ACCOUNT_A.id,
        currency: 'USD',
        days: 28,
        window: { since: '2026-08-21', until: '2026-09-17' },
        filters: EMPTY_FILTERS,
        creatives,
      })
    );
  });

  await page.route('**/api/meta_ads/creatives/*/video_source/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const creativeMatch = pathname.match(/\/creatives\/([^/]+)\/video_source/);
    const creativeKey = creativeMatch?.[1] ?? '';
    const isB =
      creativeKey === String(CREATIVE_B.id) || creativeKey === CREATIVE_B.slug;
    await route.fulfill(
      json({
        creative_id: isB ? CREATIVE_B.id : CREATIVE_A.id,
        video_id: '',
        meta_ad_id: isB ? 'ad-b' : 'ad-a',
        ad_name: isB ? PREVIEW_AD_NAME_B : PREVIEW_AD_NAME_A,
        ad_format: 'MOBILE_FEED_STANDARD',
        iframe_src: isB
          ? 'https://example.test/iframe-account-b'
          : 'https://example.test/iframe-account-a',
        iframe_html: `<iframe src="${
          isB
            ? 'https://example.test/iframe-account-b'
            : 'https://example.test/iframe-account-a'
        }"></iframe>`,
        thumbnail_url: isB ? CREATIVE_B.thumbnail_url : CREATIVE_A.thumbnail_url,
        permalink_url: '',
      })
    );
  });
}
