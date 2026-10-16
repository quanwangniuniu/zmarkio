import { expect, test } from '@playwright/test';
import {
  getActiveProjectSlug,
  waitForWorkspaceReady,
} from '../tasks/tasks-helpers';

test('compares Meta, Google and TikTok with a selectable canonical KPI', async ({ page }) => {
  await page.route('**/api/report/cross-platform/**', async (route) => {
    await route.fulfill({
      json: {
        status: 'ready',
        start_date: '2026-09-10',
        end_date: '2026-10-09',
        rows: [
          { platform: 'meta', status: 'ready', cpm: '12', cpc: '0.5', ctr: '2.4', roas: '3' },
          { platform: 'google', status: 'ready', cpm: '10', cpc: '0.4', ctr: '2.8', roas: '4' },
          { platform: 'tiktok', status: 'ready', cpm: '8', cpc: '0.3', ctr: '3.1', roas: '2.5' },
        ],
      },
    });
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await waitForWorkspaceReady(page);
  const projectSlug = await getActiveProjectSlug(page);
  const context = await page.evaluate(() => {
    let organization: { id: number; name: string; slug: string } | null = null;
    for (const key of ['auth-storage-v1', 'auth-storage']) {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const candidate = JSON.parse(raw)?.state?.user?.current_organization;
      if (candidate?.id && candidate?.name && candidate?.slug) {
        organization = candidate;
        break;
      }
    }
    let project = null;
    for (const key of ['project-storage-v1', 'project-storage']) {
      const raw = localStorage.getItem(key);
      if (!raw) continue;
      const candidate = JSON.parse(raw)?.state?.activeProject;
      if (candidate?.id && candidate?.name && candidate?.slug) {
        project = candidate;
        break;
      }
    }
    return { organization, project };
  });
  if (!context.organization || !context.project) {
    throw new Error('No active organization/project found after storage hydration');
  }

  const { organization, project } = context;
  await page.route(`**/api/core/organizations/${organization.slug}/`, (route) =>
    route.fulfill({ json: organization })
  );
  await page.route(`**/api/core/projects/${projectSlug}/`, (route) =>
    route.fulfill({ json: project })
  );
  await page.goto(
    `/${encodeURIComponent(organization.slug)}/${encodeURIComponent(projectSlug)}/overview`,
    { waitUntil: 'domcontentloaded' }
  );

  const widget = page.getByTestId('cross-platform-performance');
  await expect(widget).toBeVisible({ timeout: 30_000 });
  await expect(widget.getByTestId('platform-meta')).toContainText('Meta');
  await expect(widget.getByTestId('platform-google')).toContainText('Google');
  await expect(widget.getByTestId('platform-tiktok')).toContainText('TikTok');

  await widget.getByRole('button', { name: 'ROAS' }).click();
  await expect(widget.getByRole('button', { name: 'ROAS' })).toHaveAttribute(
    'aria-pressed',
    'true'
  );
  await expect(widget.getByTestId('platform-google')).toContainText('4');
});
