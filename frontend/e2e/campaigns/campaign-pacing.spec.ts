import { expect, test, type Page } from '@playwright/test';

/**
 * Budget pacing forecaster UI (MED-271).
 *
 * Campaigns are created through the API so each case pins an exact pacing
 * state: a configured campaign with no linked Meta spend resolves to
 * "No spend data", and one missing a budget and end date resolves to
 * "Not configured" with a prompt telling the user what to fill in.
 */

type Created = { slug: string; id: string };

/**
 * Same origin by default — nginx serves the app and the API together. When the
 * UI is run from a separate dev server, point E2E_API_BASE at the stack
 * (e.g. http://localhost) so these setup calls still reach the backend.
 */
const API_BASE = process.env.E2E_API_BASE ?? '';

/** JWT stored in localStorage by the auth layer. */
async function getToken(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('auth-storage-v1');
      return raw ? ((JSON.parse(raw) as any)?.state?.token ?? null) : null;
    } catch {
      return null;
    }
  });
  if (!token) throw new Error('No auth token in localStorage — auth.setup did not run');
  return token;
}

async function getOwnerId(page: Page): Promise<number> {
  const id = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('auth-storage-v1');
      return raw ? ((JSON.parse(raw) as any)?.state?.user?.id ?? null) : null;
    } catch {
      return null;
    }
  });
  if (!id) throw new Error('No user id in localStorage');
  return id as number;
}

async function getProjectId(page: Page): Promise<number> {
  const id = await page.evaluate(() => {
    try {
      const raw = localStorage.getItem('project-storage-v1');
      return raw ? ((JSON.parse(raw) as any)?.state?.activeProject?.id ?? null) : null;
    } catch {
      return null;
    }
  });
  if (!id) throw new Error('No active project in localStorage');
  return id as number;
}

function isoDaysFromToday(offset: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return date.toISOString().slice(0, 10);
}

async function createCampaign(
  page: Page,
  overrides: Record<string, unknown>,
): Promise<Created> {
  const token = await getToken(page);
  const response = await page.request.post(`${API_BASE}/api/campaigns/`, {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: {
      name: `Pacing E2E ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      objective: 'CONVERSION',
      platforms: ['META'],
      start_date: isoDaysFromToday(-30),
      owner_id: await getOwnerId(page),
      project_id: await getProjectId(page),
      ...overrides,
    },
  });
  if (!response.ok()) {
    throw new Error(`Campaign create failed (${response.status()}): ${await response.text()}`);
  }
  const body = await response.json();
  return { slug: body.slug, id: body.id };
}

async function deleteCampaign(page: Page, slug: string) {
  const token = await getToken(page);
  await page.request.delete(`${API_BASE}/api/campaigns/${slug}/`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

test.describe('Campaign budget pacing', () => {
  test.beforeEach(async ({ page }) => {
    // localStorage is origin-scoped, so land on the app before reading it.
    await page.goto('/campaigns', { waitUntil: 'domcontentloaded' });
  });

  test('a configured campaign with no linked spend shows "No spend data"', async ({ page }) => {
    const campaign = await createCampaign(page, {
      end_date: isoDaysFromToday(30),
      budget_estimate: 1000,
    });

    try {
      await page.goto(`/campaigns/${campaign.slug}`, { waitUntil: 'domcontentloaded' });

      const section = page.getByTestId('pacing-section');
      await expect(section).toBeVisible({ timeout: 20_000 });

      const badge = section.getByTestId('pacing-badge');
      await expect(badge).toHaveAttribute('data-pacing-status', 'no_data', { timeout: 20_000 });
      await expect(badge).toHaveText('No spend data');
      await expect(section.getByTestId('pacing-prompt')).toContainText('No Meta spend is linked');
    } finally {
      await deleteCampaign(page, campaign.slug);
    }
  });

  test('a campaign with no budget or end date prompts the user to fill them in', async ({ page }) => {
    const campaign = await createCampaign(page, {});

    try {
      await page.goto(`/campaigns/${campaign.slug}`, { waitUntil: 'domcontentloaded' });

      const section = page.getByTestId('pacing-section');
      await expect(section).toBeVisible({ timeout: 20_000 });

      const badge = section.getByTestId('pacing-badge');
      await expect(badge).toHaveAttribute('data-pacing-status', 'not_configured', {
        timeout: 20_000,
      });
      await expect(badge).toHaveText('Not configured');
      await expect(section.getByTestId('pacing-prompt')).toContainText(
        'Add a budget estimate and an end date',
      );
    } finally {
      await deleteCampaign(page, campaign.slug);
    }
  });

  test('a campaign that has not started yet is reported as not started', async ({ page }) => {
    const campaign = await createCampaign(page, {
      start_date: isoDaysFromToday(7),
      end_date: isoDaysFromToday(37),
      budget_estimate: 500,
    });

    try {
      await page.goto(`/campaigns/${campaign.slug}`, { waitUntil: 'domcontentloaded' });

      const badge = page.getByTestId('pacing-section').getByTestId('pacing-badge');
      await expect(badge).toHaveAttribute('data-pacing-status', 'not_started', {
        timeout: 20_000,
      });
    } finally {
      await deleteCampaign(page, campaign.slug);
    }
  });

  test('recompute refreshes the forecast in place', async ({ page }) => {
    const campaign = await createCampaign(page, {
      end_date: isoDaysFromToday(30),
      budget_estimate: 1000,
    });

    try {
      await page.goto(`/campaigns/${campaign.slug}`, { waitUntil: 'domcontentloaded' });

      const section = page.getByTestId('pacing-section');
      await expect(section.getByTestId('pacing-badge')).toBeVisible({ timeout: 20_000 });

      const recomputeResponse = page.waitForResponse(
        (response) =>
          response.url().includes(`/api/optimization/campaigns/${campaign.slug}/pacing/recompute/`) &&
          response.request().method() === 'POST',
      );

      await section.getByTestId('pacing-recompute').click();

      expect((await recomputeResponse).status()).toBe(200);
      await expect(section.getByTestId('pacing-badge')).toHaveAttribute(
        'data-pacing-status',
        'no_data',
      );
    } finally {
      await deleteCampaign(page, campaign.slug);
    }
  });

  test('the campaign list renders a pacing column', async ({ page }) => {
    const campaign = await createCampaign(page, {
      end_date: isoDaysFromToday(30),
      budget_estimate: 1000,
    });

    try {
      // The list only carries forecasts that already exist. Visiting the detail
      // page computes one lazily, so the row has something to render.
      await page.goto(`/campaigns/${campaign.slug}`, { waitUntil: 'domcontentloaded' });
      await expect(
        page.getByTestId('pacing-section').getByTestId('pacing-badge'),
      ).toBeVisible({ timeout: 20_000 });

      await page.goto('/campaigns', { waitUntil: 'domcontentloaded' });

      await expect(page.getByRole('columnheader', { name: 'Pacing' })).toBeVisible({
        timeout: 20_000,
      });
      await expect(page.getByTestId('pacing-badge').first()).toBeVisible({ timeout: 20_000 });
    } finally {
      await deleteCampaign(page, campaign.slug);
    }
  });
});
