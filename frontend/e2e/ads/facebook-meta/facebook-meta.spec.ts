import { test, expect } from './facebook-meta-fixtures';

test('shows the real empty state for a user with no creatives', async ({ page, account }) => {
  const response = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/facebook_meta/adcreatives/' && response.request().method() === 'GET',
  );
  await page.goto(account.facebookPath);
  const list = await response;
  expect(list.status()).toBe(200);
  expect((await list.json()).count).toBe(0);
  await expect(page.getByText('No ad creatives yet', { exact: true })).toBeVisible();
  await expect(page.getByText('above to create your first one.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New Ad Creative', exact: true })).toBeVisible();
  await expect(page.getByRole('table')).toBeHidden();
});

test('lists a real creative and opens its content and image preview', async ({ page, account, ads }) => {
  const creative = await ads.create();
  await ads.attachPhoto(creative.id);
  await page.goto(account.facebookPath);
  await expect(page.getByRole('heading', { name: 'Facebook Meta ad creatives', exact: true })).toBeVisible();
  const table = page.getByRole('table');
  for (const name of ['Name', 'Status', 'Call to action', 'ID']) {
    await expect(table.getByRole('columnheader', { name, exact: true })).toBeVisible();
  }
  const row = table.getByRole('row').filter({ hasText: creative.name });
  await expect(row.getByRole('cell', { name: creative.name, exact: true })).toBeVisible();
  await expect(row.getByRole('cell', { name: 'Active', exact: true })).toBeVisible();
  await expect(row.getByRole('cell', { name: creative.id, exact: true })).toBeVisible();
  await row.click();

  // The list uses the legacy ID route; creation uses the slug (covered by the share test).
  await expect(page).toHaveURL(`${account.facebookPath}/${creative.id}`);
  await expect(page.getByRole('heading', { name: creative.name, exact: true })).toBeVisible();
  const content = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Creative content', exact: true }) });
  await expect(content.getByText('Discover our summer collection.', { exact: true })).toBeVisible();
  await expect(content.getByText('Summer starts here', { exact: true })).toBeVisible();
  await expect(content.getByRole('link')).toHaveAttribute('href', 'https://example.com/summer');
  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview', exact: true }) });
  const image = preview.getByRole('img', { name: 'Summer collection preview', exact: true }).first();
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
});
