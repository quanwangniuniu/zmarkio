/**
 * Playwright snapshot coverage for the shared EmptyState component.
 *
 * List APIs are mocked to return empty results, so the specs don't depend on
 * the signed-in E2E user's real data. Each EmptyState card is checked with a
 * text-based ARIA snapshot (title, description, CTA); no image baselines.
 * Covers:
 *  - Tasks
 *  - Campaigns
 *  - Decisions
 *  - Meetings (incoming / completed)
 *  - Messages (no conversation selected)
 */

import { test, expect, type Page } from '@playwright/test';
import type { DecisionListResponse, DecisionGraphResponse } from '@/types/decision';
import type { OptionModuleConfig } from '@/types/orgCustomization';

const EMPTY_PAGE = { count: 0, next: null, previous: null, results: [] };

// Pages compile on first visit in dev mode, so allow extra time.
const FIRST_LOAD_TIMEOUT = 15_000;

// Make one list endpoint return an empty paginated response.
async function mockEmptyList(page: Page, pathname: string | RegExp) {
  await page.route(
    (url) =>
      typeof pathname === 'string' ? url.pathname === pathname : pathname.test(url.pathname),
    (route) => route.fulfill({ json: EMPTY_PAGE }),
  );
}

// Find the EmptyState root by its test id, then pick the one with this title.
function emptyState(page: Page, title: string) {
  return page.getByTestId('empty-state').filter({ has: page.getByRole('heading', { name: title }) });
}

// Default option rows, in the shape of backend get_option_config()
// (org_customization/services.py) for a project with no saved settings.
const LIST_ATTRIBUTES = ['label', 'enabled', 'order'];
const LOOKUP_ATTRIBUTES = ['label', 'color'];

function defaultOptions(field: string, editable: string[], choices: [string, string][]) {
  return choices.map(([value, defaultLabel], order) => ({
    field,
    value,
    default_label: defaultLabel,
    label: null,
    color: null,
    enabled: true,
    order,
    is_default: false,
    editable,
  }));
}

const DEFAULT_DECISIONS_CONFIG = {
  options: [
    ...defaultOptions('signal_metric', LIST_ATTRIBUTES, [
      ['ROAS', 'ROAS'], ['CPA', 'CPA'], ['CONVERSION_RATE', 'Conversion Rate'],
      ['REVENUE', 'Revenue'], ['PURCHASES', 'Purchases'], ['CTR', 'CTR'],
      ['CLICKS', 'Clicks'], ['IMPRESSIONS', 'Impressions'], ['CPC', 'CPC'],
      ['CPM', 'CPM'], ['AD_SPEND', 'Ad Spend'], ['AOV', 'AOV'],
    ]),
    ...defaultOptions('signal_movement', LIST_ATTRIBUTES, [
      ['SHARP_INCREASE', 'Sharp Increase'], ['MODERATE_INCREASE', 'Moderate Increase'],
      ['SLIGHT_INCREASE', 'Slight Increase'], ['NO_SIGNIFICANT_CHANGE', 'No Significant Change'],
      ['SLIGHT_DECREASE', 'Slight Decrease'], ['MODERATE_DECREASE', 'Moderate Decrease'],
      ['SHARP_DECREASE', 'Sharp Decrease'], ['VOLATILE', 'Volatile'],
      ['UNEXPECTED_SPIKE', 'Unexpected Spike'], ['UNEXPECTED_DROP', 'Unexpected Drop'],
    ]),
    ...defaultOptions('signal_period', LIST_ATTRIBUTES, [
      ['LAST_24_HOURS', 'Last 24 Hours'], ['LAST_3_DAYS', 'Last 3 Days'],
      ['LAST_7_DAYS', 'Last 7 Days'], ['LAST_14_DAYS', 'Last 14 Days'],
      ['LAST_30_DAYS', 'Last 30 Days'],
    ]),
    ...defaultOptions('review_quality', LOOKUP_ATTRIBUTES, [
      ['GOOD', 'Good'], ['ACCEPTABLE', 'Acceptable'], ['POOR', 'Poor'],
    ]),
    ...defaultOptions('risk_level', LOOKUP_ATTRIBUTES, [
      ['LOW', 'Low'], ['MEDIUM', 'Medium'], ['HIGH', 'High'],
    ]),
  ],
  can_edit: false,
};

