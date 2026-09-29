import { expect, test } from '@playwright/test';

const user = {
  id: 99,
  username: 'privacy.user',
  email: 'privacy@example.com',
  first_name: 'Privacy',
  last_name: 'User',
  roles: ['Media Buyer'],
  organization: { id: 7, name: 'Outlook' },
};

test('confirms account erasure and submits one asynchronous request', async ({ page }) => {
  test.setTimeout(90_000);
  await page.addInitScript((mockUser) => {
    if (sessionStorage.getItem('erasure-test-seeded')) return;
    sessionStorage.setItem('erasure-test-seeded', '1');
    localStorage.setItem('auth-storage-v1', JSON.stringify({
      state: {
        token: 'test-token',
        refreshToken: 'test-refresh-token',
        organizationAccessToken: null,
        user: mockUser,
        isAuthenticated: true,
      },
      version: 0,
    }));
    localStorage.setItem('project-storage', JSON.stringify({
      state: { activeProject: { id: 101, name: 'Project', organization: { name: 'Outlook' } } },
      version: 0,
    }));
  }, user);
  await page.route('**/auth/me/', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(user),
  }));
  await page.route('**/api/teams/my-teams/', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ team_ids: [] }),
  }));

  let submissions = 0;
  await page.route('**/auth/me/delete/', async route => {
    submissions += 1;
    expect(route.request().method()).toBe('DELETE');
    expect(route.request().postDataJSON()).toMatchObject({ confirm: 'DELETE MY ACCOUNT' });
    await route.fulfill({
      status: 202, contentType: 'application/json',
      body: JSON.stringify({ message: 'Account erasure request submitted.' }),
    });
  });

  await page.goto('/profile', { waitUntil: 'domcontentloaded' });
  await page.getByRole('button', { name: 'Delete Account' }).click();
  await expect(page.getByText('Collaborative work will remain')).toBeVisible();
  await expect(page.getByText('Audit, approval, consent and billing records are retained')).toBeVisible();
  const submit = page.getByRole('button', { name: 'Submit Erasure Request' });
  await expect(submit).toBeDisabled();
  await page.getByPlaceholder('DELETE MY ACCOUNT').fill('DELETE MY ACCOUNT');
  await submit.click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 30_000 });
  await expect(page.getByRole('heading', { name: 'Sign In' })).toBeVisible({ timeout: 30_000 });
  expect(submissions).toBe(1);
  const authState = await page.evaluate(() => localStorage.getItem('auth-storage-v1'));
  if (authState) {
    expect(JSON.parse(authState).state.token).toBeNull();
    expect(JSON.parse(authState).state.isAuthenticated).toBe(false);
  }
});
