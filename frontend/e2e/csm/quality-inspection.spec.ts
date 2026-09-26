import { test, expect, Page } from '@playwright/test';

/**
 * Conversation quality inspection (MED-223), AC1 to AC5.
 *
 * Every API call is mocked, so the spec exercises the UI contract without
 * needing seeded conversations or a supervisor account in the database.
 */

const CONVERSATION = {
  id: 412,
  customer: 9,
  customer_name: 'Ada Lovelace',
  customer_email: 'ada@example.com',
  queue: 3,
  queue_name: 'T1 Frontline',
  queue_organisation_id: 14,
  assigned_to: 77,
  assigned_to_name: 'Grace H.',
  assigned_to_user_id: 41,
  status: 'closed',
  status_display: 'Closed',
  channel: 'email',
  channel_display: 'Email',
  tags: ['Refund not received', 'vip'],
  started_at: '2026-03-04T09:00:00Z',
  ended_at: '2026-03-04T09:42:00Z',
  elapsed_seconds: 2520,
  created_at: '2026-03-04T09:00:00Z',
  ticket: null,
  message_count: 2,
  review_count: 0,
  latest_rating: null,
  my_review: null,
};

const FILTER_OPTIONS = {
  organisations: [{ id: 14, name: 'Acme' }],
  queues: [{ id: 3, name: 'T1 Frontline', organisation: 14, is_active: true }],
  agents: [{ user_id: 41, name: 'Grace H.', email: 'grace@example.com' }],
  channels: [
    { value: 'web', label: 'Web' },
    { value: 'email', label: 'Email' },
  ],
  statuses: [
    { value: 'closed', label: 'Closed' },
    { value: 'active', label: 'Active' },
  ],
  tags: ['vip'],
  customers: [{ id: 9, name: 'Ada Lovelace', email: 'ada@example.com' }],
};

const REPORT = {
  filters_echo: { bucket: 'day', date_basis: 'review' },
  totals: {
    reviews: 1,
    conversations_reviewed: 1,
    conversations_in_scope: 4,
    coverage_pct: 25,
  },
  by_rating: [
    { rating: 'good', rating_display: 'Good', count: 0, pct: 0 },
    { rating: 'needs_improvement', rating_display: 'Needs Improvement', count: 0, pct: 0 },
    { rating: 'poor', rating_display: 'Poor', count: 1, pct: 100 },
  ],
  by_agent: [
    {
      agent_user_id: 41,
      agent_name: 'Grace H.',
      total: 1,
      good: 0,
      needs_improvement: 0,
      poor: 1,
    },
  ],
  by_date: [
    { bucket: '2026-03-04', total: 1, good: 0, needs_improvement: 0, poor: 1 },
  ],
  generated_at: '2026-03-04T12:00:00Z',
};

/** State the mocked backend keeps between requests within one test. */
type MockState = { review: Record<string, unknown> | null; lastListUrl: string };

