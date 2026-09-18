import {
  type APIRequestContext,
  type BrowserContext,
  type Page,
  expect,
} from '@playwright/test';

/**
 * Wait for any blocking overlay (Guided Onboarding, "Preparing your workspace")
 * to dismiss before interacting with the spreadsheet pages.
 */
export async function waitForSpreadsheetPageReady(page: Page) {
  await expect(page.getByText('Preparing your workspace')).not.toBeVisible({
    timeout: 15_000,
  });
}

/**
 * Neutralize the OnboardingGate overlay for this page (route stub, no backend
 * mutation). Some seeded users have tenant data but no OrganizationMembership
 * row, so GET /api/core/onboarding-status/ returns needs_onboarding=true and
 * OnboardingGate renders a full-screen z-[9999] overlay that intercepts every
 * click. Creating a real org in the test is NOT safe: it would switch the
 * user's tenant schema away from the seeded data. Call BEFORE page.goto().
 */
export async function stubOnboardingComplete(page: Page) {
  await page.route('**/api/core/onboarding-status/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        needs_onboarding: false,
        has_org: true,
        has_project: true,
      }),
    }),
  );
}

export interface SpreadsheetIds {
  projectId: number;
  spreadsheetId: number;
}

/**
 * Navigate to /spreadsheet, select a project by name, open the spreadsheet
 * by name, and return both projectId and spreadsheetId.
 */
export async function navigateToSpreadsheetAndSelectProject(
  page: Page,
): Promise<SpreadsheetIds> {
  await page.goto('/spreadsheet');

  await expect(
    page.getByRole('heading', { name: 'Spreadsheet' }),
  ).toBeVisible({ timeout: 10_000 });

  const projectName = process.env.E2E_PROJECT_NAME || 'E2E Test Project';
  const spreadsheetName =
    process.env.E2E_SPREADSHEET_NAME || 'E2E Spreadsheet';

  const projectCard = page
    .locator('button')
    .filter({
      has: page.locator('.font-medium.text-gray-900', { hasText: projectName }),
    })
    .first();

  await projectCard.click();

  await page.waitForURL(/\/projects\/[\w-]+\/spreadsheets/, {
    timeout: 100_000,
  });

  await waitForSpreadsheetPageReady(page);

  const projectSlugMatch = page.url().match(/\/projects\/([\w-]+)\/spreadsheets/);
  const projectSlug = projectSlugMatch?.[1];
  if (!projectSlug) throw new Error('Could not parse projectSlug from URL');

  // Fetch the numeric project ID via API using the slug
  const token = await getAuthToken(page);
  const origin = new URL(page.url()).origin;
  const projectResp = await page.request.get(
    `${origin}/api/projects/?search=${encodeURIComponent(projectSlug)}`,
    { headers: token ? { Authorization: `Bearer ${token}` } : {} },
  );
  let projectId = 0;
  if (projectResp.ok()) {
    const projectData = await projectResp.json();
    const projects: Array<{ id: number; slug: string }> = projectData.results ?? projectData ?? [];
    const match = projects.find((p) => p.slug === projectSlug);
    if (match) projectId = match.id;
  }
  if (!projectId) throw new Error('Could not resolve projectId from slug');

  // Open the spreadsheet by name to obtain spreadsheetId
  const row = page
    .locator('tr')
    .filter({
      has: page.locator('.font-medium.text-gray-900', {
        hasText: spreadsheetName,
      }),
    })
    .first();

  await expect(row).toBeVisible({ timeout: 10_000 });
  await row.click();

  await page.waitForURL(
    new RegExp(`\\/projects\\/${projectId}\\/spreadsheets\\/\\d+`),
    { timeout: 15_000 },
  );

  const spreadsheetMatch = page.url().match(/\/spreadsheets\/(\d+)/);
  const spreadsheetId = spreadsheetMatch
    ? parseInt(spreadsheetMatch[1], 10)
    : 0;
  if (!spreadsheetId) throw new Error('Could not parse spreadsheetId from URL');

  return { projectId, spreadsheetId };
}

/** Click a grid cell by its (row, col) data attributes. */
export async function clickCell(page: Page, row: number, col: number) {
  const cell = page.locator(`td[data-row="${row}"][data-col="${col}"]`).first();
  await cell.scrollIntoViewIfNeeded();
  await cell.click();
}

/** Double-click a cell, type a value, and press Enter to commit. */
export async function editCell(
  page: Page,
  row: number,
  col: number,
  value: string,
) {
  const cell = page.locator(`td[data-row="${row}"][data-col="${col}"]`).first();
  await cell.scrollIntoViewIfNeeded();
  await cell.dblclick();
  await page.keyboard.type(value, { delay: 30 });
  await page.keyboard.press('Enter');
}

/** Extract the auth token from localStorage. */
async function getAuthToken(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    try {
      const raw = localStorage.getItem('auth-storage-v1');
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed?.state?.token ?? null;
    } catch {
      return null;
    }
  });
}

