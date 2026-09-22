import { test, expect } from '@playwright/test';
import {
	getAppSidebar,
	ensureSidebarExpandedForNav,
	clickSidebarLinkAndWait,
	expectSidebarLinkActive,
	sidebarLinkByHref,
	waitForAppRendered,
	waitForLayoutMain,
	collapseSidebar,
	expandSidebar,
} from './navigation-helpers';

/**
 * Sidebar + main layout navigation (authenticated user).
 * Style aligned with e2e/profile: describe blocks, URL + content assertions.
 */
test.describe('Sidebar and main layout navigation', () => {
	test.describe.configure({ mode: 'serial', timeout: 90_000 });

	test('logged-in user sees sidebar; current route link is highlighted on Tasks', async ({
		page,
	}) => {
		await page.goto('/tasks');

		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);

		await expect(getAppSidebar(page)).toBeVisible();
		await expect(page).toHaveURL(/\/tasks/);

		await expect(page.locator('main')).toBeVisible();

		await expectSidebarLinkActive(page, '/tasks');
	});

	test('sidebar navigation: Tasks → Calendar → Messages updates URL and main content', async ({
		page,
	}) => {
		await page.goto('/tasks');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);
		await expect(getAppSidebar(page)).toBeVisible();

		// 1) Calendar
		await clickSidebarLinkAndWait(page, '/calendar');
		await expect(page.locator('main')).toBeVisible();
		await expect(page.getByLabel('Calendar view')).toBeVisible({ timeout: 15_000 });
		await expectSidebarLinkActive(page, '/calendar');

		// 2) Messages
		await clickSidebarLinkAndWait(page, '/messages');
		await expect(page.locator('main')).toBeVisible();
		await expect(
			page.getByRole('heading', { name: 'Messages', level: 1 }),
		).toBeVisible({ timeout: 15_000 });
		await expectSidebarLinkActive(page, '/messages');

		// Sidebar still present
		await expect(getAppSidebar(page)).toBeVisible();
	});

	test('sidebar navigation: Messages → Tasks shows Tasks workspace and highlights Tasks', async ({
		page,
	}) => {
		await page.goto('/messages');
		await ensureSidebarExpandedForNav(page);

		await clickSidebarLinkAndWait(page, '/tasks');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);
		await expect(page.locator('main')).toBeVisible();
		await expect(
			page.locator('main').getByRole('heading', { name: 'Select project' }),
		).toBeVisible({ timeout: 20_000 });
		await expectSidebarLinkActive(page, '/tasks');
	});

	test('starting from /calendar highlights Calendar and renders main', async ({ page }) => {
		await page.goto('/calendar');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);

		await expect(getAppSidebar(page)).toBeVisible();
		await expect(page).toHaveURL(/\/calendar/);
		await expect(page.locator('main')).toBeVisible();
		await expectSidebarLinkActive(page, '/calendar');
		await expect(page.getByLabel('Calendar view')).toBeVisible({ timeout: 20_000 });
	});

	test('starting from /messages highlights Messages and renders main', async ({ page }) => {
		await page.goto('/messages');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);

		await expect(getAppSidebar(page)).toBeVisible();
		await expect(page).toHaveURL(/\/messages/);
		await expect(page.locator('main')).toBeVisible();
		await expectSidebarLinkActive(page, '/messages');
		await expect(
			page.getByRole('heading', { name: 'Messages', level: 1 }),
		).toBeVisible({ timeout: 20_000 });
	});

	test('re-clicking current route keeps URL, main, and active state stable (/tasks)', async ({
		page,
	}) => {
		await page.goto('/tasks');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);

		await expect(page.locator('main')).toBeVisible();
		await expectSidebarLinkActive(page, '/tasks');
		await expect(
			page.locator('main').getByRole('heading', { name: 'Select project' }),
		).toBeVisible({ timeout: 20_000 });

		const beforeUrl = page.url();
		const beforePath = new URL(beforeUrl).pathname;

		const tasksLink = sidebarLinkByHref(page, '/tasks');
		await expect(tasksLink).toBeVisible();

		// Clicking the current route (href already matches currentPath) should not break the shell.
		await tasksLink.click();
		// Exercise currentPath === href early-return branch in helper.
		await clickSidebarLinkAndWait(page, '/tasks');

		await expect
			.poll(() => {
				try {
					return new URL(page.url()).pathname;
				} catch {
					return '';
				}
			}, { timeout: 10_000 })
			.toBe(beforePath);

		await expect(page.locator('main')).toBeVisible();
		await expectSidebarLinkActive(page, '/tasks');
		await expect(
			page.locator('main').getByRole('heading', { name: 'Select project' }),
		).toBeVisible({ timeout: 20_000 });
	});

	test('sidebar collapse/expand still allows navigation', async ({ page }) => {
		await page.goto('/tasks');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);

		await collapseSidebar(page);

		// Even when collapsed, navigation links should still work
		await clickSidebarLinkAndWait(page, '/calendar');
		await expect(page.getByLabel('Calendar view')).toBeVisible({ timeout: 20_000 });
		await expectSidebarLinkActive(page, '/calendar');

		// Expand back and navigate again
		await expandSidebar(page);
		await clickSidebarLinkAndWait(page, '/messages');
		await expect(page.getByRole('heading', { name: 'Messages', level: 1 })).toBeVisible({
			timeout: 20_000,
		});
		await expectSidebarLinkActive(page, '/messages');
	});

	
	test('parent menu expands children and child link navigates (Email Draft → Mailchimp)', async ({
		page,
	}) => {
		await page.goto('/tasks');
		await waitForLayoutMain(page);
		await ensureSidebarExpandedForNav(page);

		const sidebar = getAppSidebar(page);

		// Expand the parent menu (it is a button because it has children)
		const emailDraftBtn = sidebar.getByRole('button', { name: 'Email Draft' });
		await expect(emailDraftBtn).toBeVisible({ timeout: 20_000 });
		await emailDraftBtn.click();

		// Child link should appear, then navigation should work
		const mailchimpLink = sidebar.locator('a[href="/mailchimp"]').first();
		await expect(mailchimpLink).toBeVisible({ timeout: 10_000 });
		await clickSidebarLinkAndWait(page, '/mailchimp');
		await expect(page.getByRole('heading', { name: 'All Email Drafts', level: 1 })).toBeVisible({
			timeout: 30_000,
		});
		await expect(getAppSidebar(page)).toBeVisible();
		await expectSidebarLinkActive(page, '/mailchimp');
	});

	test('mobile sidebar still allows navigation (Tasks → Calendar → Messages)', async ({
		page,
	}) => {
		// Preserve small viewport to hit the mobile collapse behaviour.
		await page.setViewportSize({ width: 375, height: 800 });

		await page.goto('/tasks');
		await waitForAppRendered(page);

		// Main should be visible after the sidebar auto-collapses.
		await expect(getAppSidebar(page)).toBeVisible();
		await expect(page.locator('main')).toBeVisible({ timeout: 20_000 });
		await expectSidebarLinkActive(page, '/tasks');

		// 1) Calendar
		await clickSidebarLinkAndWait(page, '/calendar');
		await expect(page.locator('main')).toBeVisible();
		await expect(page.getByLabel('Calendar view')).toBeVisible({ timeout: 20_000 });
		await expectSidebarLinkActive(page, '/calendar');

		// 2) Messages
		await clickSidebarLinkAndWait(page, '/messages');
		await expect(page.locator('main')).toBeVisible();
		await expect(
			page.getByRole('heading', { name: 'Messages', level: 1 }),
		).toBeVisible({ timeout: 20_000 });
		await expectSidebarLinkActive(page, '/messages');
	});
});

