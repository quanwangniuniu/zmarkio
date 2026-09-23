import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { test as base, expect } from '../ads-fixtures';

type Creative = { id: string; slug: string; name: string };
type Ads = {
  create: () => Promise<Creative>;
  track: (creative: Creative) => Creative;
  attachPhoto: (creativeId: string) => Promise<void>;
};

export const test = base.extend<{ ads: Ads }>({
  ads: [async ({ account }, use) => {
    const creatives: Creative[] = [];
    const photos: number[] = [];
    try {
      await use({
        track: (creative) => { creatives.push(creative); return creative; },
        create: async () => {
          const name = `MED-128 ${randomUUID()}`;
          const response = await account.api.post('/api/facebook_meta/adcreatives/', { data: {
            name,
            object_story_spec: { link_data: {
              message: 'Discover our summer collection.', name: 'Summer starts here',
              description: 'Explore the new collection.', link: 'https://example.com/summer',
            } },
          } });
          expect(response.status()).toBe(201);
          const { data } = await response.json();
          const creative = { ...data, name };
          creatives.push(creative);
          return creative;
        },
        attachPhoto: async (creativeId) => {
          const uploaded = await account.api.post('/api/facebook_meta/photos/upload/', { multipart: {
            file: {
              name: `ads-${randomUUID()}.png`, mimeType: 'image/png',
              buffer: fs.readFileSync(path.join(__dirname, '../../profile/fixtures/sample-image.png')),
            },
            caption: 'Summer collection preview',
          } });
          expect(uploaded.status()).toBe(201);
          const { photo } = await uploaded.json();
          photos.push(photo.id);
          const associated = await account.api.post(`/api/facebook_meta/${creativeId}/associate-media/`, {
            data: { photo_ids: [photo.id], video_ids: [] },
          });
          expect(associated.status()).toBe(200);
        },
      });
    } finally {
      const errors: unknown[] = [];
      for (const url of [
        ...creatives.map(({ id }) => `/api/facebook_meta/${id}/`),
        ...photos.map((id) => `/api/facebook_meta/photos/${id}/`),
      ]) {
        try { expect((await account.api.delete(url)).status(), `Clean up ${url}`).toBe(200); }
        catch (error) { errors.push(error); }
      }
      if (errors.length) throw new Error(`Ads record cleanup failed:\n${errors.map(String).join('\n')}`);
    }
  }, { auto: true }],
});

export { expect };
