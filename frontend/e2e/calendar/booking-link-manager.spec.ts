import { test, expect, type Page } from '@playwright/test';
import fs from 'fs';
import path from 'path';

/**
 * Owner-side booking link management.
 *
 * The API is mocked so the spec doesn't depend on the signed-in E2E user
 * already having links or calendars. Ownership scoping and validation are
 * covered server-side in backend/calendars/test_booking_link_crud.py.
 */

const PAGE_URL = '/calendar/booking-links';
const AUTH_FILE = path.resolve(__dirname, '../.auth/user.json');


/**
 * The org/project slugs the signed-in user actually has.
 *
 * The nested layout validates these against the API (and will switch orgs if
 * they diverge), so placeholder slugs land on an error state instead of the
 * page. auth.setup persists the active project, which is the same source the
 * app reads.
 */
function activeSlugs(): { orgSlug: string; projectSlug: string } {
  const state = JSON.parse(fs.readFileSync(AUTH_FILE, 'utf-8'));
  for (const origin of state.origins ?? []) {
    for (const item of origin.localStorage ?? []) {
      if (item.name !== 'project-storage-v1') continue;
      const project = JSON.parse(item.value)?.state?.activeProject;
      const orgSlug = project?.organization?.slug;
      const projectSlug = project?.slug ?? project?.id;
      if (orgSlug && projectSlug) {
        return { orgSlug: String(orgSlug), projectSlug: String(projectSlug) };
      }
    }
  }
  throw new Error('No active project in storage state.');
}

const LINKS_GLOB = '**/api/booking-links/';
const CALENDARS_GLOB = /\/api\/calendars\/(\?.*)?$/;

const EXISTING_LINK = {
  id: '11111111-1111-1111-1111-111111111111',
  slug: 'intro-call',
  organization_slug: 'acme',
  title: 'Intro Call',
  description: 'A quick chat.',
  duration_minutes: 30,
  slot_increment_minutes: 15,
  buffer_before_minutes: 0,
  buffer_after_minutes: 0,
  min_notice_minutes: 60,
  max_advance_days: 60,
  timezone: 'UTC',
  availability_windows: [],
  is_active: true,
  scope: 'team',
  calendar: '22222222-2222-2222-2222-222222222222',
  host: { id: 1, name: 'Ada Lovelace' },
  invitees: [],
  invitee_emails: [],
  invitees_only: false,
  created_by_name: '',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

async function mockCalendars(page: Page) {
  await page.route(CALENDARS_GLOB, async (route) => {
    if (route.request().method() !== 'GET') {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        count: 2,
        results: [
          {
            id: '22222222-2222-2222-2222-222222222222',
            name: 'Primary',
            timezone: 'UTC',
            is_primary: true,
            project_id: 7,
          },
          // Non-primary: must not be offered, since the API would reject it.
          {
            id: '33333333-3333-3333-3333-333333333333',
            name: 'Secondary',
            timezone: 'UTC',
            is_primary: false,
          },
        ],
      }),
    });
  });
}

async function mockLinks(page: Page, links: unknown[]) {
  await page.route(LINKS_GLOB, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(links),
      });
      return;
    }
    await route.continue();
  });
}

async function gotoManager(page: Page) {
  await page.goto(PAGE_URL);
  await expect(page.getByTestId('booking-link-manager')).toBeVisible({ timeout: 30_000 });
}