test.describe('Empty states', () => {
  test('tasks', async ({ page }) => {
    await mockEmptyList(page, '/api/tasks/');

    await page.goto('/tasks');

    const card = emptyState(page, 'No tasks yet');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toMatchAriaSnapshot(`
      - heading "No tasks yet" [level=3]
      - paragraph: Create your first task to start tracking work in this project.
      - button "Add the first task"
    `);
  });

  test('campaigns', async ({ page }) => {
    await mockEmptyList(page, '/api/campaigns/');

    await page.goto('/campaigns');

    const card = emptyState(page, 'No campaigns yet');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toMatchAriaSnapshot(`
      - heading "No campaigns yet" [level=3]
      - paragraph: Create your first campaign to start planning and tracking performance.
      - button "Create campaign"
    `);
  });

  test('decisions', async ({ page }) => {
    // The decisions list uses token pagination: { items, nextPageToken }.
    await page.route(
      (url) => url.pathname === '/api/decisions/',
      (route) => route.fulfill({ json: { items: [], nextPageToken: null } satisfies DecisionListResponse }),
    );
    // An empty project returns an empty graph, not an error.
    await page.route(
      (url) => url.pathname === '/api/decisions/graph',
      (route) => route.fulfill({ json: { nodes: [], edges: [], topics: [] } satisfies DecisionGraphResponse }),
    ); 
    // A project with no saved settings returns every default option.
    await page.route(
      (url) => url.pathname === '/api/org-customization/decisions-config/',
      (route) => route.fulfill({ json: DEFAULT_DECISIONS_CONFIG satisfies OptionModuleConfig }),
    );
    // Start listening before navigating, so no request is missed.
    const requested = (pathname: string) =>
      page.waitForRequest((req) => new URL(req.url()).pathname === pathname);
    const decisionRequests = Promise.all([
      requested('/api/decisions/'),
      requested('/api/decisions/graph'),
      requested('/api/org-customization/decisions-config/'),
    ]);

    await page.goto('/decisions');
    // All three mocked endpoints must actually be called.
    await decisionRequests;

    const card = emptyState(page, 'No decisions yet');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toMatchAriaSnapshot(`
      - heading "No decisions yet" [level=3]
      - paragraph: Create your first decision to map context, options, signals, and outcomes into a traceable tree.
      - button "Create your first decision"
    `);
    // If the decisions list request fails, the page still shows the empty card but
    // also pops an error toast (react-hot-toast, role="status"). No toast = real empty branch.
    await expect(page.getByRole('status').filter({ hasText: /\S/ })).toHaveCount(0);
  });

  test('meetings', async ({ page }) => {
    await mockEmptyList(page, /^\/api\/projects\/[^/]+\/meetings\/$/);

    await page.goto('/meetings');

    const incoming = emptyState(page, 'No incoming meetings');
    const completed = emptyState(page, 'No completed meetings');
    await expect(incoming).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(completed).toBeVisible();
    await expect(incoming).toMatchAriaSnapshot(`
      - heading "No incoming meetings" [level=3]
      - paragraph: Create your first meeting to start planning.
      - button "Create meeting"
    `);
    await expect(completed).toMatchAriaSnapshot(`
      - heading "No completed meetings" [level=3]
      - paragraph: Meetings with a scheduled day before today appear here.
    `);
  });
  
  test('messages (desktop): CTA is hidden', async ({ page }) => {
    await mockEmptyList(page, '/api/chat/chats/');

    await page.goto('/messages');

    const card = emptyState(page, 'Select a conversation');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toMatchAriaSnapshot(`
      - heading "Select a conversation" [level=3]
      - paragraph: Choose a chat from the list or start a new conversation with your team members.
    `);
    // On desktop the conversation list is always visible, so the CTA is hidden (md:hidden).
    await expect(card.getByRole('button', { name: 'Browse conversations' })).toBeHidden();
  });

  test('messages (mobile): CTA opens the conversation drawer', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await mockEmptyList(page, '/api/chat/chats/');

    await page.goto('/messages');

    const card = emptyState(page, 'Select a conversation');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });

    const cta = card.getByRole('button', { name: 'Browse conversations' });
    await expect(cta).toBeVisible();

    await cta.click();
    await expect(page.getByRole('dialog')).toBeVisible();
  });

});