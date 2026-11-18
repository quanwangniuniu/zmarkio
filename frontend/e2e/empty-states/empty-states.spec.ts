/**
 * MED-309: Playwright snapshot coverage for the shared EmptyState component.
 *
 * List APIs are mocked to return empty results, so the specs don't depend on
 * the signed-in E2E user's real data. Only the EmptyState card is captured,
 * so banners, sidebars and loading skeletons elsewhere on the page don't make
 * the snapshots flaky. Covers:
 *  - Tasks
 *  - Campaigns
 *  - Decisions
 *  - Meetings (incoming / completed)
 *  - Messages (no conversation selected)
 */

import { test, expect, type Page } from '@playwright/test';

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

// The EmptyState root is the parent of its title heading.
function emptyState(page: Page, title: string) {
  return page.getByRole('heading', { name: title }).locator('..');
}

test.describe('Empty states', () => {
  test('tasks', async ({ page }) => {
    await mockEmptyList(page, '/api/tasks/');

    await page.goto('/tasks');

    const card = emptyState(page, 'No tasks yet');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toHaveScreenshot('tasks-empty.png');
  });

  test('campaigns', async ({ page }) => {
    await mockEmptyList(page, '/api/campaigns/');

    await page.goto('/campaigns');

    const card = emptyState(page, 'No campaigns yet');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toHaveScreenshot('campaigns-empty.png');
  });

  test('decisions', async ({ page }) => {
    // The decisions list uses token pagination: { items, nextPageToken }.
    await page.route(
      (url) => url.pathname === '/api/decisions/',
      (route) => route.fulfill({ json: { items: [], nextPageToken: null } }),
    );
    // The page treats a failed graph request as "no graph".
    await page.route(
      (url) => url.pathname === '/api/decisions/graph',
      (route) => route.fulfill({ status: 404, json: {} }),
    );

    await page.goto('/decisions');

    const card = emptyState(page, 'No decisions yet');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toHaveScreenshot('decisions-empty.png');
  });

  test('meetings', async ({ page }) => {
    await mockEmptyList(page, /^\/api\/projects\/[^/]+\/meetings\/$/);

    await page.goto('/meetings');

    const incoming = emptyState(page, 'No incoming meetings');
    const completed = emptyState(page, 'No completed meetings');
    await expect(incoming).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(completed).toBeVisible();
    await expect(incoming).toHaveScreenshot('meetings-incoming-empty.png');
    await expect(completed).toHaveScreenshot('meetings-completed-empty.png');
  });

  test('messages', async ({ page }) => {
    await mockEmptyList(page, '/api/chat/chats/');

    await page.goto('/messages');

    const card = emptyState(page, 'Select a conversation');
    await expect(card).toBeVisible({ timeout: FIRST_LOAD_TIMEOUT });
    await expect(card).toHaveScreenshot('messages-empty.png');
  });
});