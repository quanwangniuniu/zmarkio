import { test, expect } from './google-ads-fixtures';

test('shows the real empty state for a user with no Google Ads', async ({ page, account }) => {
  const response = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/google_ads/ads/' && response.request().method() === 'GET',
  );
  await page.goto(`${account.projectPath}/google-ads`);
  const list = await response;
  expect(list.status()).toBe(200);
  expect((await list.json()).count).toBe(0);
  await expect(page.getByText('No Google Ads yet', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'New Ad', exact: true })).toBeVisible();
  await expect(page.getByRole('table')).toBeHidden();
});

test('lists an owned search ad and opens its saved content and preview', async ({ page, account, ads }) => {
  const ad = await ads.create();
  const path = `${account.projectPath}/google-ads`;
  await page.goto(path);
  await expect(page.getByRole('heading', { name: 'Google Ads', exact: true })).toBeVisible();
  const table = page.getByRole('table');
  const row = table.getByRole('row').filter({ hasText: ad.name });
  await expect(row.getByRole('cell', { name: ad.name, exact: true })).toBeVisible();
  for (const name of ['Name', 'Status', 'Type', 'Created', 'ID']) {
    await expect(table.getByRole('columnheader', { name, exact: true })).toBeVisible();
  }
  await expect(row.getByRole('cell', { name: 'Draft', exact: true })).toBeVisible();
  await expect(row.getByRole('cell', { name: 'Responsive Search', exact: true })).toBeVisible();
  await expect(row.getByRole('cell', { name: String(ad.id), exact: true })).toBeVisible();

  // Populate through the actual update contract before opening the saved detail.
  const saved = await account.api.patch(`/api/google_ads/ads/${ad.id}/`, { data: {
    responsive_search_ad_data: {
      headline_texts: ['Summer starts here', 'Explore our collection', 'Find your summer style'],
      description_texts: ['Discover our summer collection.', 'Shop fresh styles for sunny days.'],
      path1: 'summer', path2: 'collection',
    },
  } });
  expect(saved.status(), 'Save search ad content').toBe(200);
  await row.getByRole('cell', { name: ad.name, exact: true }).click();
  await expect(page).toHaveURL(`${path}/${ad.slug}`);
  await expect(page.getByRole('heading', { name: ad.name, exact: true })).toBeVisible();
  const content = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Ad content', exact: true }) });
  await expect(content.getByPlaceholder('Headline 1', { exact: true })).toHaveValue('Summer starts here');
  await expect(content.getByPlaceholder('Description 1', { exact: true })).toHaveValue('Discover our summer collection.');
  await expect(content.getByPlaceholder('e.g., products', { exact: true })).toHaveValue('summer');
  await expect(content.getByPlaceholder('e.g., sale', { exact: true })).toHaveValue('collection');
  const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview', exact: true }) });
  await expect(preview.getByText('Summer starts here', { exact: false })).toBeVisible();
  await expect(preview.getByText('Discover our summer collection.', { exact: false })).toBeVisible();
  await preview.getByRole('button', { name: 'Preview ads', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Preview ads', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Summer starts here', { exact: false })).toBeVisible();
  await expect(dialog.getByText('Discover our summer collection.', { exact: false })).toBeVisible();
  await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(dialog).toBeHidden();
});

test('generates a header share link that opens a public search ad preview', async ({ page, browser, account, ads }) => {
  const ad = await ads.create();
  const saved = await account.api.patch(`/api/google_ads/ads/${ad.id}/`, { data: {
    responsive_search_ad_data: {
      headline_texts: ['Share the summer', 'Explore our collection', 'Find your summer style'],
      description_texts: ['A summer collection worth sharing.', 'Shop fresh styles for sunny days.'],
    },
  } });
  expect(saved.status()).toBe(200);
  await page.goto(`${account.projectPath}/google-ads/${ad.slug}`);
  await expect(page.getByRole('heading', { name: ad.name, exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Share preview', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share Google Ad preview', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Copy link', exact: true })).toBeDisabled();
  const generated = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/google_ads/${ad.id}/create_preview/` && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Generate link', exact: true }).click();
  const response = await generated;
  expect(response.status()).toBe(201);
  const preview = await response.json();
  expect(preview.ad_id).toBe(ad.id);
  await expect(dialog.getByRole('button', { name: 'Copy link', exact: true })).toBeEnabled();
  await expect(dialog.getByRole('button', { name: 'Generate link', exact: true })).toBeDisabled();
  const link = await dialog.getByRole('textbox').inputValue();
  expect(new URL(link).origin).toBe(new URL(page.url()).origin);

  const guest = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  try {
    const publicPage = await guest.newPage();
    const loaded = publicPage.waitForResponse((response) =>
      new URL(response.url()).pathname === `/api/google_ads/preview/${preview.token}/`,
    );
    await publicPage.goto(link);
    const publicResponse = await loaded;
    expect(publicResponse.status()).toBe(200);
    expect((await publicResponse.json()).ad.id).toBe(ad.id);
    const publicPreview = publicPage.getByRole('dialog', { name: 'Preview ads', exact: true });
    await expect(publicPreview).toBeVisible();
    await expect(publicPreview.getByText('Share the summer', { exact: false })).toBeVisible();
    await expect(publicPreview.getByText('A summer collection worth sharing.', { exact: false })).toBeVisible();
    await expect(publicPage.getByRole('button', { name: 'Edit name', exact: true })).toBeHidden();
    await expect(publicPage.getByRole('button', { name: 'Publish', exact: true })).toBeHidden();
  } finally {
    await guest.close();
  }
});
