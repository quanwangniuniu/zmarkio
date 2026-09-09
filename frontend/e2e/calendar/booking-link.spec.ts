import { test, expect, type Page } from '@playwright/test';

/**
 * External booking through a public link.
 *
 * The API is mocked (the `email_draft` specs do the same) because slot
 * availability moves with the clock, which would make assertions flaky.
 * booking-link-real.spec.ts covers the same journey unmocked. The server side — tenant
 * resolution, double-booking, throttling, PII — is covered by
 * backend/calendars/test_public_booking.py; this spec covers what the prospect
 * actually does.
 */

// Public tests start signed out; the member scenario seeds its own session.
test.use({ storageState: { cookies: [], origins: [] } });

const ORG = 'acme';
const LINK = 'intro-call';
const BOOKING_URL = `/book/${ORG}/${LINK}`;
// Literal regex: the widget now sends ?from=&to=, and a plain glob would
// also swallow the /bookings/ POST route.
const API_GLOB = /\/api\/public\/book\/acme\/intro-call\/(\?[^/]*)?$/;
const BOOKINGS_GLOB = `**/api/public/book/${ORG}/${LINK}/bookings/`;

/** Fixed future slots so assertions don't drift with the clock. */
const SLOTS = [
  { start: '2027-03-02T09:00:00Z', end: '2027-03-02T10:00:00Z' },
  { start: '2027-03-02T10:00:00Z', end: '2027-03-02T11:00:00Z' },
  { start: '2027-03-03T09:00:00Z', end: '2027-03-03T10:00:00Z' },
];

const LINK_PAYLOAD = {
  slug: LINK,
  title: 'Intro Call',
  description: 'A quick chat about your campaigns.',
  duration_minutes: 60,
  min_notice_minutes: 0,
  timezone: 'UTC',
  owner_name: 'Ada Lovelace',
  slots: SLOTS,
  invitees_only: false,
  viewer_can_book: true,
  viewer_bookings: [],
};

async function mockAvailability(page: Page, payload: unknown = LINK_PAYLOAD) {
  await page.route(API_GLOB, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(payload),
    });
  });
}

async function gotoBooking(page: Page) {
  await page.goto(BOOKING_URL);
  await expect(page.getByTestId('booking-widget')).toBeVisible({ timeout: 30_000 });
}