async function mockQualityApi(page: Page, state: MockState) {
  await page.route('**/auth/me/', (route) =>
    route.fulfill({
      json: {
        id: 1,
        email: 'sup@example.com',
        username: 'sup',
        first_name: 'Sup',
        last_name: 'Ervisor',
        is_staff: false,
        is_org_admin: false,
        is_csm_admin: false,
        is_csm_supervisor: true,
        organization: null,
        current_organization: null,
        roles: [],
      },
    }),
  );

  await page.route('**/api/csm/quality/filter-options/', (route) =>
    route.fulfill({ json: FILTER_OPTIONS }),
  );

  await page.route('**/api/csm/quality/conversations/?*', (route) => {
    state.lastListUrl = route.request().url();
    const url = new URL(state.lastListUrl);
    // Honour the channel filter so the test can assert filtering really applies.
    const channels = url.searchParams.getAll('channel');
    const matches = channels.length === 0 || channels.includes(CONVERSATION.channel);
    const results = matches ? [{ ...CONVERSATION, my_review: state.review, review_count: state.review ? 1 : 0 }] : [];
    route.fulfill({ json: { count: results.length, results } });
  });

  await page.route('**/api/csm/quality/conversations/412/', (route) =>
    route.fulfill({
      json: {
        ...CONVERSATION,
        my_review: state.review,
        reviews: state.review ? [state.review] : [],
        messages: [
          {
            id: 1,
            sender_type: 'customer',
            content: 'Where is my refund?',
            created_at: '2026-03-04T09:00:00Z',
          },
          {
            id: 2,
            sender_type: 'agent',
            content: 'Let me check that for you.',
            created_at: '2026-03-04T09:05:00Z',
          },
        ],
        customer_profile: null,
        linked_tickets: [],
      },
    }),
  );

  await page.route('**/api/csm/quality/conversations/412/review/', async (route) => {
    const body = route.request().postDataJSON() as { rating: string; comment: string };
    state.review = {
      id: 88,
      conversation: 412,
      rating: body.rating,
      rating_display: body.rating === 'poor' ? 'Poor' : 'Good',
      comment: body.comment,
      reviewer: 1,
      reviewer_name: 'Sup Ervisor',
      reviewed_at: '2026-03-04T12:00:00Z',
      agent_user: 41,
      agent_name: 'Grace H.',
      queue: 3,
      organisation: 14,
      created: true,
    };
    await route.fulfill({ status: 201, json: state.review });
  });

  await page.route('**/api/csm/quality/report/?*', (route) => route.fulfill({ json: REPORT }));
  await page.route('**/api/csm/quality/report/', (route) => route.fulfill({ json: REPORT }));

  await page.route('**/api/csm/quality/report/export.csv/*', (route) =>
    route.fulfill({
      status: 200,
      headers: {
        'content-type': 'text/csv; charset=utf-8',
        'content-disposition': 'attachment; filename="quality-inspection-all_all-20260304-120000.csv"',
      },
      body: [
        'Section,Key,Label,Total,Good,Needs Improvement,Poor',
        'rating,poor,Poor,1,,,1',
        'agent,41,Grace H.,1,0,0,1',
        'total,,All,1,0,0,1',
      ].join('\n'),
    }),
  );
}

test.describe('Conversation quality inspection', () => {
  let state: MockState;

  test.beforeEach(async ({ page }) => {
    state = { review: null, lastListUrl: '' };
    await mockQualityApi(page, state);
  });

  test('a supervisor can browse, filter, annotate, report and export', async ({ page }) => {
    // AC1 — the area loads for a supervisor and lists a closed conversation.
    await page.goto('/csm/quality');
    await expect(page.getByRole('heading', { name: 'Conversation quality' })).toBeVisible();
    await expect(page.getByText('Refund not received')).toBeVisible();
    await expect(page.getByText('Closed')).toBeVisible();

    // AC2 — a filter narrows the list and lands in the URL.
    await page.getByRole('button', { name: 'Channel' }).click();
    await page.getByRole('listbox', { name: 'Channel' }).getByLabel('Web').click();
    await expect(page).toHaveURL(/channel=web/);
    await expect(page.getByText(/no conversations match these filters/i)).toBeVisible();

    await page.getByRole('button', { name: /clear all/i }).click();
    await expect(page.getByText('Refund not received')).toBeVisible();

    // AC3 — annotate with a rating and a comment; it comes back on the row.
    await page.getByRole('button', { name: 'Refund not received' }).click();
    const drawer = page.getByRole('dialog', { name: 'Review conversation' });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText('Where is my refund?')).toBeVisible();

    await drawer.getByRole('radio', { name: 'Poor' }).click();
    await drawer.getByLabel('Comment').fill('Missed the refund policy.');
    await drawer.getByRole('button', { name: /save annotation/i }).click();

    await expect(drawer.getByText(/last reviewed by sup ervisor/i)).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();
    await expect(page.getByText('Poor')).toBeVisible();

    // AC4 — the report aggregates those annotations.
    await page.getByRole('button', { name: 'Report' }).click();
    await expect(page).toHaveURL(/tab=report/);
    await expect(page.getByText('100% of annotations')).toBeVisible();
    await expect(page.getByText('1 of 4 conversations reviewed')).toBeVisible();
    await expect(
      page.getByRole('table', { name: /annotation counts per agent/i }),
    ).toContainText('Grace H.');

    // AC5 — the report downloads as CSV.
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /export csv/i }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain('quality-inspection-');
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });

  test('filters are carried in the URL across a reload', async ({ page }) => {
    await page.goto('/csm/quality?channel=email&tab=report');

    await expect(page).toHaveURL(/tab=report/);
    await expect(page.getByText('1 of 4 conversations reviewed')).toBeVisible();

    // The date-basis toggle only exists on the report tab.
    await expect(page.getByLabel('Date range applies to')).toBeVisible();
  });
});
