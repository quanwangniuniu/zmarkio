import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { test as base, expect } from '../ads-fixtures';

type Draft = { id: string; name: string; groupId: string; groupName: string; imageUrl: string };
type Ads = { create: () => Promise<Draft> };

export const test = base.extend<{ ads: Ads }>({
  ads: [async ({ account, page }, use) => {
    const drafts: string[] = [];
    const groups: string[] = [];
    const materials: number[] = [];
    try {
      await use({ create: async () => {
        const name = `MED-128 TikTok ${randomUUID()}`;
        const groupName = `${name} group`;
        const group = await account.api.post('/api/tiktok/creation/ad-group/save/', { data: { name: groupName } });
        expect(group.status(), 'Create an owned TikTok group').toBe(201);
        const groupId = (await group.json()).data['ad-group-id'];
        groups.push(groupId);

        // The existing fixture is JPEG. A unique suffix prevents global MD5 deduplication
        // from returning another user's material; JPEG readers ignore bytes after EOI.
        const uploaded = await account.api.post('/api/tiktok/file/image/ad/upload/', { multipart: {
          name,
          file: {
            name: `${randomUUID()}.jpg`, mimeType: 'image/jpeg',
            buffer: Buffer.concat([
              fs.readFileSync(path.join(__dirname, '../../profile/fixtures/sample-image.png')),
              Buffer.from(randomUUID()),
            ]),
          },
        } });
        expect(uploaded.status(), 'Upload an owned TikTok image').toBe(201);
        const material = await uploaded.json();
        materials.push(material.id);
        const image = { id: material.id, type: 'image', url: material.preview_url, title: name };
        const saved = await account.api.post('/api/tiktok/creation/ad-drafts/save/', { data: {
          adgroup_id: groupId,
          form_data_list: [{
            name, ad_text: 'Discover our summer collection.', call_to_action: 'Shop now',
            creative_type: 'SINGLE_IMAGE', assets: { primaryCreative: image, images: [image] },
          }],
        } });
        expect(saved.status(), 'Create an owned TikTok draft').toBe(200);
        const id = (await saved.json()).data['ad-draft-id'][0];
        drafts.push(id);
        return { id, name, groupId, groupName, imageUrl: material.preview_url };
      } });
    } finally {
      const errors: unknown[] = [];
      // Stop pending editor autosaves before deleting the records they reference.
      try { if (!page.isClosed()) await page.goto('about:blank'); } catch (error) { errors.push(error); }
      const cleanup = [
        ...drafts.map((id) => async () => {
          // Deleting the draft also deletes its public preview snapshots.
          const response = await account.api.post('/api/tiktok/creation/ad-drafts/delete/', { data: { ad_draft_ids: [id] } });
          expect(response.status(), `Delete TikTok draft ${id}`).toBe(200);
          expect((await response.json()).data.deleted_ids).toContain(id);
        }),
        ...groups.map((id) => async () => {
          const response = await account.api.post('/api/tiktok/creation/ad-group/delete/', { data: { ad_group_ids: [id] } });
          expect(response.status(), `Delete TikTok group ${id}`).toBe(200);
        }),
        ...materials.map((id) => async () => {
          expect((await account.api.delete(`/api/tiktok/material/delete/${id}/`)).status(), `Delete TikTok image ${id}`).toBe(200);
        }),
      ];
      for (const remove of cleanup) {
        try { await remove(); } catch (error) { errors.push(error); }
      }
      if (errors.length) throw new Error(`TikTok cleanup failed:\n${errors.map(String).join('\n')}`);
    }
  }, { auto: true }],
});

export { expect };