test.describe('Public booking link', () => {
  test.describe.configure({ mode: 'serial', timeout: 90_000 });

  test('an external visitor can book a slot end to end', async ({ page }) => {
    await mockAvailability(page);

    let submitted: Record<string, unknown> | null = null;
    await page.route(BOOKINGS_GLOB, async (route) => {
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'confirmed',
          start: SLOTS[0].start,
          end: SLOTS[0].end,
          title: 'Intro Call with Grace Hopper',
          timezone: 'UTC',
          cancel_token: 'signed-token-abc',
          feed_url: 'webcal://app.example/api/public/book/acme/intro-call/signed-token-abc.ics',
        }),
      });
    });

    await gotoBooking(page);

    // The page identifies whose time this is and how long it runs.
    await expect(page.getByText('Intro Call')).toBeVisible();
    await expect(page.getByText('Ada Lovelace')).toBeVisible();
    await expect(page.getByText('60 minutes')).toBeVisible();

    // Show times in a fixed zone so the assertions below are deterministic.
    await page.getByTestId('booking-timezone').selectOption('UTC');
    // Nothing is offered until a date is chosen.
    await expect(page.getByTestId('booking-slot')).toHaveCount(0);
    await expect(page.getByText('Pick a date to see available times.')).toBeVisible();
    await expect(page.getByTestId('booking-date-available')).toHaveCount(2);

    await page.locator('[data-date="2027-03-02"]').click();
    await expect(page.getByTestId('booking-slot')).toHaveCount(2);
    await page.getByTestId('booking-slot').first().click();
    // The chosen time stays visible and a confirm step appears beside it.
    await page.getByTestId('booking-next').click();

    await expect(page.getByTestId('booking-form')).toBeVisible();
    await expect(page.getByTestId('booking-booker-scope')).toHaveCount(0);
    await page.getByTestId('booking-name').fill('Grace Hopper');
    await page.getByTestId('booking-email').fill('grace@example.com');
    await page.getByTestId('booking-phone').fill('+44 7700 900123');
    await page.getByTestId('booking-notes').fill('Looking forward to it.');
    await page.getByTestId('booking-submit').click();

    await expect(page.getByTestId('booking-confirmed')).toBeVisible();
    await expect(page.getByText(/You're booked/)).toBeVisible();
    await expect(page.getByTestId('booking-confirmed-next-step')).toHaveText(
      /cancel or pick another time on this page/i,
    );
    await expect(page.getByTestId('book-another')).toBeVisible();
    await expect(page.getByTestId('booking-back-to-slots')).toBeVisible();

    await expect(page.getByTestId('subscription-url')).toContainText('.ics');

    const download = await Promise.all([
      page.waitForEvent('download'),
      page.getByTestId('download-ics').click(),
    ]).then(([event]) => event);
    expect(download.suggestedFilename()).toBe('intro-call-with-grace-hopper.ics');

    // The guest's only route back to this booking.
    await expect(page.getByTestId('confirmation-cancel-link')).toHaveAttribute(
      'href',
      /\/book\/acme\/intro-call\/cancel\?token=signed-token-abc$/,
    );

    // Optional, but it has to reach the API when given.
    expect(submitted).toMatchObject({ phone: '+44 7700 900123' });

    // The slot's exact instant must reach the API, not a re-derived local time.
    expect(submitted).toMatchObject({
      name: 'Grace Hopper',
      email: 'grace@example.com',
      start: SLOTS[0].start,
    });

    await page.getByTestId('booking-back-to-slots').click();
    await expect(page.getByTestId('booking-existing-reminder')).toContainText(
      'You already have a booking on',
    );
  });

  test('a signed-in member only confirms notes', async ({ page }) => {
    await page.route('**/auth/me/', route => route.fulfill({
      json: { id: 99, email: 'ada@acme.com', username: 'ada', first_name: 'Ada', last_name: 'Lovelace', roles: [] },
    }));
    await page.route('**/auth/me/teams/', route => route.fulfill({ json: { team_ids: [] } }));
    await page.addInitScript(() => {
      const persisted = {
        state: {
          token: 'signed-in-token',
          user: {
            email: 'ada@acme.com',
            username: 'ada',
            first_name: 'Ada',
            last_name: 'Lovelace',
            organization: null,
            current_organization: null,
            roles: [],
          },
          isAuthenticated: true,
        },
        version: 0,
      };
      window.localStorage.setItem('auth-storage-v1', JSON.stringify(persisted));
    });
    await mockAvailability(page);

    let submitted: Record<string, unknown> | null = null;
    await page.route(BOOKINGS_GLOB, async (route) => {
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'confirmed',
          start: SLOTS[0].start,
          end: SLOTS[0].end,
          title: 'Intro Call with Ada Lovelace',
          timezone: 'UTC',
          cancel_token: 'signed-token-ada',
          feed_url: 'webcal://app.example/api/public/book/acme/intro-call/signed-token-ada.ics',
        }),
      });
    });

    await gotoBooking(page);
    await page.getByTestId('booking-timezone').selectOption('UTC');
    await page.locator('[data-date="2027-03-02"]').click();
    await page.getByTestId('booking-slot').first().click();
    await page.getByTestId('booking-next').click();

    await expect(page.getByTestId('booking-form')).toBeVisible();
    await expect(page.getByTestId('booking-name')).toHaveCount(0);
    await expect(page.getByTestId('booking-email')).toHaveCount(0);
    await expect(page.getByTestId('booking-phone')).toHaveCount(0);
    await page.getByTestId('booking-notes').fill('See you then.');
    await page.getByTestId('booking-submit').click();

    await expect(page.getByTestId('booking-confirmed')).toBeVisible();
    await expect(page.getByTestId('booking-confirmed-next-step')).toHaveText(
      /already on your personal calendar/i,
    );
    expect(submitted).toMatchObject({
      name: 'Ada Lovelace',
      email: 'ada@acme.com',
      notes: 'See you then.',
      start: SLOTS[0].start,
    });
  });

  test('times re-render when the visitor changes timezone', async ({ page }) => {
    await mockAvailability(page);
    await gotoBooking(page);

    await page.getByTestId('booking-timezone').selectOption('UTC');
    await page.locator('[data-date="2027-03-02"]').click();
    await expect(page.getByTestId('booking-slot').first()).toHaveText(/9:00/);

    // 09:00Z on 2 March is 20:00 the same day in Sydney — same date, later time.
    await page.getByTestId('booking-timezone').selectOption('Australia/Sydney');
    await expect(page.getByTestId('booking-slot').first()).toHaveText(/8:00/);
  });

  test('picking another date swaps the times shown', async ({ page }) => {
    await mockAvailability(page);
    await gotoBooking(page);
    await page.getByTestId('booking-timezone').selectOption('UTC');

    // 2 March has two slots, 3 March has one.
    await page.locator('[data-date="2027-03-02"]').click();
    await expect(page.getByTestId('booking-slot')).toHaveCount(2);
    await page.locator('[data-date="2027-03-03"]').click();
    await expect(page.getByTestId('booking-slot')).toHaveCount(1);
  });

  test('a slot taken while the page was open is reported, not silently failed',
    async ({ page }) => {
      await mockAvailability(page);
      await page.route(BOOKINGS_GLOB, async (route) => {
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            error: 'SLOT_UNAVAILABLE',
            message: 'That time is no longer available.',
          }),
        });
      });

      await gotoBooking(page);
      await page.locator('[data-date="2027-03-02"]').click();
      await page.getByTestId('booking-slot').first().click();
      await page.getByTestId('booking-next').click();
      await page.getByTestId('booking-name').fill('Grace Hopper');
      await page.getByTestId('booking-email').fill('grace@example.com');
      await page.getByTestId('booking-submit').click();

      // Neutral wording: a 409 means taken *or* lapsed, and the client can't
      // tell which.
      await expect(page.getByTestId('booking-error')).toContainText(
        /no longer available/i,
      );
      // Back to the slot list so another time can be picked.
      await expect(page.getByTestId('booking-slots')).toBeVisible();
    });

  test('an unknown link shows an unavailable message rather than an error page',
    async ({ page }) => {
      await page.route(API_GLOB, async (route) => {
        await route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'This booking link is not available.' }),
        });
      });

      await page.goto(BOOKING_URL);
      await expect(page.getByTestId('booking-missing')).toBeVisible({ timeout: 30_000 });
    });

  test('an invitees-only link looks the same as a missing link', async ({
    page,
  }) => {
    await mockAvailability(page, {
      ...LINK_PAYLOAD,
      invitees_only: true,
      viewer_can_book: false,
      slots: [],
    });
    await page.goto(BOOKING_URL);
    await expect(page.getByTestId('booking-missing')).toBeVisible();
    await expect(page.getByText('Link unavailable')).toBeVisible();
    await expect(page.getByTestId('booking-widget')).toHaveCount(0);
  });

  test('a returning booker is reminded of their existing time', async ({ page }) => {
    await mockAvailability(page, {
      ...LINK_PAYLOAD,
      viewer_bookings: [
        {
          start: SLOTS[0].start,
          end: SLOTS[0].end,
          title: 'Intro Call with Grace Hopper',
        },
      ],
    });
    await gotoBooking(page);
    const reminder = page.getByTestId('booking-existing-reminder');
    await expect(reminder).toBeVisible();
    await expect(reminder).toContainText('You already have a booking on');
    await expect(reminder).toContainText('at');
  });

  test('a link with no free time says so', async ({ page }) => {
    await mockAvailability(page, { ...LINK_PAYLOAD, slots: [] });
    await gotoBooking(page);
    await expect(page.getByText(/No times are available/)).toBeVisible();
    await expect(page.getByTestId('booking-slot')).toHaveCount(0);
  });

  test('the date grid is keyboard navigable', async ({ page }) => {
    // WAI-ARIA date-picker pattern: one tabbable date, arrows move between
    // them. Without it the grid is 30-odd tab stops with no date announced.
    await mockAvailability(page);
    await gotoBooking(page);
    await page.getByTestId('booking-timezone').selectOption('UTC');

    const selected = page.locator('[data-date="2027-03-02"]');
    await selected.click();
    await selected.focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.locator('[data-date="2027-03-03"]')).toBeFocused();

    // Enter picks the focused date and its times replace the previous day's.
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('booking-slot')).toHaveCount(1);
  });

  test('time-of-day bands can be folded', async ({ page }) => {
    await mockAvailability(page);
    await gotoBooking(page);
    await page.getByTestId('booking-timezone').selectOption('UTC');
    await page.locator('[data-date="2027-03-02"]').click();

    // Bands start open — a booking page should not hide availability by default.
    await expect(page.getByTestId('booking-slot')).toHaveCount(2);
    const toggle = page.getByTestId('booking-period-toggle').first();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByTestId('booking-slot')).toHaveCount(0);

    await toggle.click();
    await expect(page.getByTestId('booking-slot')).toHaveCount(2);
  });
});