/**
 * Create a new sheet inside a spreadsheet via the REST API.
 * Returns the created sheet's `id`.
 */
export async function createSheetByApi(
  page: Page,
  spreadsheetId: number,
  name: string,
): Promise<number> {
  const token = await getAuthToken(page);
  if (!token) throw new Error('No auth token found in localStorage');

  const origin = new URL(page.url()).origin;
  const resp = await page.request.post(
    `${origin}/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/`,
    {
      headers: { Authorization: `Bearer ${token}` },
      data: { name },
    },
  );

  if (!resp.ok()) {
    throw new Error(`Failed to create sheet "${name}": ${resp.status()}`);
  }
  const body = await resp.json();
  return body.id;
}

/**
 * Delete a pattern by name via the UI (Patterns tab).
 * Finds the delete button that is a sibling of the button containing the pattern name.
 */
export async function deletePatternByName(
  page: Page,
  patternName: string,
): Promise<void> {
  const patternsTab = page.getByRole('button', { name: 'Patterns' });
  await patternsTab.click();
  await page.waitForTimeout(300);

  const nameBtn = page
    .getByRole('button')
    .filter({ hasText: patternName })
    .first();
  await expect(nameBtn).toBeVisible({ timeout: 5_000 });

  const deleteBtn = nameBtn
    .locator('xpath=following-sibling::button[@aria-label="Delete pattern"]');
  await deleteBtn.click();

  await expect(nameBtn).not.toBeVisible({ timeout: 5_000 });
}

/**
 * Delete a sheet by name inside a spreadsheet.
 * Lists all sheets, finds the one matching `name`, and deletes it.
 */
export async function deleteSheetByName(
  page: Page,
  projectId: number,
  spreadsheetId: number,
  name: string,
): Promise<void> {
  const token = await getAuthToken(page);
  if (!token) return;

  const origin = new URL(page.url()).origin;
  const headers = { Authorization: `Bearer ${token}` };

  const listResp = await page.request.get(
    `${origin}/api/spreadsheet/spreadsheets/${spreadsheetId}/sheets/`,
    { headers },
  );
  if (!listResp.ok()) return;

  const body = await listResp.json();
  const sheets: Array<{ id: number; name: string }> = body.results ?? body;
  const target = sheets.find((s) => s.name === name);
  if (!target) return;

  await page.request.delete(
    `${origin}/api/projects/${projectId}/spreadsheets/${spreadsheetId}/sheets/${target.id}/`,
    { headers },
  );
}

/**
 * Return the slug of a spreadsheet in the active project, creating one if the
 * project has none. Open it at the flat `/spreadsheets/<slug>` route (numeric
 * ids 404 since the slug migration). Token and active project come from the
 * saved storageState, so no page visit is needed first.
 */
export async function getOrCreateSpreadsheetSlug(
  context: BrowserContext,
  request: APIRequestContext,
  name = 'Spreadsheet E2E',
): Promise<string> {
  const state = await context.storageState();
  const localStorageOf = (key: string): string | undefined => {
    for (const origin of state.origins ?? []) {
      const hit = origin.localStorage?.find((item) => item.name === key);
      if (hit) return hit.value;
    }
    return undefined;
  };
  const parseState = (raw: string | undefined) => {
    try {
      return raw ? JSON.parse(raw)?.state : undefined;
    } catch {
      return undefined;
    }
  };

  const authState = parseState(
    localStorageOf('auth-storage-v1') ?? localStorageOf('auth-storage'),
  );
  const token: string | undefined = authState?.token;
  const organizationToken: string | undefined = authState?.organizationAccessToken;
  expect(token, 'auth token missing from storageState (auth.setup failed?)').toBeTruthy();

  const activeProjectId: number | undefined = parseState(
    localStorageOf('project-storage-v1') ?? localStorageOf('project-storage'),
  )?.activeProject?.id;
  expect(activeProjectId, 'no active project in storageState (auth.setup incomplete?)').toBeTruthy();

  const base = process.env.PLAYWRIGHT_API_BASE_URL?.replace(/\/$/, '') ?? '';
  const listUrl = `${base}/api/spreadsheet/spreadsheets/?project_id=${activeProjectId}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    ...(process.env.PLAYWRIGHT_API_HOST
      ? { Host: process.env.PLAYWRIGHT_API_HOST }
      : {}),
    ...(organizationToken ? { 'X-Organization-Token': organizationToken } : {}),
  };

  const listResp = await request.get(listUrl, { headers });
  expect(listResp.ok(), `spreadsheet list API returned ${listResp.status()}`).toBeTruthy();
  const body = await listResp.json();
  const results: Array<{ slug: string }> = body.results ?? body ?? [];
  if (results.length > 0) return results[0].slug;

  const createResp = await request.post(listUrl, { headers, data: { name } });
  expect(createResp.ok(), `spreadsheet create API returned ${createResp.status()}`).toBeTruthy();
  return (await createResp.json()).slug;
}
