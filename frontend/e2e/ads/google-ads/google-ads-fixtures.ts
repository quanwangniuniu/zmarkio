import { randomUUID } from 'crypto';
import { test as base, expect } from '../ads-fixtures';

type Ad = { id: number; slug: string; name: string };

export const test = base.extend<{ ads: { create: () => Promise<Ad> } }>({
  ads: async ({ account }, use) => {
    const created: Ad[] = [];
    try {
      await use({ create: async () => {
        const response = await account.api.post('/api/google_ads/ads/', { data: {
          name: `MED-128 ${randomUUID()}`, type: 'RESPONSIVE_SEARCH_AD', status: 'DRAFT',
          display_url: 'example.com', final_urls: ['https://example.com/summer'],
        } });
        expect(response.status(), 'Create an owned Google Ads draft').toBe(201);
        const ad: Ad = await response.json();
        created.push(ad);
        return ad;
      } });
    } finally {
      const errors: unknown[] = [];
      for (const ad of created) {
        try {
          const response = await account.api.delete(`/api/google_ads/ads/${ad.id}/`);
          expect(response.status(), `Delete owned Google Ads draft ${ad.id}`).toBe(204);
        } catch (error) { errors.push(error); }
      }
      if (errors.length) throw new Error(`Google Ads cleanup failed:\n${errors.map(String).join('\n')}`);
    }
  },
});

export { expect };