test.describe('Cancelling a booking', () => {
  const CANCEL_URL = `/book/${ORG}/${LINK}/cancel?token=signed-token-abc`;
  const CANCEL_GLOB = `**/api/public/book/${ORG}/${LINK}/cancel/`;

  test('a guest confirms before anything is cancelled', async ({ page }) => {
    // Mail clients and chat apps prefetch links; cancelling on load would drop
    // meetings nobody meant to drop.
    let called = false;
    await page.route(CANCEL_GLOB, async (route) => {
      called = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'cancelled' }),
      });
    });

    await page.goto(CANCEL_URL);
    await expect(page.getByTestId('booking-cancel')).toBeVisible();
    await expect(page.getByTestId('cancel-keep')).toHaveAttribute(
      'href',
      `/book/${ORG}/${LINK}`,
    );
    expect(called).toBe(false);

    await page.getByTestId('cancel-confirm').click();
    await expect(page.getByText('Booking cancelled')).toBeVisible();
    expect(called).toBe(true);
  });

  test('a stale or spent link says so rather than failing silently', async ({ page }) => {
    await page.route(CANCEL_GLOB, async (route) => {
      await route.fulfill({
        status: 404,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'NOT_FOUND' }),
      });
    });

    await page.goto(CANCEL_URL);
    await page.getByTestId('cancel-confirm').click();
    await expect(page.getByTestId('cancel-error')).toBeVisible();
  });

  test('a link with no code explains itself', async ({ page }) => {
    await page.goto(`/book/${ORG}/${LINK}/cancel`);
    await expect(page.getByTestId('cancel-missing-token')).toBeVisible();
  });

  test('recovery requests never expose another guest booking or cancel token', async ({ page }) => {
    await mockAvailability(page);
    await page.route(`**/api/public/book/${ORG}/${LINK}/lookup/`, async (route) => {
      expect(route.request().postDataJSON()).toEqual({ email: 'grace@example.com' });
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ status: 'accepted' }) });
    });
    await gotoBooking(page);
    await page.getByTestId('booking-find-open').click();
    await page.getByTestId('booking-find-value').fill('grace@example.com');
    await page.getByTestId('booking-find-submit').click();
    await expect(page.getByTestId('booking-find-results')).toContainText('If an upcoming booking matches');
    await expect(page.getByTestId('booking-find-cancel')).toHaveCount(0);
  });
});
