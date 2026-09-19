import { randomUUID } from 'crypto';
import { test, expect } from './facebook-meta-fixtures';

test('creates a creative by slug and generates, opens, and revokes a real public preview', async ({ page, browser, account, ads }) => {
  const name = `MED-128 share ${randomUUID()}`;
  await page.goto(account.facebookPath);
  await page.getByRole('button', { name: 'New Ad Creative', exact: true }).click();
  const createDialog = page.getByRole('dialog', { name: 'New Facebook Meta ad creative', exact: true });
  await createDialog.getByLabel('Name', { exact: true }).fill(name);
  const creation = page.waitForResponse((response) =>
    response.url().endsWith('/api/facebook_meta/adcreatives/') && response.request().method() === 'POST',
  );
  await createDialog.getByRole('button', { name: 'Create creative', exact: true }).click();
  const created = await creation;
  expect(created.status()).toBe(201);
  const { data } = await created.json();
  const creative = ads.track({ ...data, name });
  expect(creative.slug).toBeTruthy();
  expect(creative.slug).not.toBe(creative.id);
  await expect(page).toHaveURL(`${account.facebookPath}/${creative.slug}`);
  await ads.attachPhoto(creative.id);
  await page.reload();
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();

  const shareEndpoint = `/api/facebook_meta/${creative.slug}/share-preview/`;
  await page.getByRole('button', { name: 'Share preview', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share Facebook creative preview', exact: true });
  await dialog.getByRole('radio', { name: '14 days', exact: true }).click();
  const generation = page.waitForResponse((response) =>
    response.url().endsWith(shareEndpoint) && response.request().method() === 'POST',
  );
  await dialog.getByRole('button', { name: 'Generate link', exact: true }).click();
  const generated = await generation;
  expect(generated.status()).toBe(201);
  expect(generated.request().postDataJSON()).toEqual({ days: 14 });
  const { link, days_active: daysActive } = await generated.json();
  expect(daysActive).toBe(14);
  await expect(dialog.getByRole('textbox')).toHaveValue(link);
  await expect(dialog.getByRole('button', { name: 'Copy link', exact: true })).toBeEnabled();

  // This context has no login state, so it exercises the public link as a visitor.
  const visitor = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  try {
    const publicPage = await visitor.newPage();
    await publicPage.goto(link);
    await expect(publicPage.getByRole('heading', { name, exact: true })).toBeVisible();
    const image = publicPage.getByRole('img', { name: 'Summer collection preview', exact: true }).first();
    await expect(image).toBeVisible();
    await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);

    await page.reload();
    const share = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Share', exact: true }) });
    await expect(share.getByText(link, { exact: true })).toBeVisible();
    const revocation = page.waitForResponse((response) =>
      response.url().endsWith(shareEndpoint) && response.request().method() === 'DELETE',
    );
    await share.getByRole('button', { name: 'Revoke link', exact: true }).click();
    expect((await revocation).status()).toBe(200);
    await expect(share.getByText('No share link generated.', { exact: false })).toBeVisible();
    const revokedPreview = publicPage.waitForResponse((response) =>
      /\/api\/facebook_meta\/preview\/[^/]+\/public\/$/.test(new URL(response.url()).pathname),
    );
    await publicPage.reload();
    const revoked = await revokedPreview;
    expect(revoked.status()).toBe(404);
    expect((await revoked.json()).code).toBe('PREVIEW_NOT_FOUND');
    await expect(publicPage.getByRole('heading', { name: 'Preview Not Available', exact: true })).toBeVisible();
  } finally {
    await visitor.close();
  }
});
