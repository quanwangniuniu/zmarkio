import { test, expect } from './tiktok-fixtures';

test('generates a real public snapshot with the selected draft content and loaded image', async ({ page, browser, account, ads }) => {
  const draft = await ads.create();
  await page.goto(`${account.projectPath}/tiktok`);
  await page.getByRole('button').filter({ has: page.getByText(draft.name, { exact: true }) }).click();
  await expect(page.getByLabel('Ad name', { exact: true })).toHaveValue(draft.name);
  await page.getByRole('button', { name: 'Share preview', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share TikTok draft', exact: true });
  const generation = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/tiktok/ad-drafts/${draft.id}/share/` && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Generate link', exact: true }).click();
  const generated = await generation;
  expect(generated.status()).toBe(201);
  const { data: { slug } } = await generated.json();
  expect(slug).toBeTruthy();
  const link = new URL(`/tiktok/preview/${slug}`, page.url()).href;
  await expect(dialog.getByRole('textbox')).toHaveValue(link);
  await expect(dialog.getByRole('button', { name: 'Copy link', exact: true })).toBeEnabled();

  const visitor = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  try {
    const publicPage = await visitor.newPage();
    const loading = publicPage.waitForResponse((response) =>
      new URL(response.url()).pathname === `/api/tiktok/public-previews/${slug}/`,
    );
    await publicPage.goto(link);
    const loaded = await loading;
    expect(loaded.status()).toBe(200);
    expect(loaded.request().headers().authorization).toBeUndefined();
    await expect(publicPage.getByRole('heading', { name: draft.name, exact: true })).toBeVisible();
    await expect(publicPage.getByText('Discover our summer collection.', { exact: true })).toBeVisible();
    await expect(publicPage.getByRole('button', { name: 'Shop now', exact: true })).toBeVisible();
    const image = publicPage.getByRole('img', { name: 'preview', exact: true });
    await expect(image).toHaveAttribute('src', draft.imageUrl);
    await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
    await expect(publicPage.getByLabel('Ad name', { exact: true })).toBeHidden();
  } finally {
    await visitor.close();
  }
});