test('hard refresh keeps active project and deep link without empty switcher flash', async ({
  page,
}) => {
  test.setTimeout(90_000);

  const deepLink = '/tasks?tab=board';
  const projectName = 'Hydration E2E 50% Project';
  const appOrigin = new URL(
    process.env.BASE_URL ?? 'http://localhost',
  ).origin;

  const activeProject = {
    id: 'hydration-e2e-project',
    slug: 'hydration-e2e-project',
    name: projectName,
    organization: {
      id: 1,
      name: 'E2E Organization',
    },
    total_monthly_budget: null,
    is_active: true,
  };

  await page.setViewportSize({
    width: 1280,
    height: 800,
  });


  await page.addInitScript(() => {
    window.localStorage.removeItem('project-storage');

    type Med257Window = typeof window & {
      __med257Probe?: {
        sawEmptyState: boolean;
        texts: string[];
      };
    };

    const target = window as Med257Window;
    const switcherSelector =
      'button[aria-label="Switch project"]';

    const emptyLabels = [
      'Select project',
      'Select a project',
      'No active project',
    ];

    target.__med257Probe = {
      sawEmptyState: false,
      texts: [],
    };

    const rememberText = (rawText: string | null) => {
      const text = (rawText ?? '')
        .replace(/\s+/g, ' ')
        .trim();

      if (!text || !target.__med257Probe) return;

      if (!target.__med257Probe.texts.includes(text)) {
        target.__med257Probe.texts.push(text);
      }

      if (emptyLabels.some((label) => text.includes(label))) {
        target.__med257Probe.sawEmptyState = true;
      }
    };

    const inspectSwitcher = () => {
      document
        .querySelectorAll(switcherSelector)
        .forEach((switcher) => {
          rememberText(switcher.textContent);
        });
    };

    const observer = new MutationObserver((records) => {
      for (const record of records) {
        if (record.type === 'characterData') {
          const parent = record.target.parentElement;

          if (parent?.closest(switcherSelector)) {
            rememberText(record.oldValue);
          }
        }

        const targetElement =
          record.target instanceof Element
            ? record.target
            : record.target.parentElement;

        if (targetElement?.closest(switcherSelector)) {
          record.removedNodes.forEach((node) => {
            rememberText(node.textContent);
          });
        }
      }

      inspectSwitcher();
    });

    observer.observe(document, {
      childList: true,
      subtree: true,
      characterData: true,
      characterDataOldValue: true,
    });

    inspectSwitcher();
  });

  await page.context().addCookies([
    {
      name: 'active-project',
      value: encodeURIComponent(
        JSON.stringify(activeProject),
      ),
      url: appOrigin,
      sameSite: 'Lax',
    },
  ]);

  const assertResolvedPage = async () => {
    await expect(
      page.getByRole('heading', {
        name: 'Tasks',
        level: 1,
      }),
    ).toBeVisible({
      timeout: 30_000,
    });

    await expect
      .poll(() => {
        const url = new URL(page.url());
        return `${url.pathname}${url.search}`;
      })
      .toBe(deepLink);

    await expect(
      page.getByTestId('tab-board'),
    ).toHaveClass(/bg-gray-100/);

    const switcher = page.getByRole('button', {
      name: 'Switch project',
    });

    await expect(switcher).toBeVisible({
      timeout: 30_000,
    });

    await expect(switcher).toContainText(projectName);

    await expect(
      page
        .locator('main')
        .getByText(projectName, { exact: true }),
    ).toBeVisible();

    const probe = await page.evaluate(() => {
      const target = window as typeof window & {
        __med257Probe?: {
          sawEmptyState: boolean;
          texts: string[];
        };
      };
	  return target.__med257Probe ?? null;
    });

    if (!probe) {
      throw new Error(
        'MED-257 probe was never installed on window — the project switcher did not render.',
      );
    }

    expect(
      probe.sawEmptyState,
      `Switcher showed an empty state. Observed: ${JSON.stringify(
        probe.texts,
      )}`,
    ).toBe(false);
  };

  await page.goto(deepLink, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });

  await assertResolvedPage();

  await page.reload({
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });

  await assertResolvedPage();
});