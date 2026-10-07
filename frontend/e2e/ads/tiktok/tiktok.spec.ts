import { test, expect } from './tiktok-fixtures';

test('shows the real empty sidebar and disables draft actions until a draft is selected', async ({ page, account }) => {
  const listing = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/tiktok/creation/sidebar/brief_info_list/',
  );
  await page.goto(`${account.projectPath}/tiktok`);
  const response = await listing;
  expect(response.status()).toBe(200);
  expect((await response.json()).data.ad_group_brief_info_list).toEqual([]);
  await expect(page.getByText('No ad groups yet.', { exact: false })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Select or create a draft', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'New ad group', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: 'Save now', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Share preview', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Delete draft', exact: true })).toBeDisabled();
  await expect(page.getByLabel('Ad name', { exact: true })).toBeHidden();
});

test('selects the correct real draft, loads its image, and persists editor changes after reload', async ({ page, account, ads }) => {
  const first = await ads.create();
  const second = await ads.create();
  const url = `${account.projectPath}/tiktok`;
  await page.goto(url);
  await expect(page.getByRole('heading', { name: 'TikTok ad drafts', exact: true })).toBeVisible();
  for (const draft of [first, second]) {
    await expect(page.getByText(draft.groupName, { exact: true })).toBeVisible();
    await page.getByRole('button').filter({ has: page.getByText(draft.name, { exact: true }) }).click();
    await expect(page).toHaveURL(url);
    await expect(page.getByText(`Draft selected · ${draft.id}`, { exact: true })).toBeVisible();
    await expect(page.getByLabel('Ad name', { exact: true })).toHaveValue(draft.name);
    await expect(page.getByLabel('Ad text', { exact: true })).toHaveValue('Discover our summer collection.');
    await expect(page.getByPlaceholder('Custom CTA label', { exact: true })).toHaveValue('Shop now');
    const preview = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Preview', exact: true }) });
    await expect(preview.getByText('Discover our summer collection.', { exact: true })).toBeVisible();
    await expect(preview.getByRole('button', { name: 'Shop now', exact: true })).toBeVisible();
    const image = preview.getByRole('img', { name: 'preview', exact: true });
    await expect(image).toHaveAttribute('src', draft.imageUrl);
    await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  }

  await page.getByLabel('Ad text', { exact: true }).fill('Download the summer lookbook.');
  await page.getByRole('button', { name: 'Download', exact: true }).click();
  const saving = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/tiktok/creation/ad-drafts/save/' &&
    response.request().postDataJSON().form_data_list[0].ad_text === 'Download the summer lookbook.',
  );
  await page.getByRole('button', { name: 'Save now', exact: true }).click();
  const saved = await saving;
  expect(saved.status()).toBe(200);
  expect((await saved.json()).data['ad-draft-id']).toEqual([second.id]);
  await page.reload();
  await expect(page.getByLabel('Ad name', { exact: true })).toHaveValue(second.name);
  await expect(page.getByLabel('Ad text', { exact: true })).toHaveValue('Download the summer lookbook.');
  await expect(page.getByPlaceholder('Custom CTA label', { exact: true })).toHaveValue('Download');

  await page.getByRole('button').filter({ has: page.getByText(first.name, { exact: true }) }).click();
  await expect(page.getByLabel('Ad name', { exact: true })).toHaveValue(first.name);
  await expect(page.getByLabel('Ad text', { exact: true })).toHaveValue('Discover our summer collection.');
});
