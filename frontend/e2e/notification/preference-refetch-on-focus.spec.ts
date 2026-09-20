import { test, expect } from '@playwright/test';
test('Preference update syncs when one UI changed in another session', async ({ browser }) => {

  // Create two seperate browser contexts to simulate two sessions.
  const contextA = await browser.newContext({ storageState: 'e2e/.auth/user.json' });
  const contextB = await browser.newContext({ storageState: 'e2e/.auth/user.json' });
  
  //Create two pages, one for each context.
  const pageA = await contextA.newPage();
  const pageB = await contextB.newPage();
  
  //Navigate both pages to the notification preferences page.
  await pageA.goto('/notifications/preferences');
  await pageB.goto('/notifications/preferences');

  // Wait for the first page to load and toggle a preference.
  await pageA.getByRole('switch').first().click();
  
  // Wait for the success message to appear and capture the state of the toggle.
  await expect(pageA.getByText('Preferences saved')).toBeVisible();
  const stateA = await pageA.getByRole('switch').first().getAttribute('aria-checked');
  if (stateA === null) {
    throw new Error('The toggle on page A has no aria-checked state to compare against.');
  }

  // Bring the second page to the front and wait for the refetch to occur.
  await pageB.evaluate(() => window.dispatchEvent(new Event('focus')));
  
  // Examine Toggle UI reflects server state within 5s of focus
  await expect(pageB.getByRole('switch').first()).toHaveAttribute('aria-checked', stateA, {timeout: 5000});

  await contextA.close();
  await contextB.close();
});