test.describe('Booking link management', () => {
  test.describe.configure({ mode: 'serial', timeout: 90_000 });

  test('shows an empty state before any link exists', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, []);
    await gotoManager(page);

    await expect(page.getByTestId('booking-link-empty')).toBeVisible();
    await expect(page.getByTestId('booking-link-item')).toHaveCount(0);
  });

  test('is reachable on the nested org/project route the sidebar builds', async ({
    page,
  }) => {
    // useBuildUrl prefixes every sidebar href with /[orgSlug]/[projectSlug], so
    // the flat route alone is not reachable from navigation — that combination
    // 404'd until the nested alias existed. The page reads org context from the
    // auth store rather than the path, so the slugs here are only routing.
    await mockCalendars(page);
    await mockLinks(page, [EXISTING_LINK]);

    const { orgSlug, projectSlug } = activeSlugs();
    await page.goto(`/${orgSlug}/${projectSlug}/calendar/booking-links`);
    await expect(page.getByTestId('booking-link-manager')).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByTestId('booking-link-item')).toHaveCount(1);
  });

  test('lists existing links with their public path', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, [EXISTING_LINK]);
    await gotoManager(page);

    await expect(page.getByTestId('booking-link-item')).toHaveCount(1);
    await expect(page.getByText('Intro Call')).toBeVisible();
    // The path uses the org the API reports for the link, not the active
    // project's. Path and duration are now separate elements on the card.
    await expect(page.getByText('/book/acme/intro-call')).toBeVisible();
    await expect(page.getByText('30 min')).toBeVisible();
    await expect(page.getByTestId('booking-link-created')).toContainText('2026');
    await expect(page.getByTestId('booking-link-open-link')).toHaveText(
      'Anyone with the link',
    );
  });

  test('shows named invitees and can add one from the card', async ({ page }) => {
    await mockCalendars(page);
    const withGuest = {
      ...EXISTING_LINK,
      invitees: [
        { id: 2, name: 'Grace Hopper', email: 'grace@example.com' },
      ],
    };
    await mockLinks(page, [withGuest]);
    await page.route('**/api/booking-links/**', async (route) => {
      if (route.request().method() !== 'PATCH') {
        await route.fallback();
        return;
      }
      const body = route.request().postDataJSON() as {
        invitee_emails?: string[];
      };
      const email = body.invitee_emails?.at(-1) ?? 'guest@example.com';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ...withGuest,
          invitees: [
            ...withGuest.invitees,
            { id: null, name: email, email },
          ],
          invitee_emails: [email],
        }),
      });
    });

    await gotoManager(page);
    await expect(page.getByTestId('booking-link-invitee-chip')).toContainText(
      'Grace Hopper',
    );
    await page.getByTestId('booking-link-quick-invite').click();
    await page
      .getByTestId('booking-link-quick-invite-input')
      .fill('guest@example.com');
    await page.getByTestId('booking-link-quick-invite-email').click();
    await expect(page.getByTestId('booking-link-invitee-chip')).toHaveCount(2);
  });

  test('an owner can generate a new link', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, []);

    let created: Record<string, unknown> | null = null;
    await page.route(LINKS_GLOB, async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      created = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ ...EXISTING_LINK, title: 'Discovery Call', slug: 'discovery-call' }),
      });
    });

    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();

    await expect(page.getByTestId('booking-link-form')).toBeVisible();
    await expect(page.getByTestId('booking-link-scope-personal')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByTestId('booking-link-scope-caption')).toContainText(
      'personal calendar',
    );
    await expect(
      page.getByTestId('booking-link-calendar').locator('option'),
    ).toHaveCount(2);
    await page.getByTestId('booking-link-title').fill('Discovery Call');
    await page.getByTestId('booking-link-calendar').selectOption({ index: 1 });
    await page.getByTestId('booking-link-duration-45').click();
    await page.getByTestId('booking-link-save').click();

    await expect(page.getByTestId('booking-link-item')).toHaveCount(1);
    expect(created).toMatchObject({
      title: 'Discovery Call',
      slug: 'discovery-call',
      duration_minutes: 45,
    });
  });

  test('a duplicate slug surfaces the server message', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, []);
    await page.route(LINKS_GLOB, async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      // The calendars app wraps validation errors in its own envelope.
      await route.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'VALIDATION_ERROR',
          message: 'VALIDATION_ERROR',
          details: [
            { field: 'title', message: 'You already have a booking link with this title.' },
          ],
        }),
      });
    });

    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();
    await page.getByTestId('booking-link-title').fill('Intro Call');
    await page.getByTestId('booking-link-calendar').selectOption({ index: 1 });
    await page.getByTestId('booking-link-save').click();

    await expect(page.getByTestId('booking-link-error')).toContainText(
      /already have a booking link/i,
    );
  });

  test('creating a team link uses the project calendar', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, []);

    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();

    await expect(page.getByTestId('booking-link-scope-personal')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await page.getByTestId('booking-link-scope-team').click();
    await expect(page.getByTestId('booking-link-scope-team')).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByTestId('booking-link-calendar')).toHaveValue(
      '22222222-2222-2222-2222-222222222222',
    );
    await expect(page.getByRole('heading', { name: 'Same project' })).toHaveCount(0);
    await expect(page.getByTestId('booking-link-save')).toBeEnabled();
  });

  test('the empty state is hidden while the create form is open', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, []);
    await gotoManager(page);

    await expect(page.getByTestId('booking-link-empty')).toBeVisible();
    await page.getByTestId('booking-link-new').click();
    // "No booking links yet" underneath the form is contradictory.
    await expect(page.getByTestId('booking-link-empty')).toBeHidden();
  });

  test('advanced scheduling rules stay collapsed until asked for', async ({ page }) => {
    // Buffers, notice and horizon are defaults most people never touch;
    // putting them on the create step made every user answer extra questions
    // to make one link.
    await mockCalendars(page);
    await mockLinks(page, []);
    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();

    await expect(page.getByTestId('booking-link-advanced')).toBeHidden();
    await expect(page.getByTestId('booking-link-min_notice_minutes')).toBeHidden();

    await page.getByTestId('booking-link-advanced-toggle').click();
    await expect(page.getByTestId('booking-link-advanced')).toBeVisible();
    await expect(page.getByTestId('booking-link-min_notice_minutes')).toBeVisible();
  });

  test('a custom duration is still reachable', async ({ page }) => {
    await mockCalendars(page);
    await mockLinks(page, []);
    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();

    // Presets cover the common cases; the number input appears only on demand.
    await expect(page.getByTestId('booking-link-duration_minutes')).toBeHidden();
    await page.getByTestId('booking-link-duration-custom').click();
    await expect(page.getByTestId('booking-link-duration_minutes')).toBeVisible();
    await page.getByTestId('booking-link-duration_minutes').fill('25');
    await expect(page.getByTestId('booking-link-duration_minutes')).toHaveValue('25');
  });

  test('CSM clients on the project are selectable alongside colleagues', async ({
    page,
  }) => {
    // Customers are usually not project members, so a picker that searched
    // members alone left them unreachable even though the API accepts them.
    // One has an account and joins as a participant; one does not and can only
    // ever be an address.
    await mockCalendars(page);
    await mockLinks(page, []);
    await page.route(/\/api\/core\/projects\/\d+\/members\/$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      });
    });
    await page.route(/\/api\/customers\/(\?.*)?$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          count: 2,
          results: [
            {
              id: 1,
              user_id: 77,
              email: 'signed.up@client.com',
              full_name: 'Signed Up Client',
              phone: '',
              is_active: true,
            },
            {
              id: 2,
              user_id: null,
              email: 'no.account@client.com',
              full_name: 'No Account Client',
              phone: '+44 123',
              is_active: true,
            },
          ],
        }),
      });
    });

    let submitted: Record<string, unknown> | null = null;
    await page.route(LINKS_GLOB, async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(EXISTING_LINK),
      });
    });

    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();
    await page.getByTestId('booking-link-title').fill('Client Call');
    await page.getByTestId('booking-link-scope-team').click();
    await page.getByTestId('booking-link-calendar').selectOption(
      '22222222-2222-2222-2222-222222222222',
    );

    await page.getByTestId('booking-link-invitee').fill('Client');
    await expect(page.getByTestId('booking-link-invitee-option-customer')).toHaveCount(2);

    // The one with an account.
    await page.getByText('Signed Up Client').click();
    await page.getByTestId('booking-link-invitee').fill('No Account');
    await page.getByText('No Account Client').click();
    await expect(page.getByTestId('booking-link-invitee-chip')).toHaveCount(2);

    await page.getByTestId('booking-link-save').click();
    // The account goes in as a participant, the contact record as an address.
    await expect.poll(() => submitted?.invitee_ids).toEqual([77]);
    await expect.poll(() => submitted?.invitee_emails).toEqual(['no.account@client.com']);
  });

  test('several guests can be added, by name or by address', async ({ page }) => {
    // Two ways out of one box, as in a share dialog: a colleague we know, or an
    // address for someone we don't.
    await mockCalendars(page);
    await mockLinks(page, []);
    await page.route(/\/api\/core\/projects\/\d+\/members\/$/, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: 1,
            user: { id: 42, name: 'Grace Hopper', username: 'grace' },
            project: { id: 7, name: 'Shared' },
            role: 'member',
            is_active: true,
          },
        ]),
      });
    });

    let submitted: Record<string, unknown> | null = null;
    await page.route(LINKS_GLOB, async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      submitted = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(EXISTING_LINK),
      });
    });

    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();
    await page.getByTestId('booking-link-title').fill('Intro Call');
    await page.getByTestId('booking-link-scope-team').click();
    await page.getByTestId('booking-link-calendar').selectOption(
      '22222222-2222-2222-2222-222222222222',
    );

    // A colleague, found by typing part of their name.
    await page.getByTestId('booking-link-invitee').fill('grace');
    await page.getByTestId('booking-link-invitee-results').getByText('Grace Hopper').click();
    await expect(page.getByTestId('booking-link-invitee-chip')).toHaveCount(1);

    // A second guest, this time an address, alongside the first.
    await page.getByTestId('booking-link-invitee').fill('stranger@example.com');
    await page.getByTestId('booking-link-invitee-email').click();
    await expect(page.getByTestId('booking-link-invitee-chip')).toHaveCount(2);

    await page.getByTestId('booking-link-save').click();
    await expect.poll(() => submitted?.invitee_ids).toEqual([42]);
    await expect.poll(() => submitted?.invitee_emails).toEqual(['stranger@example.com']);
  });

  test('the calendar page carries the entry point for booking links', async ({
    page,
  }) => {
    // Booking links have no sidebar entry of their own; the way in is a button
    // on the calendar toolbar, beside Ask Agent.
    await page.goto('/calendar');
    const entry = page.getByTestId('calendar-create-booking-link');
    await expect(entry).toBeVisible({ timeout: 30_000 });
    await expect(entry).toHaveText(/Create Booking link/);
    await expect(entry).toHaveAttribute('href', /\/calendar\/booking-links$/);
  });

  test('offers only calendars scoped to the active project', async ({ page }) => {
    // The calendar page filters its own view by project. If this picker were
    // unscoped it could offer a calendar whose events that page never shows —
    // the booking would succeed and then appear nowhere.
    const requested: string[] = [];
    await page.route(CALENDARS_GLOB, async (route) => {
      requested.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 0, results: [] }),
      });
    });
    await mockLinks(page, []);

    await gotoManager(page);
    // The project store hydrates after first render, so an unscoped request can
    // go out first; what matters is that the list finally rendered is scoped.
    expect(requested.length).toBeGreaterThan(0);
    expect(requested[requested.length - 1]).toContain('project_id=');
  });

  test('creates a personal calendar when none exist', async ({
    page,
  }) => {
    const createdCalendar = {
      id: '44444444-4444-4444-4444-444444444444',
      name: 'My Calendar',
      timezone: 'UTC',
      is_primary: true,
    };
    await page.route(CALENDARS_GLOB, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(createdCalendar),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ count: 0, results: [] }),
      });
    });
    await mockLinks(page, []);

    await gotoManager(page);
    await page.getByTestId('booking-link-new').click();

    await expect(page.getByTestId('booking-link-calendar-created')).toHaveText(
      'My Calendar was created automatically.',
    );
    await expect(page.getByTestId('booking-link-calendar')).toHaveValue(
      createdCalendar.id,
    );
    await expect(page.getByTestId('booking-link-save')).toBeEnabled();
  });

  test('an owner can edit an existing link', async ({ page }) => {
    // Every field was set-once until now: changing a duration or notice period
    // meant deleting the link and reissuing the URL.
    await mockCalendars(page);
    await mockLinks(page, [EXISTING_LINK]);

    let patched: Record<string, unknown> | null = null;
    await page.route(/\/api\/booking-links\/[0-9a-f-]+\/$/, async (route) => {
      if (route.request().method() !== 'PATCH') {
        await route.fallback();
        return;
      }
      patched = route.request().postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...EXISTING_LINK, title: 'Renamed Call', duration_minutes: 45 }),
      });
    });

    await gotoManager(page);
    await page.getByTestId('booking-link-edit').click();

    // The form opens prefilled, in edit mode.
    await expect(page.getByTestId('booking-link-form')).toBeVisible();
    await expect(page.getByText('Edit booking link')).toBeVisible();
    await expect(page.getByTestId('booking-link-title')).toHaveValue('Intro Call');

    await page.getByTestId('booking-link-title').fill('Renamed Call');
    await page.getByTestId('booking-link-duration-45').click();
    await page.getByTestId('booking-link-save').click();

    await expect(page.getByText('Renamed Call')).toBeVisible();
    expect(patched).toMatchObject({ title: 'Renamed Call', duration_minutes: 45 });
    // Editing preserves the existing calendar selection.
    expect(patched).toHaveProperty('calendar_id', EXISTING_LINK.calendar);
  });
});
